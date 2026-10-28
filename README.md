# MATS Trading Research Framework

A local, SQLite-backed, model-agnostic framework for crypto spot research and guarded
Bybit testnet execution. The graph collects market data, analyzes all configured
timeframes with one primary analyst, builds and cost-tests strategies, applies a
net-of-cost critique gate, sizes approved plans, and records dry-run or testnet orders.

## Safety defaults

- Dry-run is enabled by default.
- The global kill switch is enabled by default and forces order placement to dry-run.
- Public Bybit market data is used by default to provide the historical depth needed
  for meaningful indicators/backtests; order placement is limited to Bybit testnet.
- Live mainnet orders are rejected.
- Risk assessment and execution are unreachable until the critique approves the backtest.
- A lower-timeframe bullish entry timing signal is required before sizing an order.
- No real exchange order has been placed as part of this implementation.

## Install

Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/) are required. The locked
Pydantic native core does not yet support Python 3.15.

```bash
uv sync --extra dev
cp .env.example .env
```

Set the shared LLM provider/model/API key/base URL and optional Bybit testnet credentials
in `.env`. Per-role overrides are also supported through `MATS_MODEL_ROUTES` JSON. For example:

```dotenv
MATS_MODEL_ROUTES='{"analyst":{"provider":"baseten","model":"meta-models/muse-spark-open-1026","api_key":"...","base_url":"https://inference.baseten.co/v1","supports_tools":true},"strategy":{"provider":"anthropic","model":"claude-3-5-sonnet-latest","api_key":"...","supports_tools":true}}'
```

Do not commit `.env` or API credentials. The default shared LLM settings select
Baseten Muse; configure an API key before starting a run. Every routed model must
support tool calling. Baseten calls use the OpenAI-compatible Model API at
`https://inference.baseten.co/v1`; the Muse model ID is passed unchanged.

## Run and resume

```bash
uv run mats --symbol BTC/USDT
uv run mats --all
uv run mats --symbol BTC/USDT --symbol ETH/USDT --workers 2
uv run mats --resume
uv run mats --resume <run-id>
uv run mats --symbol ETH/USDT --timeframe 15m --timeframe 1h --account-equity 5000
uv run mats --loop --interval 60
```

`--all` runs every `MATS_SYMBOLS` pair concurrently (two at a time by default); each
pair gets its own run and final JSON state in the returned JSON array. Use `--workers`
to cap concurrency. Concurrent multi-symbol execution is refused if both dry-run and
the kill switch are disabled, because aggregate live portfolio exposure is not yet
coordinated.

The database defaults to `./data/state.db`. Configure `MATS_DATABASE_PATH` to change it.
Public market history is fetched from Bybit mainnet by default, while the execution
adapter remains on Bybit testnet. Set `MATS_MARKET_DATA_TESTNET=true` only when
intentionally using the sparse testnet candle history.

Each symbol gets at most three strategy attempts total: one initial candidate and up to
two critique-guided revisions (`MATS_MAX_REVISION_LOOPS=2`). Before spending a strategy
call, the builder measures supported EMA candidates across configured timeframes using
the available bars and actual fee/slippage settings. It only offers candidates with
enough history and trades; insufficient history or no viable signal is recorded in
`strategy_diagnostics` rather than hidden behind repeated critique loops.

If a symbol has too little testnet history, you will see the reason in that symbol's
JSON `errors` and `strategy_diagnostics.timeframe_coverage`. A zero net PnL with zero
trades means no trades were opened; it is not evidence that trading costs erased an
edge. Review `backtest_report.total_trades`, `total_fees`, and `total_slippage`.

Runs can also be started and resumed from Python:

```python
from framework.core.graph import TradingFramework

framework = TradingFramework()
try:
    result = framework.run("BTC/USDT")
    print(result.model_dump_json(indent=2))
finally:
    framework.close()
```

See [docs/framework.md](./docs/framework.md) for graph state, persistence, tool contracts,
recovery behavior, and instructions for adding an agent.

## Tests and checks

```bash
uv run pytest
uv run ruff check src/framework tests
uv run ruff format --check src/framework tests
```

Tests use local fixtures and mocks; they do not call a live exchange or LLM.
