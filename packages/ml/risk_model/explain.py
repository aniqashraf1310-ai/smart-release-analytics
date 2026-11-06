"""
Per-release explanations for a random forest.

Method: tree-path decomposition (Saabas, 2014; also known as "treeinterpreter").
For every tree, walking from the root to the leaf a sample lands in, each split
changes the predicted failure share of the node. That change is credited to the
feature used at the split. Summed over the path:

    tree_prediction = root_value + sum(feature contributions)

Averaging over all trees gives an exact additive decomposition of the forest's
raw vote into  base_value + contribution per feature. This is the same idea as
SHAP's TreeExplainer but cheaper and dependency-free; SHAP values would spread
credit more fairly across interacting features, which is noted as future work.

The calibrated probability shown to users is a monotone (isotonic) mapping of
this raw vote, so the *direction and ranking* of drivers carry over directly.
"""
from __future__ import annotations

import numpy as np


def _positive_value(tree) -> np.ndarray:
    v = tree.tree_.value[:, 0, :]
    return v[:, 1] / v.sum(axis=1)


def forest_contributions(forest, X: np.ndarray) -> tuple[np.ndarray, float]:
    """Return (contributions[n_samples, n_features], base_value) for class 1."""
    X = np.asarray(X, dtype=np.float32)
    n, m = X.shape
    total = np.zeros((n, m))
    base = 0.0
    for est in forest.estimators_:
        pos = _positive_value(est)
        feat = est.tree_.feature
        paths = est.decision_path(X)
        base += pos[0]
        for i in range(n):
            nodes = paths.indices[paths.indptr[i]:paths.indptr[i + 1]]  # root..leaf (ids ascend along a path)
            parents, children = nodes[:-1], nodes[1:]
            np.add.at(total[i], feat[parents], pos[children] - pos[parents])
    k = len(forest.estimators_)
    return total / k, base / k


def pipeline_contributions(pipelines, X_df, feature_groups: dict[str, list[int]]):
    """
    Average contributions over several fitted Pipeline(preprocess -> RandomForest)
    objects (one per calibration fold) and aggregate one-hot columns back to the
    original feature they came from.

    Returns (contributions[n_samples, n_original_features], base_value, names).
    """
    names = list(feature_groups)
    acc = None
    base = 0.0
    for pipe in pipelines:
        pre, rf = pipe[:-1], pipe[-1]
        Xt = pre.transform(X_df)
        Xt = Xt.toarray() if hasattr(Xt, "toarray") else Xt
        c, b = forest_contributions(rf, Xt)
        agg = np.stack([c[:, idx].sum(axis=1) for idx in feature_groups.values()], axis=1)
        acc = agg if acc is None else acc + agg
        base += b
    k = len(pipelines)
    return acc / k, base / k, names


def linear_contributions(pipelines, X_df, feature_groups: dict[str, list[int]], train_distribution: dict):
    """
    Explanation for a (calibrated) logistic regression.

    Each feature's contribution is  coef * (x - typical_x)  in log-odds, where
    typical_x is the training mean (0 after standard scaling; the category share
    for one-hot columns). To show the same unit as the forest explanation, the
    log-odds are converted to percentage points with the local slope p(1-p) of
    the logistic curve - an approximation that is exact for small changes.
    """
    names = list(feature_groups)
    acc = None
    base = 0.0
    for pipe in pipelines:
        pre, lr = pipe[:-1], pipe[-1]
        cols = list(pre.get_feature_names_out())
        ref = np.zeros(len(cols))
        for j, col in enumerate(cols):
            if col.startswith("cat__"):
                for feat, spec in train_distribution.items():
                    prefix = f"cat__{feat}_"
                    if spec["kind"] == "categorical" and col.startswith(prefix):
                        ref[j] = spec["proportions"][col[len(prefix):]]
        Xt = np.asarray(pre.transform(X_df), dtype=float)
        logit_c = lr.coef_[0] * (Xt - ref)
        p = lr.predict_proba(Xt)[:, 1]
        slope = (p * (1 - p))[:, None]
        agg = np.stack([logit_c[:, idx].sum(axis=1) for idx in feature_groups.values()], axis=1) * slope
        acc = agg if acc is None else acc + agg
        base += float(1 / (1 + np.exp(-(lr.intercept_[0] + lr.coef_[0] @ ref))))
    k = len(pipelines)
    return acc / k, base / k, names
