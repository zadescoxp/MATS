# MATS Trading Research Framework

A local, SQLite-backed, model-agnostic framework for crypto spot research and guarded
Bybit testnet execution. The graph collects market data, analyzes all configured
timeframes with one primary analyst, builds and cost-tests strategies, applies a
net-of-cost critique gate, sizes approved plans, and records dry-run or testnet orders.

## Safety defaults

- Dry-run is enabled by default.
- The global kill switch is enabled by default and forces order placement to dry-run.
- Live order placement is limited in code to Bybit testnet; mainnet orders are rejected.
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

Set model routes and optional Bybit testnet credentials in `.env`. The settings model
supports per-role providers, model names, API keys, base URLs, and a tool-support flag
through `MATS_MODEL_ROUTES` JSON. For example:

```dotenv
MATS_MODEL_ROUTES='{"analyst":{"provider":"openai","model":"gpt-4o-mini","api_key":"...","supports_tools":true},"strategy":{"provider":"anthropic","model":"claude-3-5-sonnet-latest","api_key":"...","supports_tools":true}}'
```

Do not commit `.env` or testnet credentials. The model routes default to an OpenAI
tool-calling model name; configure an API key before starting a run.

## Run and resume

```bash
uv run mats --symbol BTC/USDT
uv run mats --resume
uv run mats --resume <run-id>
uv run mats --symbol ETH/USDT --timeframe 15m --timeframe 1h --account-equity 5000
uv run mats --loop --interval 60
```

The database defaults to `./data/state.db`. Configure `MATS_DATABASE_PATH` to change it.
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
