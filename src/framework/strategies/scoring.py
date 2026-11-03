"""How good a strategy's evidence is, in one number on the scale of a t-statistic.

A trade's net return is its net PnL over the notional it put at risk. The t-statistic of
those returns, mean / std x sqrt(trades), asks how sure we can be the average trade makes
money: it grows with consistency and with the number of trades, and it is unitless, so it
compares strategies on any coin, timeframe, or account size.

Two penalties on the same scale keep the number honest:

- search: picking the best of many variants finds lucky ones, so each template loses
  ``search_weight x ln(variants tested)``, a rough stand-in for the deflated Sharpe ratio.
- overfit (unseen history only): when the per-trade edge falls from the history a variant
  was chosen on to the history it never saw, the drop is charged at the unseen sample size.

Pass/fail limits (drawdown, trade count, cost cover) stay hard gates elsewhere, so a large
score can never buy an unacceptable drawdown.
"""

import math
from collections.abc import Sequence

from framework.schemas.models import TradeRecord

# Trade returns never really vary less than this (0.5% of notional); a near-constant series
# would otherwise make the per-trade Sharpe, and so the overfit penalty, explode.
_MIN_STD = 0.005
# A per-trade Sharpe above 2 is not seen in real trading; cap it so it cannot dominate.
_MAX_PER_TRADE_SHARPE = 2.0
_MAX_T = 10.0


def trade_returns(trades: Sequence[TradeRecord]) -> list[float]:
    return [trade.net_pnl / trade.entry_notional for trade in trades if trade.entry_notional > 0]


def per_trade_sharpe(returns: Sequence[float]) -> float:
    """Mean over standard deviation of trade returns; 0 without two trades."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    value = mean / max(math.sqrt(variance), _MIN_STD)
    return max(-_MAX_PER_TRADE_SHARPE, min(_MAX_PER_TRADE_SHARPE, value))


def t_statistic(returns: Sequence[float]) -> float:
    value = per_trade_sharpe(returns) * math.sqrt(len(returns))
    return max(-_MAX_T, min(_MAX_T, value))


def search_penalty(variants_tested: int, weight: float) -> float:
    return weight * math.log(max(variants_tested, 1))


def selection_score(
    trades: Sequence[TradeRecord], variants_tested: int, search_weight: float
) -> float:
    """Score on the history a variant is chosen on (never the unseen part)."""
    return t_statistic(trade_returns(trades)) - search_penalty(variants_tested, search_weight)


def outcome_score(
    in_sample: Sequence[TradeRecord],
    unseen: Sequence[TradeRecord],
    variants_tested: int,
    *,
    overfit_weight: float,
    search_weight: float,
) -> float:
    """Score on history the choice never saw: the measure of whether the choice was good."""
    seen_returns = trade_returns(in_sample)
    unseen_returns = trade_returns(unseen)
    decay = max(0.0, per_trade_sharpe(seen_returns) - per_trade_sharpe(unseen_returns))
    return (
        t_statistic(unseen_returns)
        - overfit_weight * decay * math.sqrt(max(len(unseen_returns), 1))
        - search_penalty(variants_tested, search_weight)
    )
