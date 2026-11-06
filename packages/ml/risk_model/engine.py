"""
Runtime risk engine used by the API.

    engine = RiskEngine.load("artifacts/latest.joblib")
    result = engine.score(features.from_release(release_row))

A loaded model bundle contains the calibrated classifier plus metadata written
by train.py: version tag, tier thresholds, feature groups for explanations and
the training distribution used for drift (PSI) monitoring.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import joblib
import numpy as np
import pandas as pd

from . import features as F
from .explain import linear_contributions, pipeline_contributions

TIERS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class RiskEngine:
    def __init__(self, bundle: dict):
        self.model = bundle["model"]
        self.meta = bundle["meta"]
        self.thresholds = self.meta["thresholds"]
        self.version_tag = self.meta["version_tag"]

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path) -> "RiskEngine":
        path = Path(path)
        if path.suffix == ".json":  # pointer file written by train.py
            import json
            path = path.parent / json.loads(path.read_text())["artifact"]
        return cls(joblib.load(path))

    # ------------------------------------------------------------------ scoring
    def _frame(self, rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
        return pd.DataFrame(list(rows), columns=F.FEATURES)

    def predict_proba(self, rows: Iterable[Mapping[str, Any]]) -> np.ndarray:
        return self.model.predict_proba(self._frame(rows))[:, 1]

    def tier(self, p: float) -> str:
        t = self.thresholds
        if p >= t["critical"]:
            return "CRITICAL"
        if p >= t["high"]:
            return "HIGH"
        if p >= t["medium"]:
            return "MEDIUM"
        return "LOW"

    def explain(self, feats: Mapping[str, Any]) -> dict:
        pipes = [cc.estimator for cc in self.model.calibrated_classifiers_]
        if self.meta.get("model_kind", "random_forest") == "random_forest":
            contrib, base, names = pipeline_contributions(pipes, self._frame([feats]),
                                                          self.meta["feature_groups"])
            method = "tree-path decomposition (Saabas) on the random forest's raw vote"
            unit = "percentage points of the forest's raw failure vote"
        else:
            contrib, base, names = linear_contributions(pipes, self._frame([feats]),
                                                        self.meta["feature_groups"],
                                                        self.meta["train_distribution"])
            method = "logistic-regression coefficients x deviation from a typical release"
            unit = "approximate percentage points (local slope of the logistic curve)"
        items = [{
            "feature": name,
            "label": F.LABELS[name],
            "value": feats[name],
            "contribution_pp": round(float(contrib[0, i]) * 100, 2),
        } for i, name in enumerate(names)]
        items.sort(key=lambda d: abs(d["contribution_pp"]), reverse=True)
        return {
            "method": method,
            "unit": unit,
            "base_value_pct": round(base * 100, 2),
            "contributions": items,
            "top_risk_drivers": [d for d in items if d["contribution_pp"] > 0][:3],
            "top_protective_factors": [d for d in items if d["contribution_pp"] < 0][:3],
        }

    def score(self, feats: Mapping[str, Any]) -> dict:
        F.validate(feats)
        p = float(self.predict_proba([feats])[0])
        return {
            "probability": round(p, 5),
            "success_probability": round(1 - p, 5),
            "tier": self.tier(p),
            "model_version": self.version_tag,
            "explanation": self.explain(feats),
        }

    # ------------------------------------------------------------------ what-if
    def what_if(self, feats: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict:
        scenario = {**feats, **overrides}
        F.validate(scenario)
        p0, p1 = self.predict_proba([feats, scenario])
        return {
            "baseline": {"probability": round(float(p0), 5), "tier": self.tier(float(p0))},
            "scenario": {"probability": round(float(p1), 5), "tier": self.tier(float(p1)),
                         "features": scenario},
            "delta_pp": round(float(p1 - p0) * 100, 2),
        }

    def suggest_mitigations(self, feats: Mapping[str, Any], limit: int = 4) -> list[dict]:
        """Single, realistic changes a developer could make, ranked by risk reduction."""
        cands: list[tuple[str, dict]] = []
        if feats["test_coverage"] < 80:
            cands.append(("Raise test coverage to 80%", {"test_coverage": 80.0}))
        elif feats["test_coverage"] < 90:
            cands.append(("Raise test coverage to 90%", {"test_coverage": 90.0}))
        if feats["patch_size_loc"] > 200:
            cands.append(("Split the patch in half", {
                "patch_size_loc": max(1, feats["patch_size_loc"] // 2),
                "files_changed": max(1, feats["files_changed"] // 2)}))
        if feats["friday_or_weekend"]:
            cands.append(("Deploy on a Monday-Thursday", {"friday_or_weekend": 0}))
        if feats["dependency_changes"] > 0:
            cands.append(("Ship dependency upgrades separately", {"dependency_changes": 0}))
        if feats["has_db_migration"]:
            cands.append(("Ship the DB migration as a separate release", {"has_db_migration": 0}))
        if not cands:
            return []
        base = float(self.predict_proba([feats])[0])
        probs = self.predict_proba([{**feats, **o} for _, o in cands])
        out = [{"action": a, "changes": o, "probability": round(float(p), 5),
                "tier": self.tier(float(p)), "delta_pp": round(float(p - base) * 100, 2)}
               for (a, o), p in zip(cands, probs) if p < base]
        out.sort(key=lambda d: d["delta_pp"])
        return out[:limit]

    # ------------------------------------------------------------------ drift
    def psi(self, rows: list[Mapping[str, Any]]) -> dict:
        """
        Population Stability Index of recent inputs against the training data.
        Rule of thumb: < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 retrain.
        """
        ref = self.meta["train_distribution"]
        df = self._frame(rows)
        eps = 1e-4
        result = {}
        for name, spec in ref.items():
            if spec["kind"] == "categorical":
                expected = np.array([spec["proportions"][c] for c in spec["categories"]])
                actual = np.array([(df[name] == c).mean() for c in spec["categories"]])
            else:
                edges = np.array(spec["edges"])
                expected = np.array(spec["proportions"])
                counts, _ = np.histogram(np.clip(df[name].astype(float), edges[0], edges[-1]), bins=edges)
                actual = counts / max(1, counts.sum())
            e, a = np.clip(expected, eps, None), np.clip(actual, eps, None)
            value = float(np.sum((a - e) * np.log(a / e)))
            result[name] = {"psi": round(value, 4),
                            "status": "stable" if value < 0.1 else "moderate" if value < 0.25 else "retrain"}
        worst = max(result.values(), key=lambda d: d["psi"]) if result else None
        return {"n": len(rows), "features": result,
                "overall": worst["status"] if worst and len(rows) >= 30 else "insufficient data"}

