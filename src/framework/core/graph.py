import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any

from langgraph.graph import END, START, StateGraph

from framework.agents.backtest import BacktestNode
from framework.agents.coder import CoderNode
from framework.agents.critique import CritiqueNode
from framework.agents.data_worker import DataWorkerNode
from framework.agents.execution import ExecutionNode
from framework.agents.multi_timeframe_analyst import MultiTimeframeAnalystNode
from framework.agents.risk_assessment import RiskAssessmentNode
from framework.agents.strategy_builder import StrategyBuilderNode
from framework.config.settings import Settings, get_settings
from framework.core.checkpointer import GraphCheckpointer
from framework.core.context import AgentContext
from framework.core.recovery import NodeRunner
from framework.core.state import GraphState
from framework.llm.client import LLMClient
from framework.persistence.database import Database
from framework.tools.backtest import BacktestTool
from framework.tools.cost_model import CostAssumptions
from framework.tools.execution import ExecutionTools
from framework.tools.market_data import MarketDataTools


class TradingFramework:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        database: Database | None = None,
        market_data: MarketDataTools | None = None,
        llm: LLMClient | None = None,
        execution: ExecutionTools | None = None,
        enable_coder: bool = False,
    ):
        self.settings = settings or get_settings()
        self.database = database or Database(self.settings.database_path)
        self.market_data = market_data or MarketDataTools(self.settings)
        self.llm = llm or LLMClient(self.settings)
        self.execution = execution or ExecutionTools(self.settings)
        self.enable_coder = enable_coder
        self._owns_database = database is None

        costs = CostAssumptions(
            maker_fee_rate=self.settings.fee_rate_maker,
            taker_fee_rate=self.settings.fee_rate_taker,
            slippage_percent=self.settings.slippage_percent,
            slippage_fixed=self.settings.slippage_fixed,
        )
        self.context = AgentContext(
            settings=self.settings,
            database=self.database,
            llm=self.llm,
            market_data=self.market_data,
            backtester=BacktestTool(costs, self.settings.initial_capital),
            execution=self.execution,
            coder_enabled=enable_coder,
        )
        self.checkpointer = GraphCheckpointer(self.database)
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        runner = NodeRunner(self.context)
        graph = StateGraph(GraphState)
        graph.add_node("initialize", runner.wrap("initialize", _initialize))
        graph.add_node("data_worker", runner.wrap("data_worker", DataWorkerNode(self.context)))
        graph.add_node(
            "multi_timeframe_analyst",
            runner.wrap("multi_timeframe_analyst", MultiTimeframeAnalystNode(self.context)),
        )
        graph.add_node(
            "strategy_builder", runner.wrap("strategy_builder", StrategyBuilderNode(self.context))
        )
        graph.add_node("backtest", runner.wrap("backtest", BacktestNode(self.context)))
        graph.add_node("critique", runner.wrap("critique", CritiqueNode(self.context)))
        graph.add_node(
            "risk_assessment", runner.wrap("risk_assessment", RiskAssessmentNode(self.context))
        )
        if self.enable_coder:
            graph.add_node("coder", runner.wrap("coder", CoderNode(self.context)))
        graph.add_node("execution", runner.wrap("execution", ExecutionNode(self.context)))

        graph.add_edge(START, "initialize")
        graph.add_conditional_edges("initialize", lambda state: _next(state, "data_worker"))
        graph.add_conditional_edges(
            "data_worker", lambda state: _next(state, "multi_timeframe_analyst")
        )
        graph.add_conditional_edges(
            "multi_timeframe_analyst", lambda state: _next(state, "strategy_builder")
        )
        graph.add_conditional_edges("strategy_builder", lambda state: _next(state, "backtest"))
        graph.add_conditional_edges("backtest", lambda state: _next(state, "critique"))
        graph.add_conditional_edges(
            "critique",
            _route_after_critique,
            {
                "strategy_builder": "strategy_builder",
                "risk_assessment": "risk_assessment",
                END: END,
            },
        )
        graph.add_conditional_edges(
            "risk_assessment",
            lambda state: _next(state, "coder" if self.enable_coder else "execution"),
        )
        if self.enable_coder:
            graph.add_conditional_edges("coder", lambda state: _next(state, "execution"))
        graph.add_edge("execution", END)
        return graph.compile(checkpointer=self.checkpointer.saver)

    def run(self, symbol: str, config: dict[str, Any] | None = None) -> GraphState:
        options = config or {}
        unknown = set(options).difference({"timeframes", "account_equity"})
        if unknown:
            raise ValueError(f"Unsupported run configuration keys: {', '.join(sorted(unknown))}")
        timeframes = options.get("timeframes", self.settings.timeframes)
        if not timeframes:
            raise ValueError("At least one timeframe is required")
        if isinstance(timeframes, str):
            raise ValueError("timeframes must be a list of timeframe strings")
        if not symbol.strip():
            raise ValueError("symbol must not be empty")
        run_id = str(uuid.uuid4())
        state = GraphState(
            run_id=run_id,
            symbol=symbol,
            timeframes_to_consider=list(timeframes),
            account_equity=options.get("account_equity"),
        )
        config_snapshot = self.settings.model_dump(
            mode="json", exclude={"bybit_api_key", "bybit_api_secret"}
        )
        config_snapshot["timeframes_to_consider"] = list(timeframes)
        config_snapshot["account_equity"] = options.get("account_equity")
        self.database.create_run(run_id, symbol, config_snapshot)
        return self._invoke(state, run_id)

    def resume(self, run_id: str | None = None) -> GraphState:
        row = self.database.resumable_run(run_id)
        if row is None:
            raise ValueError(f"No incomplete run found for run_id={run_id!r}")
        raw_state = self.database.latest_checkpoint(row["run_id"])
        if raw_state is None:
            saved_config = json.loads(row["config_snapshot"])
            raw_state = GraphState(
                run_id=row["run_id"],
                symbol=row["symbol"],
                timeframes_to_consider=saved_config.get(
                    "timeframes_to_consider", self.settings.timeframes
                ),
            ).model_dump(mode="json")
        state = GraphState.model_validate(raw_state)
        self.database.update_run_status(state.run_id, "running")
        return self._invoke(state, state.run_id)

    def _invoke(self, state: GraphState, run_id: str) -> GraphState:
        try:
            output = self.graph.invoke(
                state.model_dump(mode="json"),
                {"configurable": {"thread_id": run_id}},
            )
            result = GraphState.model_validate(output)
            self.database.update_run_status(
                run_id, "failed" if result.status == "failed" else "completed"
            )
            return result
        except Exception as exc:
            self.database.log_event(run_id, "graph_failed", str(exc), error=exc)
            self.database.update_run_status(run_id, "failed")
            checkpoint = self.database.latest_checkpoint(run_id)
            failed = GraphState.model_validate(checkpoint) if checkpoint else state
            return failed.model_copy(
                update={
                    "status": "failed",
                    "errors": [*failed.errors, f"graph: {type(exc).__name__}: {exc}"],
                }
            )

    def run_polling(
        self,
        symbols: list[str] | None = None,
        *,
        interval_seconds: float = 60,
        stop_event: threading.Event | None = None,
    ) -> Iterator[GraphState]:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        event = stop_event or threading.Event()
        configured_symbols = symbols or self.settings.symbols
        if not configured_symbols:
            raise ValueError("At least one symbol is required for continuous polling")
        while not event.is_set():
            for symbol in configured_symbols:
                if event.is_set():
                    break
                yield self.run(symbol)
            event.wait(interval_seconds)

    def close(self) -> None:
        self.checkpointer.close()
        if self._owns_database:
            self.database.close()


def _initialize(state: GraphState) -> dict[str, str]:
    return {"status": "running"}


def _next(state: GraphState | dict[str, Any], node: str) -> str:
    current = GraphState.model_validate(state)
    return END if current.status == "failed" else node


def _route_after_critique(state: GraphState | dict[str, Any]) -> str:
    current = GraphState.model_validate(state)
    if current.status == "failed":
        return END
    if current.critique is None:
        return END
    if current.critique.decision == "APPROVE":
        return "risk_assessment"
    return "strategy_builder"
