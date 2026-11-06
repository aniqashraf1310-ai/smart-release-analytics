"""Loads the trained model once and registers it in the model_versions table."""
import logging
import sys

from fastapi import HTTPException

from .config import ML_PACKAGE_DIR, MODEL_PATH
from .db import scalar, transaction

sys.path.insert(0, str(ML_PACKAGE_DIR))
from risk_model import features  # noqa: E402
from risk_model.engine import RiskEngine  # noqa: E402

log = logging.getLogger("risk")


class _State:
    engine: RiskEngine | None = None
    model_version_id: int | None = None
    error: str | None = None


state = _State()


def load_model() -> None:
    try:
        engine = RiskEngine.load(MODEL_PATH)
    except FileNotFoundError:
        state.error = (f"no trained model at {MODEL_PATH}. "
                       "Run `python packages/ml/train.py` and restart the API.")
        log.warning(state.error)
        return
    meta = engine.meta
    import json
    with transaction() as conn:
        state.model_version_id = scalar(
            conn, "SELECT register_model(%s, %s, %s, %s::jsonb, %s::jsonb, %s)",
            (meta["version_tag"], meta["algorithm"], meta["trained_at"],
             json.dumps(meta["metrics"]), json.dumps(meta["thresholds"]), str(MODEL_PATH)))
    state.engine = engine
    state.error = None
    log.info("loaded model %s (id %s)", meta["version_tag"], state.model_version_id)


def engine() -> RiskEngine:
    if state.engine is None:
        raise HTTPException(503, state.error or "model not loaded")
    return state.engine


__all__ = ["features", "engine", "load_model", "state"]
