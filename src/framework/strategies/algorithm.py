"""One-shot checks for an already-running strategy algorithm."""


def bar_already_evaluated(previous: str | None, current: str | None) -> bool:
    """True when this candle was already passed through the strategy algorithm."""
    return bool(previous and current and previous == current)
