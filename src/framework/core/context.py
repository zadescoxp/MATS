from dataclasses import dataclass
from typing import TYPE_CHECKING

from framework.config.settings import Settings
from framework.llm.client import LLMClient
from framework.persistence.database import Database

if TYPE_CHECKING:
    from framework.tools.backtest import BacktestTool
    from framework.tools.execution import ExecutionTools
    from framework.tools.market_data import MarketDataTools


@dataclass
class AgentContext:
    settings: Settings
    database: Database
    llm: LLMClient
    market_data: "MarketDataTools"
    backtester: "BacktestTool"
    execution: "ExecutionTools"
    coder_enabled: bool = False
