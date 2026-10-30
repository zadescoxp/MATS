import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from framework.core.context import AgentContext
from framework.core.state import GraphState


@dataclass
class AgentResult:
    updates: dict[str, Any]
    tokens_used: int = 0
    cost: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class NodeRunner:
    def __init__(self, context: AgentContext, stop_event: Event | None = None):
        self.context = context
        self.stop_event = stop_event

    def wrap(
        self, name: str, function: Callable[[GraphState], dict[str, Any] | AgentResult]
    ) -> Callable[[GraphState | dict[str, Any]], dict[str, Any]]:
        def run(raw_state: GraphState | dict[str, Any]) -> dict[str, Any]:
            state = GraphState.model_validate(raw_state)
            if self.stop_event and self.stop_event.is_set():
                self.context.database.log_event(
                    state.run_id,
                    "run_stopped",
                    f"Run stopped before the {name} agent started.",
                )
                return {"status": "stopped"}
            step_id = f"{state.run_id}:{name}:r{state.revision_count}"
            start_state = state.model_dump(mode="json")
            self.context.database.checkpoint(state.run_id, name, "before", start_state)

            if self.context.database.step_completed(step_id):
                updates = self.context.database.step_output(step_id)
                if updates is None:
                    raise RuntimeError(f"Completed step {step_id!r} has no saved output")
                return self._checkpoint_after(name, state, updates)

            started = self.context.database.start_step(step_id, state.run_id, name, start_state)
            if not started:
                updates = self.context.database.step_output(step_id)
                if updates is not None:
                    return self._checkpoint_after(name, state, updates)
                raise RuntimeError(f"Could not acquire resumable step {step_id!r}")

            attempts = self.context.settings.retry_attempts
            for attempt in range(attempts):
                try:
                    result = function(state)
                    agent_result = (
                        result if isinstance(result, AgentResult) else AgentResult(result)
                    )
                    updated = GraphState.model_validate(
                        {**state.model_dump(mode="python"), **agent_result.updates}
                    )
                    self.context.database.finish_step(
                        step_id,
                        output=agent_result.updates,
                        tool_calls=agent_result.tool_calls,
                        tokens_used=agent_result.tokens_used,
                        cost=agent_result.cost,
                    )
                    self.context.database.checkpoint(
                        state.run_id, name, "after", updated.model_dump(mode="json")
                    )
                    return agent_result.updates
                except Exception as exc:
                    transient = _is_transient(exc)
                    if transient and attempt + 1 < attempts:
                        time.sleep(min(2**attempt, 8))
                        continue
                    detail = describe_error(exc)
                    self.context.database.finish_step(step_id, error=detail)
                    self.context.database.log_event(
                        state.run_id, "node_failed", f"{name}: {detail}", error=exc
                    )
                    failed = state.model_copy(
                        update={
                            "errors": [*state.errors, f"{name}: {type(exc).__name__}: {detail}"],
                            "status": "failed",
                        }
                    )
                    failed_dump = failed.model_dump(mode="json")
                    self.context.database.checkpoint(state.run_id, name, "failed", failed_dump)
                    self.context.database.checkpoint(state.run_id, name, "after", failed_dump)
                    return {"errors": failed_dump["errors"], "status": "failed"}
            raise RuntimeError(f"Node {name!r} exited its retry loop unexpectedly")

        return run

    def _checkpoint_after(
        self, name: str, state: GraphState, updates: dict[str, Any]
    ) -> dict[str, Any]:
        updated = GraphState.model_validate({**state.model_dump(mode="python"), **updates})
        self.context.database.checkpoint(
            state.run_id, name, "after", updated.model_dump(mode="json")
        )
        return updates


def describe_error(exc: BaseException) -> str:
    texts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(texts) < 5:
        seen.add(id(current))
        text = " ".join(str(current).split())
        if text and text not in texts:
            texts.append(text)
        current = current.__cause__ or current.__context__
    joined = " ".join(texts)
    if (
        "Failed to resolve" in joined
        or "nodename nor servname" in joined
        or "Name or service not known" in joined
    ):
        host = _host_from(joined) or "the exchange host"
        return (
            f"Could not resolve {host}. DNS lookup failed, "
            "so the request never reached the exchange."
        )
    summary = texts[0] if texts else type(exc).__name__
    extra = next((text for text in texts[1:] if text not in summary), "")
    if extra:
        summary = f"{summary} — {extra}"
    return summary[:500]


def _host_from(text: str) -> str | None:
    for marker in ("Failed to resolve '", "host='"):
        if marker in text:
            return text.split(marker, 1)[1].split("'", 1)[0]
    return None


def _is_transient(error: Exception) -> bool:
    name = type(error).__name__.lower()
    module = type(error).__module__.lower()
    return (
        isinstance(error, TimeoutError | ConnectionError)
        or any(token in name for token in ("timeout", "network", "ratelimit", "connection"))
        or "httpx" in module
    )
