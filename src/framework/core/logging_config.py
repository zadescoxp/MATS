import contextvars
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import TextIO

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("mats_run_id", default=None)
_symbol: contextvars.ContextVar[str | None] = contextvars.ContextVar("mats_symbol", default=None)
_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar("mats_agent", default=None)

_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[36m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
_RESET = "\033[0m"


class MatsConsoleHandler(logging.StreamHandler):
    pass


class ConsoleFormatter(logging.Formatter):
    def __init__(self, *, color: bool = False):
        super().__init__(datefmt="%H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level = record.levelname.ljust(8)
        if self.color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            if color:
                level = f"{color}{level}{_RESET}"
        context = []
        if run_id := (_run_id.get() or getattr(record, "mats_run_id", None)):
            context.append(f"run={run_id[:8]}")
        if symbol := (_symbol.get() or getattr(record, "mats_symbol", None)):
            context.append(f"symbol={symbol}")
        if agent := (_agent.get() or getattr(record, "mats_agent", None)):
            context.append(f"agent={agent}")
        context_text = f" [{' '.join(context)}]" if context else ""
        message = f"{timestamp} | {level} | {record.name}{context_text} | {record.getMessage()}"
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        return message


@contextmanager
def bind_log_context(
    *, run_id: str | None = None, symbol: str | None = None, agent: str | None = None
) -> Generator[None, None, None]:
    tokens = (
        (_run_id, _run_id.set(run_id)),
        (_symbol, _symbol.set(symbol)),
        (_agent, _agent.set(agent)),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def configure_logging(log_level: str = "INFO", *, stream: TextIO | None = None) -> None:
    level = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        raise ValueError(f"Unsupported log level {log_level!r}.")

    root = logging.getLogger()
    root.setLevel(level)
    handler = next(
        (item for item in root.handlers if isinstance(item, MatsConsoleHandler)),
        None,
    )
    if handler is None:
        handler = MatsConsoleHandler(stream or sys.stderr)
        root.addHandler(handler)
    elif stream is not None:
        handler.setStream(stream)
    handler.setLevel(level)
    handler.setFormatter(
        ConsoleFormatter(color=bool(getattr(handler.stream, "isatty", lambda: False)()))
    )
