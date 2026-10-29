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
| `live_signal` | `LiveSignal \| None` | Fresh deterministic long/short/exit/hold signal and reason |
| `generated_code` | `str \| None` | Optional Python signal skeleton; never executed |
| `execution_result` | `ExecutionReport \| None` | Dry-run/testnet orders, fills, and errors |
| `errors` | `list[str]` | Resumable node/graph failure details |
| `messages` | `list[dict]` | LLM call history summaries |
| `status` | `running \| completed \| failed \| stopped` | Run lifecycle |
| `account_equity` | `float \| None` | Optional per-run equity override |

Nodes return partial state updates; the runner validates each update against
`GraphState`. Data is serialized as typed OHLCV lists so both SQLite checkpoints and
LangGraph's SQLite saver can restore it without serializing DataFrame internals.

## Persistence and resume

The SQLite database has `runs`, `agent_steps`, `strategies`, `backtests`, `critiques`,
`executions`, `live_positions`, `strategy_stops`, `system_events`, and `node_checkpoints`
tables. LangGraph `SqliteSaver`
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
- `evaluate_live_signal(strategy_def, latest_ohlcv_dict, position=None)` is a pure
  evaluator shared by backtests, risk assessment, execution, and the position monitor.
  It returns `action`, `reason`, `strength`, `price`, `stop`, and `strategy_over`.
- The hard-coded, structured strategy library in `framework.strategies.library`
  contains six templates: EMA crossover; RSI recovery from oversold/overbought in the
  EMA trend; Supertrend direction change filtered by ATR percentage; MACD histogram
  zero-cross with higher-timeframe EMA bias; Bollinger squeeze breakout; and
  higher-timeframe EMA trend with lower-timeframe EMA crossover timing. Each saved
  strategy JSON includes its `template_name` and exact parameter values. The model
  sees the library summary and may only select a measured candidate; arbitrary
  generated strategy code is not executed.
- Library defaults use a 2% protective stop. EMA crossover exits on the opposite
  crossover; RSI pullback and Bollinger breakout use a 4% target; Supertrend trails at
  3 ATR; MACD-with-bias has a 48-bar time stop; and higher/lower-timeframe timing has
  a 24-bar time stop. Strategies exit earlier on their stop, target, or applicable
  opposite signal. These are starting templates; the persisted strategy JSON is the
  runtime source of truth.
- `BacktestTool.run(strategy, candles, data=...)` evaluates candidates using the same
  signal function as live execution. A crossover is acted on at the next candle open to
  avoid look-ahead. Current spot backtests open long positions only; bearish signals
  close an open long, and do not simulate borrowed/derivative shorts. Fees and slippage
  are charged on entry and exit.
- Template exits include the opposite signal, configured percentage stop, optional
  percentage target, ATR trailing stop, and bar-count time stop. Live position
  monitoring checks the current candle open/high/low against protective stop and target,
  opposite template signals, and time stops. A stop/target hit, opposite signal, or time
  stop closes the position and stops that exact symbol/template/parameter fingerprint
  until reset. `MATS_EDGE_FADE_LOOKBACK` closed trades are used to compute recent
  average net PnL in quote-currency units; expectancy below
  `MATS_EDGE_FADE_MIN_EXPECTANCY` also closes and
  stops the strategy. With no closed trades, expectancy is unavailable and does not
  trigger edge fade.
- `StrategyBuilderNode` backtests candidates from supported template/timeframe
  combinations using configured costs. It only offers candidates that meet configured
  history and trade-count thresholds, ranks candidates by net PnL and drawdown, and
  supplies at most the top five per timeframe. Template, parameters, exit rule, risk
  values, and timeframe are stored in the strategy JSON and diagnostics.
- `reprice_trade_list(trades, CostAssumptions)` recomputes fees, slippage, and net PnL
  without changing the source trade objects.
- `ExecutionTools` exposes market/limit placement, conditional protective stops,
  order cancellation/lookup, open orders, and balance. Orders are monitored for
  completion; timeout handling cancels the remainder and reports partial fills.

The backtester deliberately rejects unsupported entry/exit rules and filters rather
than silently treating an unknown rule as valid. `ExecutionNode` logs `dry_run` and
`kill_switch` at startup, re-fetches the strategy's OHLCV timeframes, and re-evaluates
the live signal instead of trusting analyst flags. It sizes only actionable signals
with positive risk size; hold/exit/zero-size decisions are recorded as expected
`hold` results without placing an order. Real Bybit testnet entries record CCXT order
IDs, status, fill details, and the protective stop ID in `executions` and
`live_positions`.

The current order path is Bybit spot, not margin or derivatives. `MATS_ALLOW_SHORT`
defaults to false. Short signals are not opened with the default setting; with
`MATS_ALLOW_SHORT=true`, short entries can be represented in dry-run simulation, but
live spot execution still refuses to send them because this path does not borrow or
create a derivative position. When monitoring a long spot position, an opposite short
signal is treated as an exit to flat.
Persisted open positions can be checked once with `mats --monitor-open-positions` or
on each `mats --loop` polling cycle. Exits cancel the recorded protective order before
sending a market flatten order. Exit events and stopped-strategy fingerprints are
persisted so resume/new runs do not silently reopen that strategy.
`mats --symbol BTC/USDT --reset-stopped-strategies` explicitly clears stopped
fingerprints for that symbol.

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
