import json

from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.strategies.regime import diagnose_regime


class RegimeNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> dict[str, object]:
        if not state.raw_data:
            raise ValueError("Regime diagnosis requires OHLCV data")
        regime = diagnose_regime(state.raw_data, state.market_context)
        self.context.database.log_event(
            state.run_id,
            "regime_diagnosed",
            json.dumps(regime, separators=(",", ":"), sort_keys=True),
        )
        return {"regime": regime}
