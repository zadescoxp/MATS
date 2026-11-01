import json
import logging
import math
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

from langgraph.graph import END, START, StateGraph

from framework.agents.backtest import BacktestNode
from framework.agents.coder import CoderNode
from framework.agents.critique import CritiqueNode
from framework.agents.data_worker import DataWorkerNode
from framework.agents.execution import ExecutionNode
from framework.agents.multi_timeframe_analyst import MultiTimeframeAnalystNode
from framework.agents.position_monitor import PositionMonitor
from framework.agents.regime import RegimeNode
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

logger = logging.getLogger(__name__)


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
        stop_event: Event | None = None,
    ):
        self.settings = settings or get_settings()
        self.database = database or Database(self.settings.database_path)
        self.market_data = market_data or MarketDataTools(self.settings)
        self.llm = llm or LLMClient(self.settings)
        self.execution = execution or ExecutionTools(self.settings)
        self.enable_coder = enable_coder
        self.stop_event = stop_event
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
        self.position_monitor = PositionMonitor(self.context)
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        runner = NodeRunner(self.context, self.stop_event)
        graph = StateGraph(GraphState)
        graph.add_node("initialize", runner.wrap("initialize", _initialize))
        graph.add_node("data_worker", runner.wrap("data_worker", DataWorkerNode(self.context)))
        graph.add_node(
            "multi_timeframe_analyst",
            runner.wrap("multi_timeframe_analyst", MultiTimeframeAnalystNode(self.context)),
        )
        graph.add_node("regime", runner.wrap("regime", RegimeNode(self.context)))
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
        graph.add_conditional_edges("multi_timeframe_analyst", lambda state: _next(state, "regime"))
        graph.add_conditional_edges("regime", lambda state: _next(state, "strategy_builder"))
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

    def run(
        self, symbol: str, config: dict[str, Any] | None = None, *, run_id: str | None = None
    ) -> GraphState:
        options = config or {}
        unknown = set(options).difference({"timeframes", "account_equity", "dashboard_batch_id"})
        if unknown:
            raise ValueError(f"Unsupported run configuration keys: {', '.join(sorted(unknown))}")
        dashboard_batch_id = options.get("dashboard_batch_id")
        if dashboard_batch_id is not None and (
            not isinstance(dashboard_batch_id, str) or not dashboard_batch_id.strip()
        ):
            raise ValueError("dashboard_batch_id must be a non-empty string")
        timeframes = options.get("timeframes", self.settings.timeframes)
        if not timeframes:
            raise ValueError("At least one timeframe is required")
        if isinstance(timeframes, str):
            raise ValueError("timeframes must be a list of timeframe strings")
        if not symbol.strip():
            raise ValueError("symbol must not be empty")
        run_id = run_id or str(uuid.uuid4())
        state = GraphState(
            run_id=run_id,
            symbol=symbol,
            timeframes_to_consider=list(timeframes),
            account_equity=options.get("account_equity"),
        )
        config_snapshot = self.settings.model_dump(
            mode="json",
            exclude={
                "bybit_api_key",
                "bybit_api_secret",
                "llm_api_key",
                "model_routes",
            },
        )
        config_snapshot["model_routes"] = {
            role: {
                "provider": route.provider,
                "model": route.model,
                "base_url": route.base_url,
                "supports_tools": route.supports_tools,
            }
            for role, route in self.settings.model_routes.items()
        }
        config_snapshot["timeframes_to_consider"] = list(timeframes)
        config_snapshot["account_equity"] = options.get("account_equity")
        if dashboard_batch_id is not None:
            config_snapshot["dashboard_batch_id"] = dashboard_batch_id
        self.database.create_run(run_id, symbol, config_snapshot)
        return self._invoke(state, run_id)

    def resume(
        self, run_id: str | None = None, *, account_equity: float | None = None
    ) -> GraphState:
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
        if account_equity is not None:
            if (
                isinstance(account_equity, bool)
                or not math.isfinite(account_equity)
                or account_equity <= 0
            ):
                raise ValueError("account_equity must be a positive finite number")
            state = state.model_copy(update={"account_equity": account_equity})
        self.database.update_run_status(state.run_id, "running")
        return self._invoke(state, state.run_id)

    def monitor_open_positions(self) -> list[dict[str, Any]]:
        return self.position_monitor()

    def reset_stopped_strategies(self, symbols: list[str]) -> dict[str, int]:
        return {symbol: self.database.clear_strategy_stops(symbol) for symbol in symbols}

    def run_many(
        self,
        symbols: list[str] | None = None,
        config: dict[str, Any] | None = None,
        *,
        max_workers: int | None = None,
    ) -> list[GraphState]:
        selected_symbols = self.settings.symbols if symbols is None else symbols
        if not selected_symbols:
            raise ValueError("At least one symbol is required")
        if any(not symbol.strip() for symbol in selected_symbols):
            raise ValueError("Symbols must not be empty")
        if len(set(selected_symbols)) != len(selected_symbols):
            raise ValueError("Symbols must be unique")
        worker_limit = self.settings.max_concurrent_symbols if max_workers is None else max_workers
        if worker_limit < 1:
            raise ValueError("max_workers must be positive")
        if not (self.settings.dry_run or self.settings.kill_switch):
            if worker_limit > 1 and len(selected_symbols) > 1:
                logger.warning(
                    "Live testnet multi-symbol runs are processed sequentially to share "
                    "the exchange's updated available balance between symbol runs."
                )
            return [self._run_isolated_symbol(symbol, config) for symbol in selected_symbols]
        with ThreadPoolExecutor(
            max_workers=min(worker_limit, len(selected_symbols)),
            thread_name_prefix="mats-symbol",
        ) as executor:
            futures = [
                executor.submit(self._run_isolated_symbol, symbol, config)
                for symbol in selected_symbols
            ]
            return [future.result() for future in futures]

    def _run_isolated_symbol(self, symbol: str, config: dict[str, Any] | None) -> GraphState:
        worker = TradingFramework(settings=self.settings, enable_coder=self.enable_coder)
        try:
            return worker.run(symbol, config)
        finally:
            worker.close()

    def _invoke(self, state: GraphState, run_id: str) -> GraphState:
        try:
            output = self.graph.invoke(
                state.model_dump(mode="json"),
                {"configurable": {"thread_id": run_id}},
            )
            result = GraphState.model_validate(output)
            self.database.update_run_status(
                run_id,
                result.status if result.status in {"failed", "stopped"} else "completed",
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
        max_workers: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> Iterator[GraphState]:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        event = stop_event or threading.Event()
        configured_symbols = self.settings.symbols if symbols is None else symbols
        if not configured_symbols:
            raise ValueError("At least one symbol is required for continuous polling")
        while not event.is_set():
            self.monitor_open_positions()
            for result in self.run_many(configured_symbols, max_workers=max_workers):
                if event.is_set():
                    break
                yield result
            event.wait(interval_seconds)

    def close(self) -> None:
        self.checkpointer.close()
        if self._owns_database:
            self.database.close()


def _initialize(state: GraphState) -> dict[str, str]:
    return {"status": "running"}


def _next(state: GraphState | dict[str, Any], node: str) -> str:
    current = GraphState.model_validate(state)
    return END if current.status in {"failed", "stopped"} else node


def _route_after_critique(state: GraphState | dict[str, Any]) -> str:
    current = GraphState.model_validate(state)
    if current.status in {"failed", "stopped"}:
        return END
    if current.critique is None:
        return END
    if current.critique.decision == "APPROVE":
        return "risk_assessment"
    return "strategy_builder"
