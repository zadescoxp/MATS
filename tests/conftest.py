from datetime import UTC, datetime, timedelta

import pytest

from framework.schemas.models import OHLCVBar


@pytest.fixture
def candles() -> list[OHLCVBar]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    values = [100.0 - min(index, 20) * 0.1 for index in range(20)]
    values.extend(98.1 + (index - 20) * 0.5 for index in range(20, 120))
    return [
        OHLCVBar(
            timestamp=start + timedelta(hours=index),
            open=price,
            high=price + 0.3,
            low=price - 0.3,
            close=price,
            volume=10 + index,
        )
        for index, price in enumerate(values)
    ]


@pytest.fixture(autouse=True)
def isolated_candle_cache(tmp_path, monkeypatch):
    """Keep any test that builds a real market-data client away from ./data/candles.db."""
    monkeypatch.setenv("MATS_CANDLE_CACHE_PATH", str(tmp_path / "candles.db"))
