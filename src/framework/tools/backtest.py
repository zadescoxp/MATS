import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from framework.schemas.models import (
    BacktestReport,
    LiveSignal,
    OHLCVBar,
    StrategyDefinition,
    TradeRecord,
)
from framework.strategies.signals import LIVE_SIGNAL_BARS, candle_frame, evaluate_live_signal
from framework.tools.candle_store import timeframe_ms
from framework.tools.cost_model import CostAssumptions, reprice_trade_list

# Each walk-forward segment needs this many bars, or the report has no walk-forward split.
MIN_SEGMENT_BARS = 20


class BacktestTool:
    def __init__(
        self,
        costs: CostAssumptions,
        initial_capital: float = 10_000,
        walk_forward_split: float = 0.7,
        perpetual_costs: CostAssumptions | None = None,
    ):
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0 < walk_forward_split < 1:
            raise ValueError("walk_forward_split must be between 0 and 1")
        self.costs = costs
        # Perpetual contracts have their own fee schedule; None charges them spot costs.
        self.perpetual_costs = perpetual_costs
        self.initial_capital = initial_capital
        self.walk_forward_split = walk_forward_split

    def run(
        self,
        strategy: StrategyDefinition,
        candles: Sequence[OHLCVBar | Mapping[str, Any]],
        *,
        data: dict[str, Sequence[OHLCVBar | Mapping[str, Any]]] | None = None,
        initial_capital: float | None = None,
        signals: list[LiveSignal] | None = None,
    ) -> BacktestReport:
        """Backtest the whole history, plus an in-sample / out-of-sample split of it.

        Both segments reuse the signals of the full pass, so every bar still sees exactly
        the trailing window live trading would; each segment starts flat with fresh capital.
        """
        capital = self._capital(initial_capital)
        candles = [_normalize_candle(candle) for candle in candles]
        if signals is None:
            signals = self.signals(strategy, candles, data=data)
        funding = (data or {}).get("funding")
        report = self.simulate(strategy, candles, signals, capital, funding=funding)
        split = self.split_index(len(candles))
        if split is None:
            return report
        in_sample = self.simulate(
            strategy, candles[:split], signals[:split], capital, funding=funding
        )
        out_of_sample = self.simulate(
            strategy, candles[split:], signals[split:], capital, funding=funding
        )
        return report.model_copy(
            update={
                "walk_forward": {
                    "split": self.walk_forward_split,
                    "in_sample": segment_summary(in_sample, candles[:split]),
                    "out_of_sample": segment_summary(out_of_sample, candles[split:]),
                }
            }
        )

    def split_index(self, bars: int) -> int | None:
        split = int(bars * self.walk_forward_split)
        if split < MIN_SEGMENT_BARS or bars - split < MIN_SEGMENT_BARS:
            return None
        return split

    def signals(
        self,
        strategy: StrategyDefinition,
        candles: Sequence[OHLCVBar | Mapping[str, Any]],
        *,
        data: dict[str, Sequence[OHLCVBar | Mapping[str, Any]]] | None = None,
    ) -> list[LiveSignal]:
        """The live signal each bar would have produced, from its trailing window only."""
        candles = [_normalize_candle(candle) for candle in candles]
        if len(candles) < 2:
            raise ValueError("Backtesting requires at least two candles")
        if any(
            later.timestamp < earlier.timestamp
            for earlier, later in zip(candles, candles[1:], strict=False)
        ):
            raise ValueError("Candles must be time-ordered")
        others: dict[str, Any] = {}
        timestamps: dict[str, list[datetime]] = {}
        primary_length = timedelta(milliseconds=timeframe_ms(strategy.chosen_timeframe) or 0)
        for timeframe, series in (data or {}).items():
            if timeframe == strategy.chosen_timeframe:
                continue
            ordered = sorted(
                (_normalize_candle(candle) for candle in series),
                key=lambda candle: candle.timestamp,
            )
            # A higher-timeframe candle is only known once it has closed, so it is keyed
            # by its close time; same-length series (the pair) and funding by their time.
            length = timeframe_ms(timeframe)  # None for the pair and funding series
            shift = timedelta(milliseconds=length) - primary_length if length else timedelta(0)
            timestamps[timeframe] = [candle.timestamp + shift for candle in ordered]
            # Funding rows are read item by item, so only price series become frames.
            others[timeframe] = (
                ordered if timeframe == "funding" or not ordered else candle_frame(ordered)
            )
        primary = candle_frame(candles)
        signals = [
            LiveSignal(
                action="hold",
                reason="Insufficient candles to evaluate a crossover.",
                strength=0,
                price=candles[0].close,
            )
        ]
        window = LIVE_SIGNAL_BARS
        for index in range(1, len(candles)):
            as_of = candles[index].timestamp
            # Each bar sees what live trading would: the trailing window of every series,
            # with no candle from after this bar.
            available_data: dict[str, Any] = {}
            for timeframe, series in others.items():
                end = bisect_right(timestamps[timeframe], as_of)
                available_data[timeframe] = series[max(0, end - window) : end]
            start = max(0, index + 1 - window)
            available_data[strategy.chosen_timeframe] = primary.iloc[start : index + 1]
            signals.append(evaluate_live_signal(strategy, available_data))
        return signals

    def simulate(
        self,
        strategy: StrategyDefinition,
        candles: Sequence[OHLCVBar | Mapping[str, Any]],
        signals: Sequence[LiveSignal],
        initial_capital: float | None = None,
        *,
        funding: Sequence[OHLCVBar | Mapping[str, Any]] | None = None,
    ) -> BacktestReport:
        """Trade precomputed signals; a signal on bar i acts at the open of bar i + 1.

        Longs buy the coin. Shorts (perpetual strategies with short_entries) keep their
        notional as margin and gain as the price falls. Perpetual positions pay or receive
        each funding settlement that falls while they are open.
        """
        capital = self._capital(initial_capital)
        costs = self._costs(strategy)
        candles = [_normalize_candle(candle) for candle in candles]
        if len(candles) < 2:
            raise ValueError("Backtesting requires at least two candles")
        if len(signals) != len(candles):
            raise ValueError("Every candle needs exactly one signal")
        settlements = _funding_settlements(funding) if strategy.market == "perpetual" else []
        next_settlement = 0
        cash = capital
        position: dict[str, Any] | None = None
        trades: list[TradeRecord] = []
        equity_curve: list[dict[str, Any]] = []

        for index, bar in enumerate(candles):
            # Funding settled since the previous bar is paid by whatever was held over it.
            rate = 0.0
            while (
                next_settlement < len(settlements)
                and settlements[next_settlement][0] <= bar.timestamp
            ):
                rate += settlements[next_settlement][1]
                next_settlement += 1
            if position is not None and rate:
                direction = 1 if position["side"] == "long" else -1
                payment = position["quantity"] * bar.open * rate * direction
                cash -= payment
                position["funding"] += payment

            if position is not None:
                long = position["side"] == "long"
                reason, exit_price = self._exit_in_bar(position, bar)
                opposite = "short" if long else "long"
                if reason is None and index > 0 and signals[index - 1].action == opposite:
                    reason, exit_price = "opposite_signal", bar.open
                bars_held = index - position["entry_index"]
                if (
                    reason is None
                    and strategy.time_stop_bars
                    and bars_held >= strategy.time_stop_bars
                ):
                    reason, exit_price = "time_stop", bar.open
                if reason is None and strategy.exit_rule == "trailing_atr":
                    signal = signals[index]
                    distance = abs(signal.price - signal.stop) if signal.stop is not None else None
                    if long:
                        position["peak_price"] = max(position["peak_price"], bar.high)
                        if distance is not None:
                            position["stop_price"] = max(
                                position["stop_price"], signal.price - distance
                            )
                        if bar.low <= position["stop_price"]:
                            reason, exit_price = "trailing_atr", position["stop_price"]
                    else:
                        position["trough_price"] = min(position["trough_price"], bar.low)
                        if distance is not None:
                            position["stop_price"] = min(
                                position["stop_price"], signal.price + distance
                            )
                        if bar.high >= position["stop_price"]:
                            reason, exit_price = "trailing_atr", position["stop_price"]
                if reason:
                    cash, trade = self._close_position(
                        cash, position, float(exit_price), bar.timestamp, reason, costs
                    )
                    trades.append(trade)
                    position = None

            action = signals[index - 1].action if index > 0 else "hold"
            side = (
                "long"
                if action == "long"
                else "short"
                if action == "short" and strategy.short_entries
                else None
            )
            if position is None and side is not None and cash > 0:
                market_price = bar.open
                max_notional = min(
                    cash / (1 + costs.fee_rate + costs.slippage_percent),
                    cash * strategy.position_fraction,
                )
                max_notional = max(0, max_notional - costs.slippage_fixed)
                quantity = max_notional / market_price
                entry_fee = max_notional * costs.fee_rate
                entry_slippage = max_notional * costs.slippage_percent + costs.slippage_fixed
                if quantity > 0 and cash >= max_notional + entry_fee + entry_slippage:
                    # A long pays for the coin; a short keeps its notional as margin.
                    cash -= entry_fee + entry_slippage + (max_notional if side == "long" else 0)
                    direction = 1 if side == "long" else -1
                    take_profit = (
                        market_price * (1 + direction * strategy.take_profit_pct)
                        if strategy.take_profit_pct
                        else None
                    )
                    position = {
                        "side": side,
                        "entry_time": bar.timestamp,
                        "entry_price": market_price,
                        "entry_notional": max_notional,
                        "entry_fee": entry_fee,
                        "entry_slippage": entry_slippage,
                        "quantity": quantity,
                        "stop_price": market_price * (1 - direction * strategy.stop_loss_pct),
                        "peak_price": market_price,
                        "trough_price": market_price,
                        "entry_index": index,
                        "take_profit_price": take_profit,
                        "funding": 0.0,
                    }
                    reason, exit_price = self._exit_in_bar(position, bar)
                    if reason:
                        cash, trade = self._close_position(
                            cash, position, float(exit_price), bar.timestamp, reason, costs
                        )
                        trades.append(trade)
                        position = None

            equity_curve.append(
                {"timestamp": _iso(bar.timestamp), "equity": cash + _held_value(position, bar)}
            )

        if position is not None:
            last = candles[-1]
            cash, trade = self._close_position(
                cash, position, last.close, last.timestamp, "end_of_data", costs
            )
            trades.append(trade)
            equity_curve[-1]["equity"] = cash

        repriced = reprice_trade_list(trades, costs)
        final_equity = float(equity_curve[-1]["equity"])
        equity_values = np.array([point["equity"] for point in equity_curve], dtype=float)
        drawdown_values = np.concatenate(([capital], equity_values))
        running_max = np.maximum.accumulate(drawdown_values)
        drawdowns = np.divide(
            running_max - drawdown_values,
            running_max,
            out=np.zeros_like(drawdown_values),
            where=running_max != 0,
        )
        returns = (
            pd.Series(drawdown_values).pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        )
        sharpe = 0.0
        if len(returns) > 1 and returns.std(ddof=1) > 0:
            sharpe = float(
                returns.mean() / returns.std(ddof=1) * _annualization(strategy.chosen_timeframe)
            )
        wins = sum(trade.net_pnl > 0 for trade in repriced)
        total_fees = sum(trade.fees for trade in repriced)
        total_slippage = sum(trade.slippage for trade in repriced)
        funding_cost = sum(trade.funding for trade in repriced)
        net_pnl = final_equity - capital

        return BacktestReport(
            initial_capital=capital,
            final_equity=final_equity,
            net_pnl=net_pnl,
            net_return_pct=net_pnl / capital,
            max_drawdown_pct=float(drawdowns.max(initial=0)),
            sharpe_ratio=sharpe,
            win_rate=wins / len(repriced) if repriced else 0.0,
            total_trades=len(repriced),
            total_fees=total_fees,
            total_slippage=total_slippage,
            funding_cost=funding_cost,
            equity_curve=equity_curve,
            trades=repriced,
            metrics={
                "profit_factor": _profit_factor(repriced),
                "average_trade_net_pnl": (
                    float(np.mean([trade.net_pnl for trade in repriced])) if repriced else 0.0
                ),
                "average_trade_gross_pnl": (
                    float(np.mean([trade.gross_pnl for trade in repriced])) if repriced else 0.0
                ),
                "average_trade_cost": (
                    (total_fees + total_slippage) / len(repriced) if repriced else 0.0
                ),
                "cost_edge_ratio": cost_edge_ratio(repriced),
                "buy_and_hold_return": candles[-1].close / candles[0].open - 1,
                "short_trades": sum(trade.side == "short" for trade in repriced),
                "bars": len(candles),
            },
        )

    def _capital(self, initial_capital: float | None) -> float:
        capital = self.initial_capital if initial_capital is None else initial_capital
        if (
            isinstance(capital, bool)
            or not isinstance(capital, int | float)
            or not math.isfinite(capital)
            or capital <= 0
        ):
            raise ValueError("initial_capital must be positive")
        return capital

    def _costs(self, strategy: StrategyDefinition) -> CostAssumptions:
        if strategy.market == "perpetual" and self.perpetual_costs is not None:
            return self.perpetual_costs
        return self.costs

    def _exit_in_bar(
        self, position: dict[str, Any], bar: OHLCVBar
    ) -> tuple[str | None, float | None]:
        stop = position["stop_price"]
        take_profit = position["take_profit_price"]
        if position["side"] == "short":
            if bar.open >= stop:
                return "stop_loss_gap", bar.open
            if take_profit is not None and bar.open <= take_profit:
                return "take_profit_gap", bar.open
            if bar.high >= stop:
                return "stop_loss", stop
            if take_profit is not None and bar.low <= take_profit:
                return "take_profit", take_profit
            return None, None
        if bar.open <= stop:
            return "stop_loss_gap", bar.open
        if take_profit is not None and bar.open >= take_profit:
            return "take_profit_gap", bar.open
        if bar.low <= stop:
            return "stop_loss", stop
        if take_profit is not None and bar.high >= take_profit:
            return "take_profit", take_profit
        return None, None

    def _close_position(
        self,
        cash: float,
        position: dict[str, Any],
        exit_price: float,
        exit_time: datetime,
        reason: str,
        costs: CostAssumptions,
    ) -> tuple[float, TradeRecord]:
        quantity = position["quantity"]
        exit_notional = quantity * exit_price
        exit_fee = exit_notional * costs.fee_rate
        exit_slippage = exit_notional * costs.slippage_percent + costs.slippage_fixed
        if position["side"] == "short":
            gross_pnl = (position["entry_price"] - exit_price) * quantity
            cash += gross_pnl - exit_fee - exit_slippage
        else:
            gross_pnl = (exit_price - position["entry_price"]) * quantity
            cash += exit_notional - exit_fee - exit_slippage
        fees = position["entry_fee"] + exit_fee
        slippage = position["entry_slippage"] + exit_slippage
        trade = TradeRecord(
            entry_time=position["entry_time"],
            exit_time=exit_time,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            quantity=quantity,
            entry_notional=position["entry_notional"],
            exit_notional=exit_notional,
            gross_pnl=gross_pnl,
            fees=fees,
            slippage=slippage,
            net_pnl=gross_pnl - fees - slippage - position["funding"],
            exit_reason=reason,
            side=position["side"],
            funding=position["funding"],
        )
        return cash, trade


def _held_value(position: dict[str, Any] | None, bar: OHLCVBar) -> float:
    """What an open position adds to cash if it were closed at this bar's close."""
    if position is None:
        return 0.0
    if position["side"] == "short":
        return (position["entry_price"] - bar.close) * position["quantity"]
    return position["quantity"] * bar.close


def _funding_settlements(
    funding: Sequence[OHLCVBar | Mapping[str, Any]] | None,
) -> list[tuple[datetime, float]]:
    """(time, rate) of each funding settlement; funding rows store 1 + rate as the close."""
    rows = [_normalize_candle(row) for row in funding or []]
    return sorted((row.timestamp, row.close - 1) for row in rows)


def cost_edge_ratio(trades: Sequence[TradeRecord]) -> float | None:
    """Gross profit earned per unit of fees and slippage paid; None without costs."""
    costs = sum(trade.fees + trade.slippage for trade in trades)
    if not trades or costs <= 0:
        return None
    return sum(trade.gross_pnl for trade in trades) / costs


def segment_summary(report: BacktestReport, candles: Sequence[OHLCVBar]) -> dict[str, Any]:
    return {
        "start": _iso(candles[0].timestamp),
        "end": _iso(candles[-1].timestamp),
        "bars": len(candles),
        "trades": report.total_trades,
        "net_pnl": report.net_pnl,
        "net_return_pct": report.net_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "win_rate": report.win_rate,
        "profit_factor": report.metrics.get("profit_factor"),
        "cost_edge_ratio": report.metrics.get("cost_edge_ratio"),
        "buy_and_hold_return": report.metrics.get("buy_and_hold_return"),
    }


def _annualization(timeframe: str) -> float:
    seconds = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14_400,
        "6h": 21_600,
        "12h": 43_200,
        "1d": 86_400,
        "1w": 604_800,
    }.get(timeframe, 86_400)
    return float(np.sqrt(365.25 * 24 * 60 * 60 / seconds))


def _normalize_candle(candle: OHLCVBar | Mapping[str, Any]) -> OHLCVBar:
    return candle if isinstance(candle, OHLCVBar) else OHLCVBar.model_validate(candle)


def _profit_factor(trades: list[TradeRecord]) -> float | None:
    wins = sum(max(trade.net_pnl, 0) for trade in trades)
    losses = abs(sum(min(trade.net_pnl, 0) for trade in trades))
    return wins / losses if losses else None


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
