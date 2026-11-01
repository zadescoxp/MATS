"""Plain-language explanations of why a run failed, stopped, or has not traded."""

from typing import Any

_AGENT_LABELS = {
    "initialize": "Startup",
    "data_worker": "Data Worker",
    "multi_timeframe_analyst": "Analyst",
    "regime": "Regime",
    "strategy_builder": "Strategy Builder",
    "backtest": "Backtest",
    "critique": "Critique",
    "risk_assessment": "Risk",
    "coder": "Coder",
    "execution": "Execution",
    "graph": "Run",
}
_FAILURE_EVENTS = {
    "node_failed",
    "graph_failed",
    "dashboard_run_failed",
    "dashboard_resume_failed",
    "entry_watcher_failed",
}
_RETIRE_EVENTS = {"algorithm_retired", "edge_fade_exit", "manual_flatten", "strategy_stopped"}

# (substrings to look for, title, what the operator should do)
_KNOWN_FAILURES: list[tuple[tuple[str, ...], str, str]] = [
    (
        ("attempt limit reached",),
        "Critique rejected every strategy attempt",
        "No candidate cleared the net-of-cost thresholds and regime checks. Try other "
        "timeframes or symbols, or review the critique issues listed here.",
    ),
    (
        ("unsupportedparamserror", "does not support parameters"),
        "The selected model rejected a request parameter",
        "MATS now drops parameters a provider does not support. Resume the run; if it "
        "fails again, choose a model with tool calling in Settings.",
    ),
    (
        ("authenticationerror", "invalid api key", "incorrect api key", "401", "unauthorized"),
        "Credentials were rejected",
        "Check the model API key, or the Bybit testnet key and secret, in Settings.",
    ),
    (
        ("ratelimit", "rate limit", "too many requests", "429"),
        "A provider rate limit was hit",
        "Wait a minute and resume the run, or run fewer symbols at once.",
    ),
    (
        ("could not resolve", "could not reach", "timed out", "timeout", "connection"),
        "A network request failed",
        "Check the internet connection and resume the run.",
    ),
    (
        ("credentials are required",),
        "Bybit testnet credentials are missing",
        "Add a Bybit testnet API key and secret in Settings, then resume.",
    ),
    (
        ("no available", "balance for testnet risk sizing", "balance to allocate"),
        "No testnet balance to trade with",
        "Fund the Bybit testnet account in the quote currency, or set account capital "
        "when starting the run.",
    ),
    (
        ("no untried library/timeframe candidate",),
        "No strategy survived the measured backtests",
        "No template had enough history and closed trades after costs. Try more or "
        "lower timeframes, or another symbol; strategy_diagnostics shows the coverage.",
    ),
    (
        ("expected a 'submit_", "tool call; received none", "conflicting"),
        "The model did not return the required structured answer",
        "Resume the run. If it repeats, select a stronger tool-calling model.",
    ),
    (
        ("validation error", "not json serializable", "has no attribute", "input should be"),
        "An agent produced data in an unexpected shape",
        "This is an internal bug, not a market problem. Resume the run; if it repeats, "
        "report the message shown here.",
    ),
    (
        ("requires ohlcv", "insufficient", "not enough"),
        "Not enough market data",
        "The exchange returned too few candles. Pick a longer-history timeframe or symbol.",
    ),
]


def diagnose_run(run: dict[str, Any], watcher: dict[str, Any] | None = None) -> dict | None:
    """Summarize a run's outcome for the console, or None while it is simply running."""
    status = run.get("status")
    events = run.get("events") or []
    if status == "failed":
        return _failure(run, events)
    if status == "stopped":
        retired = _latest(events, _RETIRE_EVENTS)
        if retired:
            return _result(
                "retired",
                "Strategy retired",
                retired["message"],
                "The strategy will not re-enter. Start a new run to research a fresh one.",
            )
        return _result("stopped", "Run stopped", "The run was stopped by the operator.", None)
    if watcher:
        return _watcher_outcome(watcher, events)
    if status != "completed":
        return None
    not_started = _latest(events, {"algorithm_not_started"})
    if not_started:
        return _result("dry_run", "Approved in dry-run only", not_started["message"], None)
    execution = _latest(events, {"execution_result"})
    if execution and execution["message"].startswith("No order placed"):
        started = _latest(events, {"execution_started"})
        detail = execution["message"]
        if started and "kill_switch=True" in started["message"]:
            detail += " The kill switch was on, so this run could only simulate orders."
        return _result("no_trade", "Completed without a trade", detail, None)
    return None


def _failure(run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    agent, message = None, None
    failed_steps = [step for step in run.get("steps") or [] if step.get("status") == "failed"]
    if failed_steps and failed_steps[-1].get("error"):
        agent = failed_steps[-1].get("agent_name")
        message = str(failed_steps[-1]["error"])
    event = _latest(events, _FAILURE_EVENTS)
    if message is None and event:
        message = event["message"]
        prefix, separator, rest = message.partition(": ")
        if separator and prefix in _AGENT_LABELS:
            agent, message = prefix, rest
    if message is None:
        message = next(
            (
                error
                for step in reversed(run.get("steps") or [])
                for error in reversed(((step.get("output") or {}).get("errors")) or [])
                if isinstance(error, str)
            ),
            "The run failed without recording an error message.",
        )
    title, hint = _classify(message)
    if "attempt limit reached" in message.lower():
        message = f"{message} {_critique_issues(run)}".strip()
    label = _AGENT_LABELS.get(agent or "", agent)
    return _result(
        "failed",
        f"{label} failed: {title}" if label else title,
        message.strip()[:600],
        hint,
        agent=agent,
    )


def _watcher_outcome(watcher: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    status = watcher.get("status")
    signal = watcher.get("signal") or {}
    if status == "paused":
        return _result(
            "paused",
            "Approved strategy is paused",
            watcher.get("error") or "A safety switch is on.",
            "Turn off the kill switch and dry-run to let it trade on testnet.",
        )
    if status == "in_position":
        check = _latest(events, {"position_check", "entry_watcher_triggered"})
        return _result(
            "in_position",
            "Trading: position open",
            check["message"] if check else "The algorithm is managing an open position.",
            None,
        )
    if status == "watching":
        check = _latest(events, {"algorithm_check"})
        detail = check["message"] if check else signal.get("reason") or "Waiting for a signal."
        if watcher.get("error"):
            detail = f"Last check failed: {watcher['error']}"
        return _result("waiting", "Approved, waiting for an entry signal", detail, None)
    if status == "failed":
        title, hint = _classify(str(watcher.get("error") or ""))
        return _result(
            "failed",
            f"Strategy algorithm failed: {title}",
            watcher.get("error") or "",
            hint,
        )
    retired = _latest(events, _RETIRE_EVENTS)
    if retired:
        return _result("retired", "Strategy retired", retired["message"], None)
    return _result("stopped", "Strategy algorithm stopped", "No live checks are running.", None)


def _critique_issues(run: dict[str, Any]) -> str:
    lines = []
    for attempt in run.get("strategies") or []:
        definition = attempt.get("strategy") or {}
        name = definition.get("template_name") or definition.get("name") or "strategy"
        timeframe = definition.get("chosen_timeframe")
        issues = attempt.get("issues") or []
        if attempt.get("decision") == "REJECT" and issues:
            label = f"{name} on {timeframe}" if timeframe else name
            lines.append(f"{label}: {'; '.join(str(issue) for issue in issues[:3])}")
    return " | ".join(lines)


def _classify(message: str) -> tuple[str, str]:
    lower = message.lower()
    for needles, title, hint in _KNOWN_FAILURES:
        if any(needle in lower for needle in needles):
            return title, hint
    return "Unexpected error", "Resume the run. If it repeats, the message above names the cause."


def _latest(events: list[dict[str, Any]], types: set[str]) -> dict[str, Any] | None:
    # Snapshot events are ordered newest first.
    return next((event for event in events if event.get("type") in types), None)


def _result(
    kind: str, title: str, detail: str, hint: str | None, *, agent: str | None = None
) -> dict[str, Any]:
    return {"kind": kind, "title": title, "detail": detail, "hint": hint, "agent": agent}
