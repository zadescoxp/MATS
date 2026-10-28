from collections.abc import Sequence
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from framework.schemas.models import BacktestReport, OHLCVBar, StrategyDefinition, TradeRecord
from framework.tools.cost_model import CostAssumptions, reprice_trade_list
from framework.tools.technicals import ema


class BacktestTool:
    def __init__(self, costs: CostAssumptions, initial_capital: float = 10_000):
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        self.costs = costs
        self.initial_capital = initial_capital

    def run(
        self,
        strategy: StrategyDefinition,
        candles: Sequence[OHLCVBar],
    ) -> BacktestReport:
        if len(candles) < 2:
            raise ValueError("Backtesting requires at least two candles")
        frame = pd.DataFrame(
            [
                {
                    "timestamp": candle.timestamp,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                }
                for candle in candles
            ]
        )
        if not frame["timestamp"].is_monotonic_increasing:
            raise ValueError("Candles must be time-ordered")

        fast = ema(frame["close"], strategy.fast_period)
        slow = ema(frame["close"], strategy.slow_period)
        bullish_cross = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        bearish_cross = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        cash = self.initial_capital
        position: dict[str, Any] | None = None
        trades: list[TradeRecord] = []
        equity_curve: list[dict[str, Any]] = []

        for index, bar in frame.iterrows():
            if position is not None:
                reason, exit_price = self._exit_in_bar(position, bar)
                if reason is None and index > 0 and bool(bearish_cross.iloc[index - 1]):
                    reason, exit_price = "ema_crossover", float(bar["open"])
                if reason:
                    cash, trade = self._close_position(
                        cash, position, float(exit_price), bar["timestamp"], reason
                    )
                    trades.append(trade)
                    position = None

            if (
                position is None
                and index > 0
                and bool(bullish_cross.iloc[index - 1])
                and cash > 0
            ):
                market_price = float(bar["open"])
                max_notional = min(
                    cash / (1 + self.costs.fee_rate + self.costs.slippage_percent),
                    cash * strategy.position_fraction,
                )
                max_notional = max(0, max_notional - self.costs.slippage_fixed)
                quantity = max_notional / market_price
                entry_fee = max_notional * self.costs.fee_rate
                entry_slippage = max_notional * self.costs.slippage_percent + self.costs.slippage_fixed
                if quantity > 0 and cash >= max_notional + entry_fee + entry_slippage:
                    cash -= max_notional + entry_fee + entry_slippage
                    position = {
                        "entry_time": bar["timestamp"],
                        "entry_price": market_price,
                        "entry_notional": max_notional,
                        "entry_fee": entry_fee,
                        "entry_slippage": entry_slippage,
                        "quantity": quantity,
                        "stop_price": market_price * (1 - strategy.stop_loss_pct),
                        "take_profit_price": (
                            market_price * (1 + strategy.take_profit_pct)
                            if strategy.take_profit_pct
                            else None
                        ),
                    }
                    reason, exit_price = self._exit_in_bar(position, bar)
                    if reason:
                        cash, trade = self._close_position(
                            cash, position, float(exit_price), bar["timestamp"], reason
                        )
                        trades.append(trade)
                        position = None

            marked_equity = cash + (position["quantity"] * float(bar["close"]) if position else 0)
            equity_curve.append(
                {"timestamp": _iso(bar["timestamp"]), "equity": marked_equity}
            )

        if position is not None:
            last = frame.iloc[-1]
            cash, trade = self._close_position(
                cash, position, float(last["close"]), last["timestamp"], "end_of_data"
            )
            trades.append(trade)
            equity_curve[-1]["equity"] = cash

        repriced = reprice_trade_list(trades, self.costs)
        final_equity = float(equity_curve[-1]["equity"])
        equity_values = np.array([point["equity"] for point in equity_curve], dtype=float)
        running_max = np.maximum.accumulate(equity_values)
        drawdowns = np.divide(
            running_max - equity_values,
            running_max,
            out=np.zeros_like(equity_values),
            where=running_max != 0,
        )
        returns = pd.Series(equity_values).pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        sharpe = 0.0
        if len(returns) > 1 and returns.std(ddof=1) > 0:
            sharpe = float(returns.mean() / returns.std(ddof=1) * _annualization(strategy.chosen_timeframe))
        wins = sum(trade.net_pnl > 0 for trade in repriced)
        total_fees = sum(trade.fees for trade in repriced)
        total_slippage = sum(trade.slippage for trade in repriced)
        net_pnl = final_equity - self.initial_capital

        return BacktestReport(
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            net_pnl=net_pnl,
            net_return_pct=net_pnl / self.initial_capital,
            max_drawdown_pct=float(drawdowns.max(initial=0)),
            sharpe_ratio=sharpe,
            win_rate=wins / len(repriced) if repriced else 0.0,
            total_trades=len(repriced),
            total_fees=total_fees,
            total_slippage=total_slippage,
            equity_curve=equity_curve,
            trades=repriced,
            metrics={
                "profit_factor": _profit_factor(repriced),
                "average_trade_net_pnl": (
                    float(np.mean([trade.net_pnl for trade in repriced])) if repriced else 0.0
                ),
                "bars": len(frame),
            },
        )

    def _exit_in_bar(
        self, position: dict[str, Any], bar: pd.Series
    ) -> tuple[str | None, float | None]:
        stop = position["stop_price"]
        take_profit = position["take_profit_price"]
        if float(bar["open"]) <= stop:
            return "stop_loss_gap", float(bar["open"])
        if take_profit is not None and float(bar["open"]) >= take_profit:
            return "take_profit_gap", float(bar["open"])
        if float(bar["low"]) <= stop:
            return "stop_loss", stop
        if take_profit is not None and float(bar["high"]) >= take_profit:
            return "take_profit", take_profit
        return None, None

    def _close_position(
        self,
        cash: float,
        position: dict[str, Any],
        exit_price: float,
        exit_time: datetime,
        reason: str,
    ) -> tuple[float, TradeRecord]:
        quantity = position["quantity"]
        exit_notional = quantity * exit_price
        exit_fee = exit_notional * self.costs.fee_rate
        exit_slippage = exit_notional * self.costs.slippage_percent + self.costs.slippage_fixed
        gross_pnl = (exit_price - position["entry_price"]) * quantity
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
            net_pnl=gross_pnl - fees - slippage,
            exit_reason=reason,
        )
        cash += exit_notional - exit_fee - exit_slippage
        return cash, trade


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


def _profit_factor(trades: list[TradeRecord]) -> float | None:
    wins = sum(max(trade.net_pnl, 0) for trade in trades)
    losses = abs(sum(min(trade.net_pnl, 0) for trade in trades))
    return wins / losses if losses else None


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
