import math
import random

import numpy as np
import pandas as pd

from framework.tools.technicals import atr, supertrend


def _reference_supertrend(frame: pd.DataFrame, period: int = 10, multiplier: float = 3):
    """The original per-cell pandas implementation, kept to prove the fast one is exact."""
    average_range = atr(frame, period)
    middle = (frame["high"] + frame["low"]) / 2
    upper = middle + multiplier * average_range
    lower = middle - multiplier * average_range
    trend = pd.Series(np.nan, index=frame.index, dtype="float64")
    direction = pd.Series(0, index=frame.index, dtype="int64")
    for index in range(1, len(frame)):
        if pd.isna(upper.iloc[index]) or pd.isna(lower.iloc[index]):
            continue
        prior_direction = direction.iloc[index - 1] or 1
        if frame["close"].iloc[index] > upper.iloc[index - 1]:
            direction.iloc[index] = 1
        elif frame["close"].iloc[index] < lower.iloc[index - 1]:
            direction.iloc[index] = -1
        else:
            direction.iloc[index] = prior_direction
            if direction.iloc[index] > 0:
                lower.iloc[index] = max(lower.iloc[index], lower.iloc[index - 1])
            else:
                upper.iloc[index] = min(upper.iloc[index], upper.iloc[index - 1])
        trend.iloc[index] = lower.iloc[index] if direction.iloc[index] > 0 else upper.iloc[index]
    return pd.DataFrame({"supertrend": trend, "direction": direction})


def _random_frame(seed: int, length: int) -> pd.DataFrame:
    rng = random.Random(seed)
    price = 100.0
    rows = []
    for _ in range(length):
        price = max(1.0, price * (1 + rng.gauss(0, 0.02)))
        if rng.random() < 0.05:
            price *= 1 + rng.choice([-0.12, 0.12])  # occasional gap to force flips
        spread = abs(rng.gauss(0, 0.01)) * price
        rows.append(
            {
                "open": price,
                "high": price + spread,
                "low": max(0.5, price - spread),
                "close": price + rng.uniform(-spread, spread),
                "volume": rng.uniform(1, 100),
            }
        )
    return pd.DataFrame(rows)


def _assert_identical(expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    assert list(actual["direction"]) == list(expected["direction"])
    for left, right in zip(expected["supertrend"], actual["supertrend"], strict=True):
        assert (math.isnan(left) and math.isnan(right)) or left == right


def test_fast_supertrend_matches_reference_on_random_walks() -> None:
    for seed in range(25):
        frame = _random_frame(seed, 400)
        for period, multiplier in ((10, 3), (7, 2), (14, 4.5)):
            _assert_identical(
                _reference_supertrend(frame, period, multiplier),
                supertrend(frame, period, multiplier),
            )


def test_fast_supertrend_matches_reference_on_short_and_flat_series() -> None:
    flat = pd.DataFrame(
        {"open": [5.0] * 30, "high": [5.0] * 30, "low": [5.0] * 30, "close": [5.0] * 30}
    )
    for frame in (_random_frame(99, 3), _random_frame(100, 11), flat):
        _assert_identical(_reference_supertrend(frame), supertrend(frame))
