# MATS Trading Research Framework

MATS is a local, multi-agent framework for crypto spot research and guarded trading on the
Bybit testnet. A run collects market data and news, has one analyst read every timeframe
together, diagnoses the market regime, picks a strategy from a library of tested templates,
backtests it net of fees and slippage, and lets a critique approve or reject it. An approved
strategy is sized by the risk agent and can then trade continuously on testnet as an
algorithm that watches price, risk, and news.

Everything is stored in SQLite, every agent step can be resumed, and a Next.js console shows
the agents working live.

```text
initialize → Data Worker → Analyst → Regime → Strategy Builder → Backtest → Critique
Critique REJECT → Strategy Builder (bounded by the run's attempt limit)
Critique APPROVE → Risk → [optional Coder] → Execution → live strategy algorithm (console)
```

## Safety defaults

- **Dry-run and the kill switch are both on by default.** Either one forces every order to be
  simulated. Turning either off from the console asks for confirmation.
- **Testnet only.** Market data comes from Bybit's public mainnet API for realistic history,
  but orders can only go to Bybit testnet; mainnet orders are rejected in code.
- **No trade without approval.** Risk sizing and execution are unreachable until the
  critique approves a backtest that clears the net-of-cost thresholds.
- **Spot, long only.** Short signals are never sent to the exchange. With
  `MATS_ALLOW_SHORT=true` they can be simulated in dry-run.
- **No borrowing unless you opt in.** Leverage is 1x by default; see [Leverage](#leverage).
- **Only your real balance is traded.** Live orders are sized against what the Bybit account
  actually holds; see [Balances](#balances).
- **Every live entry gets a protective stop.** If the stop cannot be confirmed, the
  position is flattened immediately.
- **Credentials stay in the Python process.** API keys are never sent to the browser.

## Install

Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/) are required (the locked Pydantic
native core does not yet support Python 3.15). The console needs Node.js.

```bash
uv sync --extra dev
cp .env.example .env
cd frontend && npm install
```

Set an LLM API key and, for testnet trading, Bybit **testnet** API credentials in `.env`.
Never commit `.env`.

### Key settings

All settings use the `MATS_` prefix and can live in `.env`. The most useful ones:

| Setting | Default | What it does |
|---|---|---|
| `MATS_DRY_RUN` / `MATS_KILL_SWITCH` | `true` / `true` | Simulate every order while either is on |
| `MATS_SYMBOLS` / `MATS_TIMEFRAMES` | `["BTC/USDT","ETH/USDT"]` / `["1h","4h","1d"]` | Defaults for new runs |
| `MATS_MAX_REVISION_LOOPS` | `2` | Revisions after the first attempt (3 attempts total) |
| `MATS_MIN_BACKTEST_TRADES` / `MATS_MIN_BACKTEST_NET_PNL` / `MATS_MAX_BACKTEST_DRAWDOWN` | `3` / `0` / `0.25` | Critique thresholds a backtest must clear |
| `MATS_INITIAL_CAPITAL` | `10000` | Capital for dry-run sizing and backtests |
| `MATS_FEE_RATE_TAKER` / `MATS_SLIPPAGE_PERCENT` | `0.001` / `0.0005` | Costs charged on every backtest fill |
| `MATS_MAX_RISK_PER_TRADE` / `MATS_MAX_ACCOUNT_EXPOSURE` | `0.01` / `0.25` | Risk agent sizing limits |
| `MATS_SPOT_LEVERAGE` | `1` | Spot margin multiple for long entries (1 to 3; 1 = no borrowing) |
| `MATS_EDGE_FADE_LOOKBACK` / `MATS_EDGE_FADE_MIN_TRADES` / `MATS_EDGE_FADE_MIN_EXPECTANCY` | `10` / `3` / `0` | When a live strategy is retired |
| `MATS_DATABASE_PATH` | `./data/state.db` | Runs, steps, checkpoints, positions |
| `MATS_CANDLE_CACHE_PATH` | `./data/candles.db` | Local candle cache |
| `MATS_MIN_HISTORY_DAYS` / `MATS_MAX_HISTORY_BARS` | `30` / `5000` | Research history depth per timeframe |
| `MATS_LOG_LEVEL` | `INFO` | `DEBUG` adds diagnostic detail |

See [`.env.example`](./.env.example) for the full list.

## Models

Every agent that calls a model uses tool calling and submits a small, validated structure;
free-form text is never trusted.

- **Default:** Baseten's OpenAI-compatible Model API with Muse
  (`MATS_LLM_PROVIDER=baseten`, `MATS_LLM_MODEL=meta-models/muse-spark-open-1026`).
- **Per-role routes:** `MATS_MODEL_ROUTES` can send the analyst, strategy, critic, or coder
  role to a different provider or model:

  ```dotenv
  MATS_MODEL_ROUTES='{"critic":{"provider":"anthropic","model":"claude-sonnet-5-5","api_key":"...","supports_tools":true}}'
  ```

- **Console:** models and Bybit keys can also be added and selected under Settings →
  Connections; they are stored in `data/connections.json`.
- **Local Ollama models** (provider `ollama`) use Ollama's native chat API. MATS requests a
  `MATS_OLLAMA_NUM_CTX` context window (16384 by default, instead of Ollama's ~4k) and caps
  replies at `MATS_OLLAMA_MAX_OUTPUT_TOKENS`. Models that write their tool call as JSON text
  (for example qwen2.5-coder) are understood. A 3B model can complete runs but analyses
  poorly; use a 7B+ tool-calling model or a hosted model for real research.

Small-model mistakes are handled without weakening validation: lists or objects sent as
JSON strings are decoded, common wordings ("uptrend", "approve") are normalised, and when a
reply is still invalid the error is sent back for one corrected attempt.

## How a run works

1. **Data Worker** downloads at least `MATS_MIN_HISTORY_DAYS` (30) days of candles per
   timeframe, never fewer than 500 and at most `MATS_MAX_HISTORY_BARS` (5000): about 17
   days of 5m, 30 days of 15m and 1h, and 500 candles of 4h or 1d. Only missing candles
   are downloaded (see [Market data](#market-data-and-the-candle-cache)). It also fetches
   the ticker, a reference pair at the same depth (for example ETH for BTC), funding
   history, and news and sentiment.
2. **Analyst** reads indicator snapshots for every timeframe plus summarised context and
   gives a bias on the highest timeframe and entry timing on the lowest.
3. **Regime** measures trend strength, volatility percentile, autocorrelation, and Hurst
   exponent, and labels the market (for example `trending_normal_vol` or `choppy_low_vol`).
4. **Strategy Builder** backtests every library template that fits the timeframes and regime,
   keeps candidates with enough history and at least `MATS_MIN_BACKTEST_TRADES` closed trades,
   and offers the best five per timeframe to the model, which picks one by candidate ID.
5. **Backtest** replays the chosen strategy with fees and slippage on every fill.
6. **Critique** applies fixed gates (net PnL, trade count, drawdown, no pure EMA crossover in
   chop, no family that recently lost in this regime) plus the model's review. A rejection
   sends written revision instructions back to the builder.
7. **Risk** sizes an approved strategy from the live signal, stop distance, and limits.
8. **Execution** places the entry and protective stop (or simulates them in dry-run).

### Strategy library and timeframes

There are 13 templates across momentum, mean reversion, relative value (pair spread),
volatility, breakout, carry (funding), regime-gated trend, and multi-timeframe families.
Strategies trade only **5m, 15m, 1h, 4h, and 1d**:

- 1m, 30m, 2h, 6h, 12h, and 1w add context to the analysis but no template trades them.
- On 5m only a few templates apply, and the multi-timeframe ones need a higher timeframe in
  the same run.
- Pair a short timeframe with 15m or 1h (for example 5m + 1h) for the best chance of an
  approved strategy. The console's start dialog warns about selections that cannot work.

### Strategy attempts and rejected runs

Each run gets three strategy attempts by default. Set a different limit per run with the
console's **Strategy attempts** option or `mats --max-attempts N`. When the critique refuses
every attempt, the run ends as `rejected`, which is shown as a notice rather than an error.
From there you can try two more attempts on the same run, reopen the start dialog with the
run's settings, or dismiss it (CLI: `mats --resume RUN_ID --extra-attempts N`).

## Live trading on testnet

Start a run with dry-run off and the kill switch off. When its strategy is approved, the
console runs it as a continuous strategy algorithm, checking every 60 seconds:

- **In a position:** it manages the exit: stop-loss, take-profit, opposite signal, time
  stop, trailing ATR stop, or a news-shock exit.
- **Flat:** it re-checks the signal, the risk plan, and live news risk, and enters only on a
  new closed candle when every execution guard approves.
- **After an exit:** the trade is recorded and the strategy re-arms for its next entry.
- **Retirement:** once at least `MATS_EDGE_FADE_MIN_TRADES` closed trades exist and their
  average net PnL falls below `MATS_EDGE_FADE_MIN_EXPECTANCY`, the strategy is retired and
  will not re-enter. A manual flatten also retires it.
- **Safety switches:** with the kill switch on, the algorithm starts paused, says why, and
  resumes by itself when the switch is turned off.
- **Stopping:** stopping one run stops only that run. **Stop all** stops every run and turns
  the kill switch on. Algorithms are restored after an API restart.

### Balances

With Bybit keys configured, the console shows the account's holdings (free, locked, USD
value, and any borrowing) in the Positions tab, refreshed every 30 seconds. Live trading only
uses money the account actually holds:

- A live run will not start without any of the quote currency (USDT), or when the capital you
  enter is more than what is free.
- Risk sizing uses the free balance; larger run capital is capped to it, with a note.
- Each entry re-checks the free balance (including fees and slippage) and buys less, or holds,
  if the account cannot pay for the full order.
- Protective stops and exits sell only the coin actually held; Bybit takes spot buy fees in the
  coin. A position sold outside MATS is recorded as closed without sending an order.

Dry runs are simulated and do not need a balance.

### Leverage

Bybit Unified accounts can trade spot on margin: an order flagged `isLeverage` borrows the
shortfall in the quote currency. MATS leaves this off (1x) unless you choose 2x or 3x per run
in the start dialog, or set `MATS_SPOT_LEVERAGE`:

- Only long entries borrow, and only when the free balance cannot pay for the order.
- Leverage raises the exposure limits, but the loss at each stop is still capped at
  `MATS_MAX_RISK_PER_TRADE` of equity, so a stop-limited trade never gets bigger.
- Borrowing is limited to (leverage - 1) times the free balance and to what Bybit will lend.
- After every leveraged exit, MATS repays the loan from the free balance with Bybit's
  no-conversion repay. Bybit refuses repayments from minute 04:00 to 05:30 of each hour, so a
  refused repay is remembered and retried on every later check.
- Spot margin must already be on in Bybit (it asks for a short quiz). MATS never changes that
  or any other account setting; a leveraged live run is refused while it is off.

Borrowing costs interest until repaid, and on a Unified account a liquidation can sell your
other holdings too.

For multi-symbol live batches, a supplied account equity is split evenly across symbols;
otherwise the available testnet quote balance is fetched once and split. Only one live batch
runs at a time.

### News and sentiment

Headlines come from public RSS feeds (CoinDesk, Cointelegraph, Decrypt, and a per-coin Google
News search), plus the crypto Fear & Greed index; no API keys are needed. Each headline is
scored for tone and for shock events such as hacks, delistings, or halted withdrawals.

- A coin-specific shock from the last six hours closes an open long and blocks entries.
- A market-wide shock, bearish headline tone, or extreme greed blocks new entries only.
- Missing news never blocks trading.

## Market data and the candle cache

Candles come from Bybit's public mainnet API by default (`MATS_MARKET_DATA_TESTNET=true` uses
the much sparser testnet history). Closed candles are cached in `./data/candles.db`,
separately for mainnet and testnet, so later runs, resumes, and the live algorithm only
download what they do not have:

- The cache records which time ranges it has verified with the exchange, so a missing row is
  never mistaken for a missing candle.
- The last `MATS_CANDLE_REFRESH_BARS` closed candles (2 by default) are re-read every time in
  case the exchange revised them; the still-forming candle is always fresh and never stored.
- Requests page 1000 candles at a time with explicit start and end times, a range counts as
  verified only up to the newest candle the exchange returned, and genuine gaps in the
  exchange's own history are logged once instead of being refetched forever.

Delete the file to rebuild it, or set `MATS_CANDLE_CACHE_ENABLED=false` to download every
time. Requests use ccxt's built-in Bybit throttle unless `MATS_EXCHANGE_RATE_LIMIT_MS` is set;
ccxt multiplies that value by each endpoint's cost, so large values make every call slow.

## Command line

```bash
uv run mats --symbol BTC/USDT
uv run mats --symbol ETH/USDT --timeframe 15m --timeframe 1h --account-equity 5000
uv run mats --symbol BTC/USDT --max-attempts 5
uv run mats --all                                  # every MATS_SYMBOLS pair
uv run mats --symbol BTC/USDT --symbol ETH/USDT --workers 2
uv run mats --resume                               # latest incomplete run
uv run mats --resume RUN_ID --extra-attempts 2     # more attempts for a rejected run
uv run mats --loop --interval 60                   # repeated polling cycles
uv run mats --monitor-open-positions               # one exit check for open positions
uv run mats --symbol BTC/USDT --reset-stopped-strategies
uv run mats --compact-database                     # one-off shrink of an older state.db
```

Research and dry-run symbols run concurrently (two at a time by default). Live testnet
symbols run one after another so each sizes against the balance left by the previous one.
Results are printed as JSON on stdout; the CLI exits non-zero if any run fails. A `rejected`
run is not a failure.

From Python:

```python
from framework.core.graph import TradingFramework

framework = TradingFramework()
try:
    result = framework.run("BTC/USDT", {"timeframes": ["15m", "1h"], "max_revision_loops": 2})
    print(result.model_dump_json(indent=2))
finally:
    framework.close()
```

## Web console

Start the API from the repository root, then the console:

```bash
uv run mats-web
```

```bash
cd frontend
npm run dev
```

Open `http://localhost:3000`. The API listens on `127.0.0.1:8765` only and uses the same
`.env` as the CLI.

- **Starting runs:** pick coins, timeframes, capital, strategy attempts, and safety switches
  in the start dialog, or type a request such as "Start trading BTC/USDT and ETH/USDT" in the
  composer. Each symbol becomes its own run, shown live.
- **Activity:** every agent step appears in the feed with its inputs, tool calls, and
  structured output. The side panels can be resized by dragging their edges (double-click
  to reset).
- **Diagnosis:** every run explains why it failed, is paused, is waiting for an entry, is in
  a position, or finished without a trade, and what to do next.
- **Backtest tab:** switch between every strategy attempt in a run to compare metrics, the
  trade chart, and the critique's reasons.
- **Positions and Activity Log:** open positions, flattening, and every system event.
- **Live chart:** loads public history and streams Bybit's public WebSocket directly.

For static inspection without the API, open [`frontend/dashboard.html`](./frontend/dashboard.html)
in a browser and choose a SQLite database file; it reads the file locally (its SQLite reader
loads from a CDN).

## Storage

Runs, agent steps, checkpoints, positions, and events live in `data/state.db`. Each run's
candles are stored once, compressed, and every checkpoint and step record refers to them,
so a run adds about 1.3 MB instead of about 18 MB. Databases created before this change
can be shrunk once (stop `mats-web` first; consider copying the file as a backup):

```bash
uv run mats --compact-database
```

A 590 MB database compacted to 44 MB this way, with every run's restored state unchanged.

## Troubleshooting

| Console message | Meaning and fix |
|---|---|
| No strategy could be tested on these timeframes | No template trades the selected timeframes, or too few trades occurred. Add 15m or 1h. |
| No strategy approved after N attempts | Every candidate missed the net-of-cost or regime checks. Try more attempts, other timeframes, or another symbol. |
| The model did not return the required structured answer | The model ignored the tool after a retry. Resume, or choose a stronger tool-calling model. |
| Approved strategy is paused | The kill switch or dry-run is on. Turn both off to trade on testnet. |
| Completed without a trade | The strategy's entry condition was not met at that moment, or a safety switch simulated the order. |
| Credentials were rejected / No testnet balance | Check the model key or the Bybit testnet key, or fund the testnet account. |

Backend logs on stderr carry run, symbol, and agent context, stage durations, retries,
market-data and LLM request summaries, and execution safety outcomes. Prompts, credentials,
and raw exchange payloads are never logged.

## Known limitations

- **Run time:** deeper history makes the strategy builder slower on short timeframes
  (about 20 to 40 seconds per timeframe per attempt on this machine).
- **Spot only:** no margin, borrowing, or derivatives; shorts are simulation-only.
- **Testnet only:** mainnet execution is deliberately disabled.

## Tests and checks

```bash
uv run pytest
uv run ruff check src/framework tests
uv run ruff format --check src/framework tests
cd frontend && npm run typecheck && npm run lint
```

Tests use local fixtures and fakes; they never call a live exchange or model.

See [docs/framework.md](./docs/framework.md) for graph state, persistence, tool contracts,
recovery, and how to add an agent.
