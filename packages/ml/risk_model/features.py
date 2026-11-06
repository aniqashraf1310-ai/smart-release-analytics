"""
Single source of truth for the model's input features.

Both the training pipeline and the API import this module, so the features a
release is scored on can never silently drift from the ones the model was
trained on.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Mapping

CATEGORICAL = {
    "change_type": ["hotfix", "feature", "config", "refactor"],
    "target_env": ["development", "staging", "production"],
}

NUMERIC = [
    "patch_size_loc",        # lines of code changed
    "files_changed",
    "test_coverage",         # percent, 0-100
    "ticket_complexity",     # 1 (trivial) .. 5 (very complex)
    "module_failure_rate",   # historical failure rate of the affected module, 0-1
    "has_db_migration",      # 0/1
    "dependency_changes",    # number of dependency bumps
    "friday_or_weekend",     # 0/1 derived from target_date
]

FEATURES = list(CATEGORICAL) + NUMERIC

# Human-readable labels used in explanations shown to approvers.
LABELS = {
    "change_type": "Change type",
    "target_env": "Target environment",
    "patch_size_loc": "Patch size (LOC)",
    "files_changed": "Files changed",
    "test_coverage": "Test coverage",
    "ticket_complexity": "Ticket complexity",
    "module_failure_rate": "Module failure history",
    "has_db_migration": "Database migration",
    "dependency_changes": "Dependency changes",
    "friday_or_weekend": "Friday/weekend deployment",
}

# Features a developer can realistically change before release, used by the
# what-if simulator to suggest mitigations.
ACTIONABLE = ["patch_size_loc", "files_changed", "test_coverage", "dependency_changes",
              "friday_or_weekend", "target_env"]


def _to_date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def from_release(release: Mapping[str, Any]) -> dict[str, Any]:
    """Build a validated feature dict from a release record (DB row or API payload)."""
    target = _to_date(release.get("target_date"))
    feats: dict[str, Any] = {
        "change_type": str(release.get("change_type", "feature")),
        "target_env": str(release.get("target_env", "production")),
        "patch_size_loc": int(release.get("patch_size_loc", 0) or 0),
        "files_changed": int(release.get("files_changed", 1) or 0),
        "test_coverage": float(release.get("test_coverage", 0) or 0),
        "ticket_complexity": int(release.get("ticket_complexity", 3) or 3),
        "module_failure_rate": float(release.get("module_failure_rate", 0) or 0),
        "has_db_migration": int(bool(release.get("has_db_migration", False))),
        "dependency_changes": int(release.get("dependency_changes", 0) or 0),
        "friday_or_weekend": int(release["friday_or_weekend"]) if "friday_or_weekend" in release
        else int(target is not None and target.weekday() >= 4),
    }
    validate(feats)
    return feats


def validate(feats: Mapping[str, Any]) -> None:
    for name, allowed in CATEGORICAL.items():
        if feats[name] not in allowed:
            raise ValueError(f"{name} must be one of {allowed}, got {feats[name]!r}")
    checks = {
        "patch_size_loc": (0, 1_000_000),
        "files_changed": (0, 100_000),
        "test_coverage": (0, 100),
        "ticket_complexity": (1, 5),
        "module_failure_rate": (0, 1),
        "has_db_migration": (0, 1),
        "dependency_changes": (0, 10_000),
        "friday_or_weekend": (0, 1),
    }
    for name, (lo, hi) in checks.items():
        v = feats[name]
        if v is None or (isinstance(v, float) and math.isnan(v)) or not lo <= v <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}, got {v!r}")
