"""
Train, evaluate and package the deployment-risk model.

Pipeline (maps to Section 3.3 / 5.2 of the report):
  1. Load data: synthetic history (+ optional real outcomes exported by the API).
  2. Hold out 20% as a test set (stratified).
  3. Random-forest hyper-parameters tuned by grid search (5-fold, PR-AUC).
  4. Stratified 5-fold CV on the training 80%: logistic regression (baseline)
     vs tuned random forest. Metrics: ROC-AUC, PR-AUC (average precision),
     Brier score, F1 at 0.5. PR-AUC is the headline metric because failures
     are the minority class.
  5. Model selection rule (fixed before looking at the test set): the model with
     the higher mean CV PR-AUC wins; if the gap is smaller than one standard
     deviation the models are treated as tied and the random forest is kept,
     as pre-registered in Section 3.3 of the report.
  6. The selected model is wrapped in sigmoid (Platt) calibration, so a displayed
     "35% failure risk" really means ~35% of such releases fail.
  7. Tier thresholds from out-of-fold predictions (never the test set), sized to
     review capacity: LOW = safest 50%, MEDIUM = next 30%, HIGH = next 15%,
     CRITICAL = riskiest 5%. The cost-optimal single cut-off (missed failure =
     10x an unnecessary review) is reported alongside for discussion.
  8. Test-set report + figures + model bundle for the API.

Usage:
    python train.py                         # synthetic data only
    python train.py --extra outcomes.csv    # add real outcomes exported from the app
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             f1_score, precision_recall_curve, precision_score, recall_score,
                             roc_auc_score)
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_val_predict, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from risk_model import features as F
from risk_model.synthetic import LABEL_COL, TRUE_PROB_COL, generate

HERE = Path(__file__).resolve().parent
SEED = 42
COST_FN, COST_FP = 10.0, 1.0      # a missed failure is 10x worse than an unnecessary review


def preprocessor(scale: bool) -> ColumnTransformer:
    return ColumnTransformer([
        ("cat", OneHotEncoder(categories=[F.CATEGORICAL[c] for c in F.CATEGORICAL],
                              handle_unknown="ignore", sparse_output=False), list(F.CATEGORICAL)),
        ("num", StandardScaler() if scale else "passthrough", F.NUMERIC),
    ])


def make_lr() -> Pipeline:
    return Pipeline([("pre", preprocessor(scale=True)),
                     ("clf", LogisticRegression(max_iter=2000, C=1.0))])


def make_rf(**params) -> Pipeline:
    clf = RandomForestClassifier(n_estimators=300, min_samples_leaf=20, max_features="sqrt",
                                 n_jobs=-1, random_state=SEED)
    clf.set_params(**params)
    return Pipeline([("pre", preprocessor(scale=False)), ("clf", clf)])


RF_GRID = {"min_samples_leaf": [20, 40, 80], "max_features": ["sqrt", 0.5], "max_depth": [None, 8]}
TIER_SHARES = {"LOW": 0.50, "MEDIUM": 0.30, "HIGH": 0.15, "CRITICAL": 0.05}


def feature_groups(pipe: Pipeline) -> dict[str, list[int]]:
    names = list(pipe[:-1].get_feature_names_out())
    groups = {}
    for feat in F.FEATURES:
        prefix = f"cat__{feat}_" if feat in F.CATEGORICAL else None
        groups[feat] = [i for i, n in enumerate(names)
                        if (prefix and n.startswith(prefix)) or n == f"num__{feat}"]
    return groups


def cost_optimal_threshold(y: np.ndarray, p: np.ndarray) -> dict:
    grid = np.round(np.arange(0.02, 0.96, 0.005), 3)
    cost = np.array([COST_FN * np.sum((p < t) & (y == 1)) + COST_FP * np.sum((p >= t) & (y == 0))
                     for t in grid])
    best = int(np.argmin(cost))
    return {"threshold": float(grid[best]), "cost_fn": COST_FN, "cost_fp": COST_FP,
            "share_flagged": round(float((p >= grid[best]).mean()), 4)}


def choose_thresholds(p: np.ndarray) -> dict:
    """Cut-offs so that tiers hold the intended share of releases (review capacity)."""
    cum = np.cumsum([TIER_SHARES[t] for t in ("LOW", "MEDIUM", "HIGH")])
    medium, high, critical = (float(np.quantile(p, q)) for q in cum)
    return {"medium": round(medium, 4), "high": round(high, 4), "critical": round(critical, 4),
            "design": "review capacity", "target_shares": TIER_SHARES}


def tier_of(p: np.ndarray, t: dict) -> np.ndarray:
    return np.select([p >= t["critical"], p >= t["high"], p >= t["medium"]],
                     ["CRITICAL", "HIGH", "MEDIUM"], "LOW")


def train_distribution(X: pd.DataFrame) -> dict:
    out = {}
    for c, cats in F.CATEGORICAL.items():
        out[c] = {"kind": "categorical", "categories": cats,
                  "proportions": {k: float((X[c] == k).mean()) for k in cats}}
    for c in F.NUMERIC:
        v = X[c].astype(float)
        edges = np.unique(np.quantile(v, np.linspace(0, 1, 11)))
        if len(edges) < 3:                       # binary / low-cardinality feature
            edges = np.array([v.min() - 0.5, (v.min() + v.max()) / 2, v.max() + 0.5])
        counts, _ = np.histogram(v, bins=edges)
        out[c] = {"kind": "numeric", "edges": edges.tolist(), "proportions": (counts / counts.sum()).tolist()}
    return out


def summarise_cv(res: dict) -> dict:
    out = {}
    for k, v in res.items():
        if not k.startswith("test_"):
            continue
        name = k[5:]
        vals = -v if name.startswith("neg_") else v
        out[name.replace("neg_", "")] = {"mean": round(float(vals.mean()), 4), "std": round(float(vals.std()), 4)}
    return out


def plots(outdir: Path, y_test, p_test, p_lr, thresholds, importances):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, grid_c = "#1f2933", "#d9dde3"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": grid_c, "axes.labelcolor": ink,
                         "xtick.color": ink, "ytick.color": ink, "axes.spines.top": False,
                         "axes.spines.right": False})
    maroon, grey = "#7C3AED", "#94A3B8"   # brand violet, slate

    # Reliability diagram
    fig, ax = plt.subplots(figsize=(5, 4.2))
    for p, label, col in ((p_test, "Selected model (calibrated)", maroon),
                          (p_lr, "Logistic regression baseline", grey)):
        fr, mp = calibration_curve(y_test, p, n_bins=10, strategy="quantile")
        ax.plot(mp, fr, marker="o", ms=4, lw=1.8, color=col, label=label)
    ax.plot([0, 1], [0, 1], ls="--", lw=1, color="#9aa5b1", label="Perfect calibration")
    ax.set_xlabel("Predicted failure probability")
    ax.set_ylabel("Observed failure rate")
    ax.set_title("Reliability diagram (test set)", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8)
    ax.grid(color=grid_c, lw=0.6)
    fig.tight_layout(); fig.savefig(outdir / "reliability_diagram.png", dpi=160); plt.close(fig)

    # Precision-recall
    fig, ax = plt.subplots(figsize=(5, 4.2))
    for p, label, col in ((p_test, "Selected model (calibrated)", maroon), (p_lr, "Logistic regression", grey)):
        pr, rc, _ = precision_recall_curve(y_test, p)
        ax.plot(rc, pr, lw=1.8, color=col, label=f"{label} (AP={average_precision_score(y_test, p):.3f})")
    ax.axhline(y_test.mean(), ls="--", lw=1, color="#9aa5b1", label=f"No-skill ({y_test.mean():.3f})")
    ax.set_xlabel("Recall (share of failures caught)")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-recall (test set)", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=8); ax.grid(color=grid_c, lw=0.6)
    fig.tight_layout(); fig.savefig(outdir / "precision_recall.png", dpi=160); plt.close(fig)

    # Failure rate by tier
    tiers = tier_of(p_test, thresholds)
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    rates = [y_test[tiers == t].mean() * 100 if (tiers == t).any() else 0 for t in order]
    counts = [(tiers == t).sum() for t in order]
    fig, ax = plt.subplots(figsize=(5, 3.6))
    bars = ax.bar(order, rates, color=["#DDD6FE", "#C084FC", "#DB2777", maroon], width=0.6)
    for b, r, n in zip(bars, rates, counts):
        ax.text(b.get_x() + b.get_width() / 2, r + 1, f"{r:.0f}%\n(n={n})", ha="center", fontsize=8, color=ink)
    ax.set_ylabel("Actual failure rate (%)")
    ax.set_ylim(0, max(rates) * 1.3 + 5)
    ax.set_title("Observed failure rate per risk tier (test set)", loc="left", fontsize=11)
    ax.grid(axis="y", color=grid_c, lw=0.6)
    fig.tight_layout(); fig.savefig(outdir / "tier_failure_rates.png", dpi=160); plt.close(fig)

    # Permutation importance
    items = sorted(importances.items(), key=lambda kv: kv[1]["mean"])
    fig, ax = plt.subplots(figsize=(5.5, 4))
    ax.barh([F.LABELS[k] for k, _ in items], [v["mean"] for _, v in items],
            xerr=[v["std"] for _, v in items], color=maroon, height=0.6, error_kw={"lw": 0.8})
    ax.set_xlabel("Drop in PR-AUC when feature is shuffled")
    ax.set_title("Global feature importance (permutation, test set)", loc="left", fontsize=11)
    ax.grid(axis="x", color=grid_c, lw=0.6)
    fig.tight_layout(); fig.savefig(outdir / "feature_importance.png", dpi=160); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6000, help="synthetic records")
    ap.add_argument("--extra", type=Path, help="CSV of real outcomes exported by the API")
    ap.add_argument("--out", type=Path, default=HERE / "artifacts")
    ap.add_argument("--reports", type=Path, default=HERE / "reports")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)

    df = generate(n=args.n, seed=SEED)
    df["source"] = "synthetic"
    if args.extra:
        real = pd.read_csv(args.extra)
        real["source"] = "real"
        real[TRUE_PROB_COL] = np.nan
        df = pd.concat([df, real[F.FEATURES + [LABEL_COL, TRUE_PROB_COL, "source"]]], ignore_index=True)
        print(f"added {len(real)} real outcome records")

    X, y = df[F.FEATURES], df[LABEL_COL].to_numpy()
    X_tr, X_te, y_tr, y_te, df_tr, df_te = train_test_split(X, y, df, test_size=0.2, stratify=y, random_state=SEED)

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)

    # ---- 1. tune the random forest
    grid = GridSearchCV(make_rf(), {f"clf__{k}": v for k, v in RF_GRID.items()},
                        scoring="average_precision", cv=cv, n_jobs=-1).fit(X_tr, y_tr)
    rf_params = {k.removeprefix("clf__"): v for k, v in grid.best_params_.items()}
    print("best RF params", rf_params, "CV PR-AUC", round(grid.best_score_, 4))

    # ---- 2. cross-validated comparison
    scoring = {"roc_auc": "roc_auc", "pr_auc": "average_precision", "neg_brier": "neg_brier_score", "f1_at_0_5": "f1"}
    factories = {"logistic_regression": make_lr, "random_forest": lambda: make_rf(**rf_params)}
    cv_results = {}
    for name, factory in factories.items():
        res = cross_validate(factory(), X_tr, y_tr, cv=cv, scoring=scoring, n_jobs=1)
        cv_results[name] = summarise_cv(res)
        print(f"CV {name:20s}", {k: v["mean"] for k, v in cv_results[name].items()})

    # ---- 3. selection rule (decided before the test set is touched)
    lr_s, rf_s = cv_results["logistic_regression"]["pr_auc"], cv_results["random_forest"]["pr_auc"]
    gap = lr_s["mean"] - rf_s["mean"]
    tol = max(lr_s["std"], rf_s["std"])
    if gap > tol:
        selected = "logistic_regression"
        reason = f"LR PR-AUC higher by {gap:.4f} (> 1 SD = {tol:.4f})"
    else:
        selected = "random_forest"
        reason = (f"PR-AUC gap {gap:+.4f} is within 1 SD ({tol:.4f}): models tied, "
                  "random forest kept as pre-registered" if gap > 0 else
                  f"RF PR-AUC higher or equal ({-gap:+.4f})")
    print("selected", selected, "-", reason)

    def make_final():
        return CalibratedClassifierCV(factories[selected](), method="sigmoid", cv=5)

    # ---- 4. tiers from out-of-fold predictions
    oof = cross_val_predict(make_final(), X_tr, y_tr, cv=cv, method="predict_proba")[:, 1]
    thresholds = choose_thresholds(oof)
    cost_opt = cost_optimal_threshold(y_tr, oof)
    print("thresholds", thresholds, "cost-optimal", cost_opt)

    # ---- 5. final fit + held-out evaluation
    final = make_final().fit(X_tr, y_tr)
    lr_final = make_lr().fit(X_tr, y_tr)
    p_te = final.predict_proba(X_te)[:, 1]
    p_lr = lr_final.predict_proba(X_te)[:, 1]
    raw_model = factories[selected]().fit(X_tr, y_tr)
    p_raw = raw_model.predict_proba(X_te)[:, 1]

    flagged = p_te >= thresholds["high"]
    tn, fp, fn, tp = confusion_matrix(y_te, flagged).ravel()
    tiers = tier_of(p_te, thresholds)
    synthetic_mask = (df_te["source"] == "synthetic").to_numpy()
    test_metrics = {
        "n_test": int(len(y_te)),
        "failure_rate": round(float(y_te.mean()), 4),
        "selected_model_calibrated": {
            "model": selected,
            "roc_auc": round(roc_auc_score(y_te, p_te), 4),
            "pr_auc": round(average_precision_score(y_te, p_te), 4),
            "brier": round(brier_score_loss(y_te, p_te), 4),
        },
        "selected_model_uncalibrated_brier": round(brier_score_loss(y_te, p_raw), 4),
        "logistic_regression": {
            "roc_auc": round(roc_auc_score(y_te, p_lr), 4),
            "pr_auc": round(average_precision_score(y_te, p_lr), 4),
            "brier": round(brier_score_loss(y_te, p_lr), 4),
        },
        "bayes_ceiling_roc_auc": round(roc_auc_score(y_te[synthetic_mask],
                                                     df_te.loc[synthetic_mask, TRUE_PROB_COL]), 4),
        "high_or_critical_flagged": {
            "threshold": thresholds["high"],
            "precision": round(precision_score(y_te, flagged), 4),
            "recall": round(recall_score(y_te, flagged), 4),
            "f1": round(f1_score(y_te, flagged), 4),
            "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        },
        "tiers": {t: {"n": int((tiers == t).sum()),
                      "share": round(float((tiers == t).mean()), 4),
                      "observed_failure_rate": round(float(y_te[tiers == t].mean()), 4) if (tiers == t).any() else None}
                  for t in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        # A single cost-optimal cut-off (missed failure = 10x an unnecessary review)
        # would flag most releases, which is why tiers are sized to review capacity.
        "cost_optimal_threshold_oof": cost_opt,
    }

    perm = permutation_importance(final, X_te, y_te, scoring="average_precision", n_repeats=5,
                                  random_state=SEED, n_jobs=1)
    importances = {f: {"mean": round(float(m), 4), "std": round(float(s), 4)}
                   for f, m, s in zip(F.FEATURES, perm.importances_mean, perm.importances_std)}

    plots(args.reports, y_te, p_te, p_lr, thresholds, importances)

    trained_at = dt.datetime.now(dt.timezone.utc)
    tag = ("rf-" if selected == "random_forest" else "lr-") + trained_at.strftime("%Y%m%d-%H%M%S")
    metrics = {"version_tag": tag, "trained_at": trained_at.isoformat(),
               "data": {"n_total": int(len(df)), "n_real": int((df["source"] == "real").sum()),
                        "failure_rate": round(float(y.mean()), 4)},
               "random_forest_tuning": {"grid": RF_GRID, "best_params": rf_params,
                                        "best_cv_pr_auc": round(float(grid.best_score_), 4)},
               "cross_validation_5fold": cv_results,
               "selection": {"selected": selected, "reason": reason},
               "test": test_metrics,
               "thresholds": thresholds, "permutation_importance_pr_auc": importances}

    meta = {"version_tag": tag, "model_kind": selected,
            "algorithm": ("RandomForest" if selected == "random_forest" else "LogisticRegression")
                         + " + sigmoid calibration",
            "trained_at": trained_at.isoformat(), "thresholds": thresholds,
            "feature_groups": feature_groups(final.calibrated_classifiers_[0].estimator),
            "feature_means": {c: float(X_tr[c].mean()) for c in F.NUMERIC},
            "train_distribution": train_distribution(X_tr), "metrics": metrics}
    artifact = f"model-{tag}.joblib"
    joblib.dump({"model": final, "meta": meta}, args.out / artifact, compress=3)
    (args.out / "latest.json").write_text(json.dumps({"artifact": artifact, "version_tag": tag}, indent=2))
    (args.reports / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(test_metrics, indent=2))
    print(f"saved {args.out / artifact}")


if __name__ == "__main__":
    main()
