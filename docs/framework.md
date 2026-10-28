# Framework state, tools, and extension guide

## Graph

The production graph is:

```text
initialize -> DataWorkerNode -> MultiTimeframeAnalystNode -> StrategyBuilderNode
           -> BacktestNode -> CritiqueNode
Critique REJECT -> StrategyBuilderNode (bounded revision loop)
Critique APPROVE -> RiskAssessmentNode -> [optional CoderNode] -> ExecutionNode
```

There is exactly one primary analyst. It receives every configured timeframe in one
call, computes the technical indicator suite, and emits one combined finding object.
Non-price context can be included in the finding without introducing a second generic
analyst. Polling is available through the CLI or `TradingFramework.run_polling`.
`TradingFramework.run_many` gives each symbol its own graph/checkpointer and runs
symbols concurrently, preserving input order in the returned states. It refuses
multi-symbol execution when both dry-run and the global kill switch are disabled
because aggregate portfolio exposure is not coordinated.

By default, historical market candles come from Bybit public mainnet endpoints while
orders remain constrained to Bybit testnet. Testnet OHLCV history may be very sparse;
`MATS_MARKET_DATA_TESTNET=true` is an explicit opt-in to it.

## `GraphState`

`framework.core.state.GraphState` is the Pydantic graph-state contract:

| Field | Type | Purpose |
|---|---|---|
| `run_id` | `str` | Stable run/checkpoint identity |
| `symbol` | `str` | Active spot market |
| `timeframes_to_consider` | `list[str]` | All timeframes fetched and analyzed together |
| `raw_data` | `dict[str, list[OHLCVBar]]` | Normalized historical bars by timeframe |
| `market_context` | `dict` | Ticker, and optionally order book/recent trades |
| `analyst_findings` | `list[MultiTimeframeFindings]` | Unified higher-bias/lower-timing findings |
| `strategy_diagnostics` | `dict` | Historical coverage and measured, net-of-cost candidate results |
| `strategy` | `StrategyDefinition \| None` | Supported strategy DSL definition |
| `backtest_report` | `BacktestReport \| None` | Net-of-fee/slippage backtest and trades |
| `critique` | `Critique \| None` | `APPROVE`/`REJECT`, issues, revision instructions |
| `revision_count` | `int` | Bounded number of strategy revisions |
| `risk_assessment` | `RiskAssessment \| None` | Sized quantity, notional, loss, and stop |
| `generated_code` | `str \| None` | Optional Python signal skeleton; never executed |
| `execution_result` | `ExecutionReport \| None` | Dry-run/testnet orders, fills, and errors |
| `errors` | `list[str]` | Resumable node/graph failure details |
| `messages` | `list[dict]` | LLM call history summaries |
| `status` | `running \| completed \| failed` | Run lifecycle |
| `account_equity` | `float \| None` | Optional per-run equity override |

Nodes return partial state updates; the runner validates each update against
`GraphState`. Data is serialized as typed OHLCV lists so both SQLite checkpoints and
LangGraph's SQLite saver can restore it without serializing DataFrame internals.

## Persistence and resume

The SQLite database has `runs`, `agent_steps`, `strategies`, `backtests`, `critiques`,
`executions`, `system_events`, and `node_checkpoints` tables. LangGraph `SqliteSaver`
stores graph-level checkpoints in the same database. The node runner stores a complete
state checkpoint immediately before and after each node and records step inputs,
outputs, status, token usage, cost, and traceback details.

Step IDs are deterministic per run, node, and revision. On resume, completed steps
return their stored outputs without calling the node or model again. Failed steps are
reopened and retried; network, connection, timeout, and rate-limit errors receive
bounded exponential backoff. An interrupted or failed run can be resumed with
`framework.resume(run_id)` or `mats --resume [run-id]`.

## Tool contracts

All tools are Python-callable and return validated Pydantic objects or structured
records:

- `MarketDataTools.fetch_ohlcv(symbol, timeframe, limit)` returns ordered
  `OHLCVResult` bars normalized to UTC. The ticker, order book, and recent-trade
  methods expose the corresponding Bybit/CCXT snapshots.
- `compute_indicators(candles, timeframe)` returns the requested SMA, EMA, MACD, RSI,
  Stochastic, Bollinger, ATR, ADX, Supertrend, VWAP, OBV, CCI, ROC, Donchian, and
  Keltner values with last-value signals. `compute_multi_timeframe` applies it to all
  supplied frames.
- `BacktestTool.run(strategy, candles)` supports the validated long-only EMA crossover
  DSL, selected filters, stop/take-profit logic, and position fraction. Entries are
  evaluated on the next bar to avoid look-ahead. Fees and percentage/fixed slippage
  are charged on entry and exit; spot funding is zero. It returns the equity curve,
  net PnL, drawdown, Sharpe, win rate, trades, and cost totals.
- Before asking the strategy model to choose, `StrategyBuilderNode` backtests supported
  EMA/timeframe/filter candidates against historical bars with the configured cost
  model. It only offers measured candidates that meet the minimum history and
  trade-count thresholds, ranks candidates by net PnL and drawdown, and supplies at
  most the top five per timeframe. The model must preserve the tested entry, filter,
  stop, take-profit, and position parameters; the evidence is recorded in
  `strategy_diagnostics`.
- `reprice_trade_list(trades, CostAssumptions)` recomputes fees, slippage, and net PnL
  without changing the source trade objects.
- `ExecutionTools` exposes market/limit placement, conditional protective stops,
  order cancellation/lookup, open orders, and balance. Orders are monitored for
  completion; timeout handling cancels the remainder and reports partial fills.

The backtester deliberately rejects unsupported entry/exit rules and filters rather
than silently treating an unknown rule as valid. Current strategy execution is
long-only spot; it does not open short positions or futures.

## Model routing

`LLMClient.invoke_with_tools(messages, tools, model_config)` uses LiteLLM's
OpenAI-compatible tool schema. For Baseten, it deliberately uses LiteLLM's OpenAI
transport with Baseten's Model API base URL and passes the model slug unchanged; this
avoids routing a Model API slug through LiteLLM's deployment-specific `/predict` adapter.
Shared model settings come from `MATS_LLM_PROVIDER`,
`MATS_LLM_MODEL`, `MATS_LLM_API_KEY`, `MATS_LLM_BASE_URL`, and
`MATS_LLM_SUPPORTS_TOOLS`; role-specific `ModelRoute` values in `MATS_MODEL_ROUTES`
override them. Each route includes provider, model, credentials, optional API base URL,
and `supports_tools`; nodes reject routes without tool support before making a request.
Token counts and reported model cost are written to `agent_steps`.

## Adding an agent

1. Define and validate its I/O contract in `framework.schemas.models`.
2. Add one focused node class under `framework.agents`; accept `GraphState`, read
   inputs, and return only the changed state keys (or `AgentResult` with token/cost
   metadata).
3. If it calls a model, define a `ToolDefinition` and parse its structured tool call;
   never parse unvalidated free-form output.
4. Register the node in `TradingFramework._build_graph`, wrap it with
   `NodeRunner.wrap`, and add explicit success/failure/conditional edges.
5. Add unit tests using fake market/model/exchange clients. Check checkpoint recovery
   and ensure that a completed step is not called again.

## Operational safety

`MATS_DRY_RUN=true` and `MATS_KILL_SWITCH=true` are safe defaults. A live testnet
request requires both flags to be explicitly disabled, `MATS_BYBIT_TESTNET=true`, and
testnet credentials. The execution implementation rejects mainnet. Risk sizing uses
configured initial capital in dry-run mode; on live testnet it uses available quote
balance unless the run explicitly supplies `account_equity`. Never place live orders
with credentials outside a controlled testnet setup.

The default critique limit is two revision loops, for a maximum of three strategy
attempts per symbol including the initial attempt. `MATS_SYMBOLS` can be processed
concurrently with `mats --all`; each symbol gets a separate persisted run.
