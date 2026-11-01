import logging

import pytest

from framework.core.logging_config import ConsoleFormatter, bind_log_context, configure_logging


def test_console_formatter_includes_bound_run_context() -> None:
    record = logging.LogRecord(
        name="framework.agents.data_worker",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Market data ready.",
        args=(),
        exc_info=None,
    )
    formatter = ConsoleFormatter()

    with bind_log_context(
        run_id="0906e239-d9cf-476d-abc8-f35f214eb305",
        symbol="ETH/USDT",
        agent="data_worker",
    ):
        output = formatter.format(record)

    assert "INFO" in output
    assert "run=0906e239" in output
    assert "symbol=ETH/USDT" in output
    assert "agent=data_worker" in output
    assert "Market data ready." in output
    assert "run=" not in formatter.format(record)


def test_console_formatter_uses_record_context_when_not_bound() -> None:
    record = logging.LogRecord(
        name="framework.core.graph",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Run delayed.",
        args=(),
        exc_info=None,
    )
    record.mats_run_id = "run-12345678"
    record.mats_symbol = "BTC/USDT"

    output = ConsoleFormatter().format(record)

    assert "run=run-123" in output
    assert "symbol=BTC/USDT" in output


def test_configure_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="Unsupported log level"):
        configure_logging("VERBOSE")
