import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import risk
from ..auth import User, require_permission
from ..db import fetch_all, transaction
from ..schemas import ScoreIn

router = APIRouter(prefix="/api/ml", tags=["predictive engine"])


@router.get("/model")
def model_info(_: User = Depends(require_permission("model.view"))):
    eng = risk.engine()
    return {"model_version_id": risk.state.model_version_id, "version_tag": eng.version_tag,
            "algorithm": eng.meta["algorithm"], "trained_at": eng.meta["trained_at"],
            "thresholds": eng.thresholds, "metrics": eng.meta["metrics"]}


@router.post("/score")
def score_preview(body: ScoreIn, _: User = Depends(require_permission("risk.what_if"))):
    """Score features without saving anything (live preview while filling the form)."""
    eng = risk.engine()
    try:
        feats = risk.features.from_release(body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    result = eng.score(feats)
    result["mitigations"] = eng.suggest_mitigations(feats)
    return result


@router.get("/drift")
def drift(last: int = Query(200, ge=1, le=5000), _: User = Depends(require_permission("model.view"))):
    """Population Stability Index of recently scored releases vs the training data."""
    eng = risk.engine()
    with transaction() as conn:
        rows = fetch_all(conn, "SELECT feature_snapshot FROM risk_predictions ORDER BY id DESC LIMIT %s", (last,))
    return eng.psi([r["feature_snapshot"] for r in rows])


@router.get("/training-export")
def training_export(_: User = Depends(require_permission("model.export"))):
    """
    Real deployment outcomes joined to the exact features that were scored,
    as a CSV for `python packages/ml/train.py --extra export.csv`.
    """
    with transaction() as conn:
        rows = fetch_all(conn, """
            SELECT p.feature_snapshot, o.outcome
            FROM deployment_outcomes o JOIN risk_predictions p ON p.id = o.prediction_id""")
    buf = io.StringIO()
    cols = risk.features.FEATURES + ["failed"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({**{c: r["feature_snapshot"].get(c) for c in risk.features.FEATURES},
                    "failed": int(r["outcome"] in ("FAILED", "ROLLED_BACK"))})
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=deployment_outcomes.csv"})
