import sys
from types import SimpleNamespace

import pytest

from framework.core.state import GraphState


def test_cli_exits_nonzero_if_any_symbol_run_failed(monkeypatch, capsys) -> None:
    import framework.cli as cli

    failed = GraphState(
        run_id="failed-run",
        symbol="BTC/USDT",
        timeframes_to_consider=["1h"],
        status="failed",
        errors=["no viable strategy"],
    )

    class FakeFramework:
        settings = SimpleNamespace(symbols=["BTC/USDT"])

        def __init__(self, enable_coder: bool = False) -> None:
            pass

        def run_many(self, symbols, config, *, max_workers):
            return [failed]

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "TradingFramework", FakeFramework)
    monkeypatch.setattr(sys, "argv", ["mats", "--all"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert '"status": "failed"' in capsys.readouterr().out
