"""A prior from past runs for ordering strategy candidates (an upper-confidence bandit).

Each arm is a regime, template, and timeframe on one market type. Its record is how many
runs measured it and its mean score on unseen history. The prior shrinks that mean toward
zero until there is evidence (``prior_strength`` runs count as much as the record), then
adds an exploration bonus that fades as the arm is tried, so untried arms still get shown.

The prior is in the same units as the edge score, so it is simply added to it. It only
orders which candidates are offered; the critique's gates still judge the choice.
"""

import math
from collections.abc import Mapping


def prior_bonus(
    record: Mapping[str, float] | None,
    *,
    weight: float,
    exploration: float,
    prior_strength: float,
) -> float:
    runs = float(record["runs"]) if record else 0.0
    mean = float(record["mean"]) if record else 0.0
    shrunk = runs / (runs + prior_strength) * mean
    return weight * (shrunk + exploration / math.sqrt(runs + 1))
