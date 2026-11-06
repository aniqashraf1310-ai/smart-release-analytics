"""
Unit tests for the predictive risk engine.

Run with: python -m pytest packages/ml/tests A small model is trained in
memory so the tests never depend on a saved artifact.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sklearn.calibration import CalibratedClassifierCV  # noqa: E402

import train as T  # noqa: E402
from risk_model import features as F  # noqa: E402
from risk_model.engine import RiskEngine  # noqa: E402
from risk_model.synthetic import LABEL_COL, TRUE_PROB_COL, generate  # noqa: E402

RISKY = dict(change_type="hotfix", target_env="production", patch_size_loc=2500, files_changed=40,
             test_coverage=30, ticket_complexity=5, module_failure_rate=0.4, has_db_migration=True,
             dependency_changes=3, target_date="2025-10-10")          # a Friday
SAFE = dict(change_type="config", target_env="development", patch_size_loc=15, files_changed=1,
            test_coverage=95, ticket_complexity=1, module_failure_rate=0.01, has_db_migration=False,
            dependency_changes=0, target_date="2025-10-14")           # a Tuesday


def _engine(kind="random_forest"):
    df = generate(n=2500, seed=1)
    X, y = df[F.FEATURES], df[LABEL_COL]
    base = T.make_rf(n_estimators=60) if kind == "random_forest" else T.make_lr()
    model = CalibratedClassifierCV(base, method="sigmoid", cv=3).fit(X, y)
    p = model.predict_proba(X)[:, 1]
    meta = {"version_tag": f"test-{kind}", "model_kind": kind,
            "thresholds": T.choose_thresholds(p),
            "feature_groups": T.feature_groups(model.calibrated_classifiers_[0].estimator),
            "train_distribution": T.train_distribution(X)}
    return RiskEngine({"model": model, "meta": meta})


@pytest.fixture(scope="module")
def engine():
    return _engine("random_forest")


# ------------------------------------------------------------------ features
def test_friday_derived_from_target_date():
    assert F.from_release(RISKY)["friday_or_weekend"] == 1
    assert F.from_release(SAFE)["friday_or_weekend"] == 0


def test_invalid_features_rejected():
    for bad in ({"test_coverage": 140}, {"target_env": "moon"}, {"ticket_complexity": 9}):
        with pytest.raises(ValueError):
            F.from_release({**SAFE, **bad})


# ------------------------------------------------------------------ synthetic data
def test_synthetic_is_reproducible_and_not_trivial():
    a, b = generate(n=500, seed=3), generate(n=500, seed=3)
    assert a.equals(b)
    d = generate(n=4000, seed=3)
    assert 0.10 < d[LABEL_COL].mean() < 0.30
    from sklearn.metrics import roc_auc_score
    ceiling = roc_auc_score(d[LABEL_COL], d[TRUE_PROB_COL])
    assert ceiling < 0.85, "labels should not be (nearly) a deterministic rule of the features"


# ------------------------------------------------------------------ scoring
def test_scores_are_probabilities_and_ordered(engine):
    risky, safe = engine.score(F.from_release(RISKY)), engine.score(F.from_release(SAFE))
    assert 0 <= safe["probability"] <= 1 and 0 <= risky["probability"] <= 1
    assert risky["probability"] > safe["probability"]
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert order.index(risky["tier"]) > order.index(safe["tier"])


def test_tiers_monotonic(engine):
    t = engine.thresholds
    assert t["medium"] < t["high"] < t["critical"]
    ps = np.linspace(0, 1, 101)
    idx = [["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(engine.tier(p)) for p in ps]
    assert idx == sorted(idx)


def test_forest_explanation_is_exactly_additive(engine):
    feats = F.from_release(RISKY)
    exp = engine.explain(feats)
    total = exp["base_value_pct"] + sum(d["contribution_pp"] for d in exp["contributions"])
    pipes = [cc.estimator for cc in engine.model.calibrated_classifiers_]
    raw = np.mean([p.predict_proba(engine._frame([feats]))[0, 1] for p in pipes]) * 100
    assert abs(total - raw) < 0.1
    assert {d["feature"] for d in exp["contributions"]} == set(F.FEATURES)


def test_top_driver_is_plausible(engine):
    drivers = [d["feature"] for d in engine.explain(F.from_release(RISKY))["top_risk_drivers"]]
    assert set(drivers) & {"test_coverage", "patch_size_loc", "module_failure_rate", "files_changed"}


def test_linear_model_explanations():
    lr = _engine("logistic_regression")
    exp = lr.explain(F.from_release(RISKY))
    assert exp["top_risk_drivers"], "a risky release should have positive drivers"
    assert lr.score(F.from_release(RISKY))["probability"] > lr.score(F.from_release(SAFE))["probability"]


# ------------------------------------------------------------------ what-if and mitigations
def test_what_if_better_coverage_lowers_risk(engine):
    res = engine.what_if(F.from_release(RISKY), {"test_coverage": 95})
    assert res["delta_pp"] < 0


def test_what_if_rejects_invalid_override(engine):
    with pytest.raises(ValueError):
        engine.what_if(F.from_release(RISKY), {"test_coverage": -5})


def test_mitigations_reduce_risk(engine):
    sugg = engine.suggest_mitigations(F.from_release(RISKY))
    assert sugg and all(s["delta_pp"] < 0 for s in sugg)
    assert [s["delta_pp"] for s in sugg] == sorted(s["delta_pp"] for s in sugg)


# ------------------------------------------------------------------ drift
def test_psi_detects_shift(engine):
    same = generate(n=400, seed=11)[F.FEATURES].to_dict("records")
    assert engine.psi(same)["features"]["test_coverage"]["status"] == "stable"
    shifted = generate(n=400, seed=11)
    shifted["test_coverage"] = shifted["test_coverage"] * 0.5
    assert engine.psi(shifted[F.FEATURES].to_dict("records"))["features"]["test_coverage"]["status"] == "retrain"


def test_psi_needs_enough_data(engine):
    assert engine.psi([F.from_release(SAFE)] * 5)["overall"] == "insufficient data"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
