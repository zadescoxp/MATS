from framework.core.diagnostics import diagnose_run


def _run(status: str, **extra):
    return {"status": status, "steps": [], "events": [], "strategies": [], **extra}


def test_failed_step_names_agent_and_gives_a_hint() -> None:
    run = _run(
        "failed",
        steps=[
            {
                "agent_name": "multi_timeframe_analyst",
                "status": "failed",
                "error": "litellm.UnsupportedParamsError: ollama does not support parameters",
            }
        ],
    )
    diagnosis = diagnose_run(run)
    assert diagnosis["kind"] == "failed"
    assert diagnosis["agent"] == "multi_timeframe_analyst"
    assert diagnosis["title"].startswith("Analyst failed")
    assert "tool calling" in diagnosis["hint"]


def test_failure_event_prefix_identifies_agent() -> None:
    run = _run(
        "failed",
        events=[
            {
                "type": "node_failed",
                "message": "critique: 1 validation error for Critique issues",
            }
        ],
    )
    diagnosis = diagnose_run(run)
    assert diagnosis["agent"] == "critique"
    assert "unexpected shape" in diagnosis["title"]


def test_attempt_limit_lists_each_rejected_strategy() -> None:
    run = _run(
        "failed",
        steps=[
            {
                "agent_name": "critique",
                "status": "completed",
                "output": {"errors": ["Strategy attempt limit reached (3 total attempts)."]},
            }
        ],
        strategies=[
            {
                "strategy": {"template_name": "mean_reversion", "chosen_timeframe": "1h"},
                "decision": "REJECT",
                "issues": ["Net PnL after fees and slippage must exceed 0.00."],
            }
        ],
    )
    diagnosis = diagnose_run(run)
    assert diagnosis["kind"] == "rejected"
    assert diagnosis["title"] == "No strategy approved after 1 attempt"
    assert "mean_reversion on 1h: Net PnL" in diagnosis["detail"]
    assert "not an error" in diagnosis["hint"]


def test_rejected_status_is_a_notice_not_a_failure() -> None:
    run = _run(
        "rejected",
        strategies=[
            {"strategy": {"template_name": "breakout_confirmed"}, "decision": "REJECT"},
            {"strategy": {"template_name": "bollinger_squeeze"}, "decision": "REJECT"},
        ],
    )
    diagnosis = diagnose_run(run)
    assert diagnosis["kind"] == "rejected"
    assert diagnosis["title"] == "No strategy approved after 2 attempts"


def test_paused_watcher_explains_the_safety_switch() -> None:
    watcher = {"status": "paused", "error": "Paused: the kill switch is on."}
    diagnosis = diagnose_run(_run("completed"), watcher)
    assert diagnosis["kind"] == "paused"
    assert "kill switch" in diagnosis["detail"]


def test_completed_hold_mentions_kill_switch_simulation() -> None:
    run = _run(
        "completed",
        events=[
            {"type": "execution_result", "message": "No order placed: no entry condition."},
            {"type": "execution_started", "message": "dry_run=False, kill_switch=True."},
        ],
    )
    diagnosis = diagnose_run(run)
    assert diagnosis["kind"] == "no_trade"
    assert "kill switch was on" in diagnosis["detail"]


def test_running_run_has_no_diagnosis() -> None:
    assert diagnose_run(_run("running")) is None
