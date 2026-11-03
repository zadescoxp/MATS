"""What strategy selection has learned, kept in the database so any model can use it.

Two records, both written by code from measured outcomes, never from an agent's verdict:

- ``strategy_arms``: for each market type, regime, template, and timeframe, how many runs
  measured it and its mean score on unseen history (Welford's running mean and variance).
  ``framework.strategies.bandit`` turns these into a prior for ordering candidates.
- ``selection_decisions``: every strategy choice, the candidate picked, its score on unseen
  history, the best score among the candidates offered, and the regret between them. Live
  results join in through ``strategy_trades`` by run.

Dropping these tables, this module, and the hooks that call it removes the feature.
"""

import math
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_arms (
    market TEXT NOT NULL,
    regime TEXT NOT NULL,
    template_name TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    runs INTEGER NOT NULL,
    mean_score REAL NOT NULL,
    m2 REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (market, regime, template_name, timeframe)
);
CREATE TABLE IF NOT EXISTS strategy_arm_runs (
    run_id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selection_decisions (
    run_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    regime TEXT NOT NULL,
    model TEXT,
    template_name TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    chosen_rank INTEGER NOT NULL,
    offered INTEGER NOT NULL,
    chosen_score REAL,
    best_score REAL,
    best_template TEXT,
    best_timeframe TEXT,
    regret REAL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, attempt)
);
"""


def record_arm_outcomes(
    database: Any,
    run_id: str,
    market: str,
    regime: str,
    outcomes: list[tuple[str, str, float]],
) -> bool:
    """Add one run's (template, timeframe, unseen score) results; once per run."""
    now = _now()
    with database._lock, database.connection:
        inserted = database.connection.execute(
            "INSERT OR IGNORE INTO strategy_arm_runs (run_id, recorded_at) VALUES (?, ?)",
            (run_id, now),
        ).rowcount
        if not inserted:
            return False  # a resumed or revised run measured the same candles again
        for template_name, timeframe, score in outcomes:
            row = database.connection.execute(
                """SELECT runs, mean_score, m2 FROM strategy_arms
                   WHERE market = ? AND regime = ? AND template_name = ? AND timeframe = ?""",
                (market, regime, template_name, timeframe),
            ).fetchone()
            runs, mean, m2 = (row["runs"], row["mean_score"], row["m2"]) if row else (0, 0.0, 0.0)
            runs += 1
            delta = score - mean
            mean += delta / runs
            m2 += delta * (score - mean)
            database.connection.execute(
                """INSERT INTO strategy_arms
                   (market, regime, template_name, timeframe, runs, mean_score, m2, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, regime, template_name, timeframe) DO UPDATE SET
                       runs = excluded.runs,
                       mean_score = excluded.mean_score,
                       m2 = excluded.m2,
                       updated_at = excluded.updated_at""",
                (market, regime, template_name, timeframe, runs, mean, m2, now),
            )
    return True


def arm_stats(database: Any, market: str, regime: str) -> dict[tuple[str, str], dict[str, float]]:
    """(template, timeframe) -> runs, mean, and standard deviation of the unseen score."""
    with database._lock:
        rows = database.connection.execute(
            """SELECT template_name, timeframe, runs, mean_score, m2 FROM strategy_arms
               WHERE market = ? AND regime = ?""",
            (market, regime),
        ).fetchall()
    return {
        (row["template_name"], row["timeframe"]): {
            "runs": row["runs"],
            "mean": row["mean_score"],
            "std": math.sqrt(row["m2"] / (row["runs"] - 1)) if row["runs"] > 1 else 0.0,
        }
        for row in rows
    }


def record_decision(database: Any, decision: dict[str, Any]) -> None:
    columns = (
        "run_id",
        "attempt",
        "symbol",
        "market",
        "regime",
        "model",
        "template_name",
        "timeframe",
        "candidate_id",
        "chosen_rank",
        "offered",
        "chosen_score",
        "best_score",
        "best_template",
        "best_timeframe",
        "regret",
    )
    values = [decision.get(column) for column in columns]
    with database._lock, database.connection:
        database.connection.execute(
            f"""INSERT OR REPLACE INTO selection_decisions ({", ".join(columns)}, created_at)
                VALUES ({", ".join("?" for _ in columns)}, ?)""",
            (*values, _now()),
        )


def decisions(database: Any) -> list[dict[str, Any]]:
    """Every recorded choice, with the live trades its run has closed since."""
    with database._lock:
        rows = database.connection.execute(
            """SELECT d.*, c.decision AS critique,
                      (SELECT COUNT(*) FROM strategy_trades t WHERE t.run_id = d.run_id)
                          AS live_trades,
                      (SELECT SUM(net_pnl) FROM strategy_trades t WHERE t.run_id = d.run_id)
                          AS live_net_pnl
               FROM selection_decisions d
               LEFT JOIN critiques c ON c.critique_id = d.run_id || '-v' || d.attempt
               ORDER BY d.created_at"""
        ).fetchall()
    return [dict(row) for row in rows]


def selection_report(database: Any) -> dict[str, Any]:
    """How well choices have done: regret overall and by model, approvals and live results."""
    rows = decisions(database)
    scored = [row for row in rows if row["regret"] is not None]

    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        regrets = [item["regret"] for item in items if item["regret"] is not None]
        return {
            "decisions": len(items),
            "mean_regret": sum(regrets) / len(regrets) if regrets else None,
            "picked_best": sum(regret <= 1e-9 for regret in regrets),
            "picked_top_ranked": sum(item["chosen_rank"] == 1 for item in items),
            "mean_chosen_score": _mean(item["chosen_score"] for item in items),
            "mean_best_score": _mean(item["best_score"] for item in items),
        }

    models = sorted({row["model"] or "unknown" for row in rows})
    approved = [row for row in rows if row["critique"] == "APPROVE"]
    traded = [row for row in approved if row["live_trades"]]
    return {
        "overall": summary(scored),
        "by_model": {
            model: summary([row for row in scored if (row["model"] or "unknown") == model])
            for model in models
        },
        "approved": len(approved),
        "approved_with_live_trades": len(traded),
        "live_net_pnl": sum(row["live_net_pnl"] or 0.0 for row in traded),
    }


def _mean(values: Any) -> float | None:
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _now() -> str:
    return datetime.now(UTC).isoformat()
