import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from framework.agents.strategy_builder import (
    StrategyBuilderNode,
    _learning_fields,
    _shortlist,
)
from framework.config.settings import Settings
from framework.core.state import GraphState
from framework.persistence.database import Database
from framework.persistence.selection_memory import (
    arm_stats,
    record_arm_outcomes,
    selection_report,
)
from framework.schemas.models import OHLCVBar, StrategyDefinition, TradeRecord
from framework.strategies.bandit import prior_bonus
from framework.strategies.scoring import outcome_score, selection_score, t_statistic
from framework.strategies.search import TemplateJob, measure_template
from framework.tools.backtest import BacktestTool
from framework.tools.cost_model import CostAssumptions

START = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(net_return: float) -> TradeRecord:
    return TradeRecord(
        entry_time=START,
        exit_time=START + timedelta(hours=1),
        entry_price=100,
        exit_price=100,
        quantity=10,
        entry_notional=1_000,
        exit_notional=1_000,
        gross_pnl=net_return * 1_000,
        fees=0,
        slippage=0,
        net_pnl=net_return * 1_000,
        exit_reason="test",
    )


def _strategy(template: str = "supertrend_atr", timeframe: str = "1h") -> StrategyDefinition:
    return StrategyDefinition(
        name=template,
        description=template,
        chosen_timeframe=timeframe,
        template_name=template,
        entry_rule=template,
    )


# --- Scoring ------------------------------------------------------------------------


def test_consistent_edges_beat_lucky_ones() -> None:
    steady = [_trade(0.01 + 0.001 * (index % 3)) for index in range(20)]
    lucky = [_trade(0.30)] + [_trade(-0.004) for _ in range(19)]

    # The lucky set makes more money in total but its average trade is unreliable.
    assert sum(trade.net_pnl for trade in lucky) > sum(trade.net_pnl for trade in steady)
    assert t_statistic([0.01 + 0.001 * (i % 3) for i in range(20)]) > 3
    assert selection_score(steady, 1, 0.5) > selection_score(lucky, 1, 0.5)


def test_searching_more_variants_costs_score() -> None:
    trades = [_trade(0.01 + 0.002 * (index % 2)) for index in range(10)]

    one = selection_score(trades, 1, 0.5)
    many = selection_score(trades, 100, 0.5)

    assert one - many == pytest.approx(0.5 * math.log(100))


def test_an_edge_that_fades_on_unseen_history_is_penalized() -> None:
    seen = [_trade(0.02 + 0.002 * (index % 2)) for index in range(20)]
    held = [_trade(0.02 + 0.002 * (index % 2)) for index in range(8)]
    faded = [_trade(0.002 + 0.002 * (index % 2)) for index in range(8)]

    def score(unseen, overfit_weight):
        return outcome_score(seen, unseen, 1, overfit_weight=overfit_weight, search_weight=0)

    assert score(held, 0.5) > score(faded, 0.5)
    # The overfit term is what separates them beyond the lower unseen t-statistic.
    assert score(faded, 0.5) < score(faded, 0)


def test_near_identical_trades_cannot_blow_up_the_score() -> None:
    seen = [_trade(0.0100), _trade(0.0101), _trade(0.0100)]
    unseen = [_trade(0.02), _trade(0.01), _trade(0.03)]

    score = outcome_score(seen, unseen, 1, overfit_weight=0.5, search_weight=0)

    assert -10 < score < 10


def test_too_few_trades_score_zero() -> None:
    assert t_statistic([0.05]) == 0
    assert selection_score([], 1, 0) == 0


# --- Search -------------------------------------------------------------------------


def _wave_bars(prices: list[float]) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=START + timedelta(hours=index),
            open=price,
            high=price * 1.002,
            low=price * 0.998,
            close=price,
            volume=10,
        )
        for index, price in enumerate(prices)
    ]


def test_edge_ranking_never_looks_at_unseen_history_but_scores_it_afterwards() -> None:
    prices = [100 + 8 * math.sin(index / 9) for index in range(600)]
    entries = [
        StrategyDefinition(
            name="mr",
            description="mr",
            chosen_timeframe="1h",
            template_name="mean_reversion",
            entry_rule="mean_reversion",
            parameters={"z_period": 20, "z_entry": 1.5},
        )
    ]
    tool = BacktestTool(CostAssumptions(), 10_000, walk_forward_split=0.7)

    def measure(bars):
        return measure_template(
            TemplateJob(tool, entries, bars, {"1h": bars}, 10_000, min_trades=1, ranking="edge")
        )

    calm = measure(_wave_bars(prices))
    # Same in-sample history; the held-out part slides steadily down.
    crashed = measure(
        _wave_bars(prices[:420] + [price - 0.2 * index for index, price in enumerate(prices[420:])])
    )

    assert calm["strategy"] == crashed["strategy"]
    assert calm["selection_score"] == crashed["selection_score"]
    assert calm["unseen"]["bars"] == 180
    assert calm["unseen"]["net_pnl"] != crashed["unseen"]["net_pnl"]
    # A strategy that kept making money on unseen history must not score as a disaster.
    assert calm["unseen"]["net_pnl"] > 0 and calm["outcome_score"] > -5


# --- Memory and bandit --------------------------------------------------------------


def test_arm_records_keep_a_running_mean_once_per_run() -> None:
    database = Database(":memory:")
    try:
        for run_id, score in (("a", 1.0), ("b", 3.0), ("b", 99.0)):
            record_arm_outcomes(
                database, run_id, "spot", "trending", [("supertrend_atr", "1h", score)]
            )
        record = arm_stats(database, "spot", "trending")[("supertrend_atr", "1h")]
        assert record["runs"] == 2
        assert record["mean"] == pytest.approx(2.0)
        assert record["std"] == pytest.approx(math.sqrt(2))
        assert arm_stats(database, "perpetual", "trending") == {}
    finally:
        database.close()


def test_prior_favours_proven_arms_and_still_explores_new_ones() -> None:
    def bonus(record):
        return prior_bonus(record, weight=1, exploration=0.5, prior_strength=5)

    untried = bonus(None)
    proven = bonus({"runs": 20, "mean": 2.0})
    failing = bonus({"runs": 20, "mean": -2.0})

    assert proven > untried > failing
    assert untried == pytest.approx(0.5)  # exploration only
    # Little evidence moves the prior little.
    assert bonus({"runs": 1, "mean": 2.0}) < proven


def _candidate(template: str, selection: float, *, timeframe: str = "1h", outcome=None):
    return {
        "strategy": _strategy(template, timeframe),
        "selection_score": selection,
        "outcome_score": outcome,
        "net_pnl": selection * 100,
        "max_drawdown": 0.01,
        "trades": 10,
    }


def test_shortlist_orders_by_edge_plus_track_record() -> None:
    settings = Settings(bandit_weight=1, bandit_exploration=0, bandit_prior_strength=1)
    arms = {("mean_reversion", "1h"): {"runs": 50, "mean": 3.0}}
    candidates = [_candidate("supertrend_atr", 1.0), _candidate("mean_reversion", 0.5)]

    with_memory = _shortlist(candidates, settings)
    assert [item["strategy"].template_name for item in with_memory][0] == "supertrend_atr"
    with_memory = _shortlist(candidates, settings, arms)
    assert [item["strategy"].template_name for item in with_memory][0] == "mean_reversion"

    legacy = _shortlist(candidates, settings.model_copy(update={"selection_score": "net_pnl"}))
    assert legacy[0]["strategy"].template_name == "supertrend_atr"
    off = settings.model_copy(update={"bandit_memory": False})
    assert _shortlist(candidates, off, arms)[0]["strategy"].template_name == "supertrend_atr"


def test_model_sees_edge_score_and_track_record_but_never_unseen_results() -> None:
    settings = Settings()
    item = _candidate("supertrend_atr", 1.23456, outcome=2.0)
    item["unseen"] = {"net_pnl": 50}
    arms = {("supertrend_atr", "1h"): {"runs": 4, "mean": 0.75}}

    fields = _learning_fields(item, settings, arms)

    assert fields == {
        "edge_score": 1.235,
        "track_record": {"runs": 4, "mean_unseen_score": 0.75},
    }
    off = settings.model_copy(update={"selection_score": "net_pnl", "bandit_memory": False})
    assert _learning_fields(item, off, arms) == {}


# --- Decision log -------------------------------------------------------------------


def test_decisions_record_regret_and_feed_the_report() -> None:
    database = Database(":memory:")
    settings = Settings(database_path=Path(":memory:"))
    node = StrategyBuilderNode(SimpleNamespace(settings=settings, database=database))
    offered = [
        {**_candidate("supertrend_atr", 2.0, outcome=0.5), "candidate_id": "c1"},
        {**_candidate("mean_reversion", 1.0, outcome=1.75), "candidate_id": "c2"},
        {**_candidate("bollinger_squeeze", 0.5, outcome=None), "candidate_id": "c3"},
    ]
    try:
        for attempt, picked in enumerate(("c1", "c2")):
            state = GraphState(
                run_id="run",
                symbol="BTC/USDT",
                timeframes_to_consider=["1h"],
                revision_count=attempt,
            )
            chosen = next(item for item in offered if item["candidate_id"] == picked)
            check = node._log_decision(
                state, "trending", chosen["strategy"], {"candidate_id": picked}, offered
            )
        assert check == {
            "candidate_id": "c2",
            "chosen_rank": 2,
            "offered": 3,
            "chosen_score": 1.75,
            "best_score": 1.75,
            "best_template": "mean_reversion",
            "best_timeframe": "1h",
            "regret": 0.0,
        }
        report = selection_report(database)
        assert report["overall"]["decisions"] == 2
        assert report["overall"]["mean_regret"] == pytest.approx((1.25 + 0) / 2)
        assert report["overall"]["picked_best"] == 1
        assert report["overall"]["picked_top_ranked"] == 1
        assert list(report["by_model"]) == [settings.model_for("strategy").model]
    finally:
        database.close()
