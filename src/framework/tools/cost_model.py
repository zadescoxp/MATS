from typing import Literal

from pydantic import BaseModel, Field

from framework.schemas.models import TradeRecord


class CostAssumptions(BaseModel):
    maker_fee_rate: float = Field(default=0.001, ge=0, lt=1)
    taker_fee_rate: float = Field(default=0.001, ge=0, lt=1)
    slippage_percent: float = Field(default=0.0005, ge=0, lt=1)
    slippage_fixed: float = Field(default=0, ge=0)
    liquidity: Literal["maker", "taker"] = "taker"

    @property
    def fee_rate(self) -> float:
        if self.liquidity == "maker":
            return self.maker_fee_rate
        if self.liquidity == "taker":
            return self.taker_fee_rate
        raise ValueError("liquidity must be either 'maker' or 'taker'")


def reprice_trade_list(
    trades: list[TradeRecord], assumptions: CostAssumptions
) -> list[TradeRecord]:
    repriced: list[TradeRecord] = []
    for trade in trades:
        fees = (trade.entry_notional + trade.exit_notional) * assumptions.fee_rate
        slippage = (
            trade.entry_notional + trade.exit_notional
        ) * assumptions.slippage_percent + 2 * assumptions.slippage_fixed
        repriced.append(
            trade.model_copy(
                update={
                    "fees": fees,
                    "slippage": slippage,
                    "net_pnl": trade.gross_pnl - fees - slippage,
                }
            )
        )
    return repriced
