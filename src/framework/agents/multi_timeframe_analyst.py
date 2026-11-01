import json
import math
from typing import Any

from framework.agents.common import invoke_structured, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import AnalystSubmission, MultiTimeframeFindings, OHLCVBar
from framework.tools.technicals import compute_multi_timeframe


class MultiTimeframeAnalystNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        if not state.raw_data:
            raise ValueError("Analyst requires OHLCV data for at least one timeframe")
        snapshots = compute_multi_timeframe(state.raw_data)
        snapshot_payload = {
            timeframe: snapshot.model_dump(mode="json") for timeframe, snapshot in snapshots.items()
        }
        timeframe_order = sorted(state.raw_data, key=_timeframe_seconds)
        highest, lowest = timeframe_order[-1], timeframe_order[0]
        messages = [
            {
                "role": "system",
                "content": (
                    "Analyze all supplied timeframes together. Assess alignment, divergence, "
                    "regime, momentum, volatility, and key levels. Do not propose a strategy. "
                    "When external context is present, let long-horizon items set the bias and "
                    "use short-horizon items only as timing risk. "
                    "Include a concise evidence-based rationale when possible. Call "
                    "submit_findings exactly once with the complete structured finding."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "higher_timeframe": highest,
                        "lower_timeframe": lowest,
                        "technical_snapshots": snapshot_payload,
                        "market_context": prompt_market_context(
                            state.market_context, state.raw_data
                        ),
                    },
                    separators=(",", ":"),
                ),
            },
        ]
        # The model only judges; timeframes and indicator snapshots are already known here.
        tool = submit_tool(
            "submit_findings",
            AnalystSubmission,
            (
                f"Submit one combined finding: bias on {highest}, entry timing on {lowest}, "
                "plus regime, momentum, volatility, key price levels, and divergences."
            ),
        )
        submission, response = invoke_structured(
            self.context.llm,
            messages,
            tool,
            AnalystSubmission,
            self.context.settings.model_for("analyst"),
        )
        finding = MultiTimeframeFindings(
            **submission.model_dump(),
            higher_timeframe=highest,
            lower_timeframe=lowest,
            technical_snapshots=snapshot_payload,
        )
        history = [
            *state.messages,
            {"role": "assistant", "content": response.content, "tool": "submit_findings"},
        ]
        return result_with_response({"analyst_findings": [finding]}, response, history)


def _timeframe_seconds(timeframe: str) -> int:
    number = int(timeframe[:-1])
    unit = timeframe[-1]
    factors = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
    if unit not in factors:
        raise ValueError(f"Unsupported timeframe {timeframe!r}")
    return number * factors[unit]


# Raw series stay in state for backtests; prompts only get summaries so the request fits
# small local context windows and does not pay for tens of thousands of candle tokens.
_PROMPT_EXCLUDED_KEYS = {"pair_candles", "funding_history", "recent_trades", "orderbook"}


def prompt_market_context(
    market_context: dict[str, Any], raw_data: dict[str, list[OHLCVBar]]
) -> dict[str, Any]:
    context = {
        key: value for key, value in market_context.items() if key not in _PROMPT_EXCLUDED_KEYS
    }
    external = context.get("external")
    if isinstance(external, dict):
        # "items" repeats everything already grouped by horizon.
        context["external"] = {key: value for key, value in external.items() if key != "items"}
    pair_candles = market_context.get("pair_candles")
    if isinstance(pair_candles, dict) and pair_candles:
        context["pair_summary"] = {
            timeframe: _pair_summary(raw_data.get(timeframe) or [], candles)
            for timeframe, candles in pair_candles.items()
            if isinstance(candles, list) and candles
        }
    funding = market_context.get("funding_history")
    if isinstance(funding, list) and funding:
        context["recent_funding"] = funding[-5:]
    orderbook = market_context.get("orderbook")
    if isinstance(orderbook, dict):
        context["orderbook_top"] = {
            "bids": (orderbook.get("bids") or [])[:5],
            "asks": (orderbook.get("asks") or [])[:5],
        }
    return context


def _pair_summary(primary: list[Any], pair: list[Any]) -> dict[str, Any]:
    pair_closes = [_close(bar) for bar in pair]
    primary_closes = [_close(bar) for bar in primary]
    summary: dict[str, Any] = {
        "last_close": pair_closes[-1],
        "return_20_bars": _return(pair_closes, 20),
        "return_100_bars": _return(pair_closes, 100),
    }
    window = min(len(pair_closes), len(primary_closes), 101)
    if window >= 21:
        primary_returns = _returns(primary_closes[-window:])
        pair_returns = _returns(pair_closes[-window:])
        summary["return_correlation"] = _correlation(primary_returns, pair_returns)
        summary["relative_strength_100_bars"] = _round(
            _return(primary_closes, window - 1) - _return(pair_closes, window - 1)
        )
    return summary


def _close(bar: Any) -> float:
    return float(bar["close"] if isinstance(bar, dict) else bar.close)


def _returns(closes: list[float]) -> list[float]:
    return [
        current / previous - 1
        for previous, current in zip(closes, closes[1:], strict=False)
        if previous
    ]


def _return(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars or not closes[-bars - 1]:
        return None
    return _round(closes[-1] / closes[-bars - 1] - 1)


def _correlation(left: list[float], right: list[float]) -> float | None:
    count = min(len(left), len(right))
    if count < 2:
        return None
    left, right = left[-count:], right[-count:]
    left_mean, right_mean = sum(left) / count, sum(right) / count
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return _round(covariance / math.sqrt(left_var * right_var))


def _round(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, 4)
