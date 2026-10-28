import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from framework.core.context import AgentContext
from framework.core.state import GraphState


@dataclass
class AgentResult:
    updates: dict[str, Any]
    tokens_used: int = 0
    cost: float = 0.0


class NodeRunner:
    def __init__(self, context: AgentContext):
        self.context = context

    def wrap(
        self, name: str, function: Callable[[GraphState], dict[str, Any] | AgentResult]
    ) -> Callable[[GraphState | dict[str, Any]], dict[str, Any]]:
        def run(raw_state: GraphState | dict[str, Any]) -> dict[str, Any]:
            state = GraphState.model_validate(raw_state)
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
                    self.context.database.finish_step(step_id, error=str(exc))
                    self.context.database.log_event(
                        state.run_id, "node_failed", f"{name}: {exc}", error=exc
                    )
                    failed = state.model_copy(
                        update={
                            "errors": [*state.errors, f"{name}: {type(exc).__name__}: {exc}"],
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


def _is_transient(error: Exception) -> bool:
    name = type(error).__name__.lower()
    module = type(error).__module__.lower()
    return (
        isinstance(error, TimeoutError | ConnectionError)
        or any(token in name for token in ("timeout", "network", "ratelimit", "connection"))
        or "httpx" in module
    )
