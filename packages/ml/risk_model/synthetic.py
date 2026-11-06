"""
Synthetic deployment-history generator.

Real fintech deployment logs are confidential, so (as described in Section 3.3
of the report) a synthetic dataset is generated that mimics patterns reported
in the literature: bigger patches, lower coverage, production targets, hotfixes,
DB migrations and historically fragile modules fail more often.

Avoiding circularity
--------------------
If labels were a deterministic rule of the observed features, any model would
simply rediscover that rule and high accuracy would prove nothing. To keep the
task honest the generator adds:

  * hidden (latent) factors the model never sees - team experience and
    infrastructure instability - which shift failure probability;
  * per-record Gaussian noise on the log-odds;
  * a small fraction of flipped labels (mislabelled outcomes, as in real logs).

The true failure probability of each record is written to the column
``_true_probability``. It is never used as a feature; training uses it only to
report the *Bayes ceiling* - the best ROC-AUC any model could reach on this
data - so model scores can be judged relative to what is achievable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import CATEGORICAL

TRUE_PROB_COL = "_true_probability"
LABEL_COL = "failed"


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def generate(n: int = 6000, seed: int = 42, label_noise: float = 0.03) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    change_type = rng.choice(CATEGORICAL["change_type"], size=n, p=[0.20, 0.45, 0.20, 0.15])
    target_env = rng.choice(CATEGORICAL["target_env"], size=n, p=[0.15, 0.30, 0.55])

    loc = np.clip(np.round(rng.lognormal(mean=5.0, sigma=1.1, size=n)), 1, 20_000).astype(int)
    files = np.maximum(1, np.round(loc / 40 * rng.lognormal(0, 0.5, size=n))).astype(int)
    coverage = np.round(rng.beta(5, 2.5, size=n) * 100, 1)
    # Complexity correlates with patch size.
    cx_score = np.log10(loc) + rng.normal(0, 0.6, size=n)
    complexity = np.clip(np.round(1 + (cx_score - 0.5) * 1.2), 1, 5).astype(int)
    module_fail = np.round(rng.beta(1.5, 10, size=n), 3)
    db_migration = (rng.random(n) < np.where(change_type == "feature", 0.20, 0.07)).astype(int)
    deps = rng.poisson(0.6, size=n)
    fri_weekend = (rng.random(n) < 0.25).astype(int)

    # Hidden factors - not available to the model.
    team_experience = rng.normal(0, 1, size=n)
    infra_instability = rng.normal(0, 1, size=n)

    s = np.log10(loc)            # 0 .. 4.3
    gap = 1 - coverage / 100     # untested share of the code
    logit = (
        -5.7
        + 0.45 * s
        + 1.6 * gap
        + 0.9 * s * gap                                  # large AND poorly tested = much riskier
        + np.select([target_env == "production", target_env == "staging"], [0.8, 0.25], 0.0)
        + np.select([change_type == "hotfix", change_type == "refactor", change_type == "config"],
                    [0.55, 0.25, -0.25], 0.0)
        + 0.22 * (complexity - 3)
        + 3.2 * module_fail
        + 0.65 * db_migration
        + 0.18 * deps
        + 0.35 * fri_weekend
        + 0.10 * np.log1p(files)
        - 0.55 * team_experience                         # hidden
        + 0.45 * infra_instability                       # hidden
        + rng.normal(0, 0.35, size=n)                    # irreducible noise
    )
    p = _sigmoid(logit)
    y = (rng.random(n) < p).astype(int)
    flip = rng.random(n) < label_noise
    y = np.where(flip, 1 - y, y)

    return pd.DataFrame({
        "change_type": change_type,
        "target_env": target_env,
        "patch_size_loc": loc,
        "files_changed": files,
        "test_coverage": coverage,
        "ticket_complexity": complexity,
        "module_failure_rate": module_fail,
        "has_db_migration": db_migration,
        "dependency_changes": deps,
        "friday_or_weekend": fri_weekend,
        LABEL_COL: y,
        TRUE_PROB_COL: np.round(p, 5),
    })
