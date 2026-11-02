# Framework state, tools, and extension guide

## Graph

The production graph is:

```text
initialize -> DataWorkerNode -> MultiTimeframeAnalystNode -> RegimeNode
           -> StrategyBuilderNode -> BacktestNode -> CritiqueNode
Critique REJECT -> StrategyBuilderNode (bounded by the run's revision limit)
Critique REJECT at the limit -> END with status "rejected"
Critique APPROVE -> RiskAssessmentNode -> [optional CoderNode] -> ExecutionNode
```

`RegimeNode` is deterministic: it labels the market from trend strength, ATR percentile,
return autocorrelation, and the Hurst exponent, and the strategy builder and critique use
the label to match strategy families to conditions. After the graph ends, the dashboard
API (`framework.web`) runs an approved live strategy as a continuous algorithm; see
"Live strategy algorithm" below.

There is exactly one primary analyst. It receives every configured timeframe in one
call, computes the technical indicator suite, and emits one combined finding object.
Non-price context can be included in the finding without introducing a second generic
analyst. Polling is available through the CLI or `TradingFramework.run_polling`.
`TradingFramework.run_many` gives each symbol its own graph/checkpointer and runs
symbols concurrently in research/dry-run mode, preserving input order. Live testnet
symbols are run sequentially so each run sizes against the updated exchange balance
after preceding orders have settled.

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
| `market_context` | `dict` | Ticker, news and sentiment, reference-pair candles, funding, optional order book/trades |
| `regime` | `dict` | Deterministic regime label and the measurements behind it |
| `analyst_findings` | `list[MultiTimeframeFindings]` | Unified higher-bias/lower-timing findings |
| `strategy_diagnostics` | `dict` | Per-timeframe coverage, why templates were skipped, and measured candidates |
| `strategy` | `StrategyDefinition \| None` | Supported strategy DSL definition |
| `backtest_report` | `BacktestReport \| None` | Net-of-fee/slippage backtest and trades |
| `critique` | `Critique \| None` | `APPROVE`/`REJECT`, issues, revision instructions |
| `revision_count` | `int` | Strategy revisions so far |
| `max_revision_loops` | `int \| None` | Per-run revision limit; `None` uses `MATS_MAX_REVISION_LOOPS` |
| `risk_assessment` | `RiskAssessment \| None` | Sized quantity, notional, loss, and stop |
| `live_signal` | `LiveSignal \| None` | Fresh deterministic long/short/exit/hold signal and reason |
| `generated_code` | `str \| None` | Optional Python signal skeleton; never executed |
| `execution_result` | `ExecutionReport \| None` | Dry-run/testnet orders, fills, and errors |
| `errors` | `list[str]` | Resumable node/graph failure details |
| `messages` | `list[dict]` | LLM call history summaries |
| `status` | `running \| completed \| failed \| stopped \| rejected` | Run lifecycle; `rejected` means every attempt was refused |
| `account_equity` | `float \| None` | Optional per-run equity override |

Nodes return partial state updates; the runner validates each update against
`GraphState`. Data is serialized as typed OHLCV lists so SQLite checkpoints can restore
it without serializing DataFrame internals.

## Persistence and resume

The SQLite database has `runs`, `agent_steps`, `strategies`, `backtests`, `critiques`,
`executions`, `live_positions`, `strategy_trades` (closed live trades, used for edge fade),
`strategy_stops`, `strategy_family_memory`, `entry_watchers` (live algorithms),
`system_events`, `node_checkpoints`, and `candle_series` tables. The node runner stores a
complete state checkpoint immediately before and after each node and records step inputs,
outputs, status, token usage, cost, and traceback details. LangGraph's own checkpointer is
not attached: recovery only ever used these records.

Candle lists are stored once. When a checkpoint or step record is written, every list of
OHLCV bars (20 or more) is saved to `candle_series`, compressed and keyed by a hash of its
exact JSON, and the record keeps a small `{"$candles": id, "count", "first", "last"}`
reference (`framework.persistence.candle_series`). Reads restore the exact list, so resume
sees byte-identical candles, including the still-forming candle the run used. Rows written
before this change hold plain lists and read back unchanged; `mats --compact-database`
rewrites them, drops LangGraph's old tables, and runs `VACUUM`.

Step IDs are deterministic per run, node, and revision. On resume, completed steps
return their stored outputs without calling the node or model again. Failed steps are
reopened and retried; network, connection, timeout, and rate-limit errors receive
bounded exponential backoff. An interrupted or failed run can be resumed with
`framework.resume(run_id)` or `mats --resume [run-id]`.

## Tool contracts

All tools are Python-callable and return validated Pydantic objects or structured
records:

- `MarketDataTools.fetch_ohlcv(symbol, timeframe, limit)` returns the latest `limit`
  ordered `OHLCVResult` bars normalized to UTC, ending with the still-forming candle.
  When the tool builds its own Bybit client, bars come through `CandleCache`
  (`framework.tools.candle_store`): closed candles and the time ranges verified with the
  exchange are kept in `MATS_CANDLE_CACHE_PATH`, so only unverified ranges and the last
  `MATS_CANDLE_REFRESH_BARS` closed candles are downloaded. Requests longer than Bybit's
  1000-candle page are paged even with the cache disabled (an in-memory store is used).
  `DataWorkerNode` sizes research history per timeframe with `history_bars`: at least
  `MATS_MIN_HISTORY_DAYS` of candles, never fewer than 500, capped at
  `MATS_MAX_HISTORY_BARS`; the reference pair uses the same depth. The ticker, order book,
  funding-history, and recent-trade methods expose the corresponding Bybit/CCXT snapshots.
- `collect_context(symbol)` and `news_sentiment(symbol)` (`framework.tools.context_feed`)
  gather keyless RSS headlines, the Fear & Greed index, macro series, filings, and
  fundamentals, tagging each item with a holding horizon and each headline with a tone
  score and shock flag. `assess_news_risk` (`framework.strategies.news_risk`) turns a
  sentiment snapshot into "block entry" and "force exit" decisions for the live algorithm.
- `compute_indicators(candles, timeframe)` returns the requested SMA, EMA, MACD, RSI,
  Stochastic, Bollinger, ATR, ADX, Supertrend, VWAP, OBV, CCI, ROC, Donchian, and
  Keltner values with last-value signals. `compute_multi_timeframe` applies it to all
  supplied frames.
- `evaluate_live_signal(strategy_def, latest_ohlcv_dict, position=None)` is a pure
  evaluator shared by backtests, risk assessment, execution, and the position monitor.
  It returns `action`, `reason`, `strength`, `price`, `stop`, and `strategy_over`.
- The hard-coded, structured strategy library in `framework.strategies.library`
  contains 13 templates in eight families: EMA crossover, Supertrend with an ATR filter,
  and MACD with higher-timeframe bias (momentum); RSI pullback and z-score mean
  reversion; a pair-spread statistical arbitrage (relative value); Bollinger squeeze and
  volatility fade (volatility); confirmed breakout; funding carry; regime-gated trend;
  and two multi-timeframe templates (higher-timeframe trend with lower-timeframe timing,
  and a hybrid). Together they trade 5m, 15m, 1h, 4h, and 1d; each template lists its
  own `supported_timeframes`, and templates with a `higher_timeframe` parameter need a
  higher timeframe in the same run. Each saved strategy JSON includes its
  `template_name` and exact parameter values. The model picks one measured candidate by
  `candidate_id`; arbitrary generated strategy code is never executed.
- Library defaults use a 2% protective stop. EMA crossover exits on the opposite
  crossover; RSI pullback and Bollinger breakout use a 4% target; Supertrend trails at
  3 ATR; MACD-with-bias has a 48-bar time stop; and higher/lower-timeframe timing has
  a 24-bar time stop. Strategies exit earlier on their stop, target, or applicable
  opposite signal. These are starting templates; the persisted strategy JSON is the
  runtime source of truth.
- `BacktestTool.run(strategy, candles, data=...)` evaluates candidates using the same
  signal function as live execution, on the same trailing window: each bar's signal sees
  the last `LIVE_SIGNAL_BARS` (500) candles of every series, as live trading fetches, and
  never a candle from after that bar. This keeps backtests linear in history length and
  makes a backtested signal the one live trading would have produced. A crossover is
  acted on at the next candle open to avoid look-ahead. Current spot backtests open long positions only; bearish signals
  close an open long, and do not simulate borrowed/derivative shorts. Fees and slippage
  are charged on entry and exit.
- Template exits include the opposite signal, configured percentage stop, optional
  percentage target, ATR trailing stop, and bar-count time stop. Live position
  monitoring checks the current candle open/high/low against protective stop and target,
  opposite template signals, and time stops. A stop/target hit, opposite signal, or time
  stop closes the position, records the trade in `strategy_trades`, and leaves the
  strategy armed for its next entry. The last `MATS_EDGE_FADE_LOOKBACK` closed trades give
  a rolling average net PnL in quote-currency units; once at least
  `MATS_EDGE_FADE_MIN_TRADES` trades exist, expectancy below
  `MATS_EDGE_FADE_MIN_EXPECTANCY` closes the position and stops that exact
  symbol/template/parameter fingerprint until reset. A manual flatten also stops it.
- `StrategyBuilderNode` backtests candidates from supported template/timeframe
  combinations using configured costs. It only offers candidates that meet configured
  history and trade-count thresholds, ranks candidates by net PnL and drawdown, and
  supplies at most the top five per timeframe. Template, parameters, exit rule, risk
  values, and timeframe are stored in the strategy JSON and diagnostics. When nothing
  qualifies, `strategy_diagnostics.timeframe_coverage` records for each timeframe which
  templates do not trade it, need a higher timeframe, do not fit the regime, lack pair
  or funding data, or made too few closed trades, and the run error lists those reasons.
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
sending a market flatten order. Exit events, closed trades, and stopped-strategy
fingerprints are persisted so resume/new runs do not silently reopen a retired strategy.
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

Ollama routes (`provider: ollama`) use LiteLLM's native `ollama_chat/` transport with
`MATS_OLLAMA_NUM_CTX` and a reply cap of `MATS_OLLAMA_MAX_OUTPUT_TOKENS`. Unsupported
parameters are dropped for every provider, and a reply that writes a tool call as JSON
text is accepted when it names one of the offered tools.

Agents call models through `invoke_structured` (`framework.agents.common`), which
parses the matching tool call, decodes lists or objects sent as JSON strings, validates
the result, and on failure sends the error back for one corrected attempt. Token use and
cost cover both calls. Submission schemas are deliberately small: the analyst submits
`AnalystSubmission` (timeframes and indicator snapshots are filled in by code) and the
strategy model submits a `StrategyChoice` naming a measured candidate.

## Adding an agent

1. Define and validate its I/O contract in `framework.schemas.models`.
2. Add one focused node class under `framework.agents`; accept `GraphState`, read
   inputs, and return only the changed state keys (or `AgentResult` with token/cost
   metadata).
3. If it calls a model, define a small submission model with `submit_tool` and call
   `invoke_structured`; never trust unvalidated free-form output. Ask the model only for
   judgments, and fill in facts the code already knows.
4. Register the node in `TradingFramework._build_graph`, wrap it with
   `NodeRunner.wrap`, and add explicit success/failure/conditional edges.
5. Add unit tests using fake market/model/exchange clients. Check checkpoint recovery
   and ensure that a completed step is not called again.

## Balances and leverage

Live sizing reads the Bybit account through `ExecutionTools.get_balance`
(`AccountBalance.available`, `framework.tools.account`). Risk sizing caps run capital to
the free quote balance, `ExecutionNode` re-checks affordability before each entry and
sizes the protective stop to the coin actually received, and `PositionMonitor` sells only
what is held. With `leverage` above 1 (run config or `MATS_SPOT_LEVERAGE`, at most 3) and
spot margin on, buying power adds up to (leverage - 1) times the free balance, limited by
Bybit's `availableToBorrow`; the order is sent with `isLeverage=1` only when cash does not
cover it, and the position records its leverage. Closing a leveraged position calls
`ExecutionTools.repay_borrowed` (`POST /v5/account/no-convert-repay`); refused repays go
to `pending_repays` and are retried by every monitor and algorithm check. MATS never
changes account settings such as margin mode or auto-repay.

## Operational safety

`MATS_DRY_RUN=true` and `MATS_KILL_SWITCH=true` are safe defaults. A live testnet
request requires both flags to be explicitly disabled, `MATS_BYBIT_TESTNET=true`, and
testnet credentials. The execution implementation rejects mainnet. Risk sizing uses
configured initial capital in dry-run mode; on live testnet it uses available quote
balance unless the run explicitly supplies `account_equity`. Never place live orders
with credentials outside a controlled testnet setup.

The default critique limit is two revision loops, for a maximum of three strategy
attempts per symbol including the initial attempt. A run can set its own limit
(`max_revision_loops` in the run config, `--max-attempts` on the CLI), and a run that
ends `rejected` can be resumed with extra attempts (`resume(run_id, extra_attempts=N)`,
`--extra-attempts`). `MATS_SYMBOLS` can be processed concurrently with `mats --all`; each
symbol gets a separate persisted run.

## Live strategy algorithm

When a dashboard run with dry-run off ends with an approved strategy,
`DashboardService` starts an algorithm for it (`entry_watchers` row, 60-second loop):

- With an open position it calls `PositionMonitor.monitor_positions`, passing a
  `news_risk_exit` when `assess_news_risk` reports a coin-specific shock.
- When flat it checks edge fade, re-runs `RiskAssessmentNode` for a fresh signal and size,
  and runs `ExecutionNode` only on a new closed candle when news risk does not block entry.
  Entry and stop order keys include the bar, so a re-armed strategy can enter again.
- Ordinary exits close the position, record the trade in `strategy_trades`, and keep the
  strategy armed. Edge fade (`PositionMonitor.edge_fade_reason`) or a manual flatten marks
  the fingerprint stopped, which retires the algorithm.
- While dry-run or the kill switch is on the algorithm is paused and logs why. Stopping a
  single run does not change the kill switch; `stop_all` turns it on.

Every run in the dashboard snapshot carries a `diagnosis` from `diagnose_run`
(`framework.core.diagnostics`): a kind (`failed`, `rejected`, `paused`, `waiting`,
`in_position`, `no_trade`, `dry_run`, `retired`, `stopped`), a title, the detail, and a
suggested next step.
