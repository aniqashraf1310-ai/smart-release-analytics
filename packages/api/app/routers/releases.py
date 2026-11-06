"""Release CRUD, risk scoring and the governance workflow."""
import json

from fastapi import APIRouter, Depends, HTTPException

from .. import risk
from ..auth import User, require_permission
from ..permissions import APPROVAL_PERMISSION
from ..db import fetch_all, fetch_one, scalar, transaction
from ..schemas import (ApprovalIn, CancelIn, EmergencyIn, OutcomeIn, ReleaseCreate, ReleaseUpdate,
                       SubmitIn, WhatIfIn)

router = APIRouter(prefix="/api/releases", tags=["releases"])

LIST_SQL = """
SELECT o.*, cu.username AS created_by_username, su.username AS submitted_by_username
FROM v_release_overview o
JOIN users cu ON cu.id = o.created_by
LEFT JOIN users su ON su.id = o.submitted_by
"""

UPDATABLE = set(ReleaseUpdate.model_fields)


def _get(conn, release_id: int) -> dict:
    row = fetch_one(conn, LIST_SQL + " WHERE o.id = %s", (release_id,))
    if row is None:
        raise HTTPException(404, f"release {release_id} not found")
    return row


def _detail(conn, release_id: int) -> dict:
    release = _get(conn, release_id)
    release["approval_progress"] = fetch_all(conn, "SELECT * FROM approval_progress(%s)", (release_id,))
    release["policy_satisfied"] = scalar(conn, "SELECT policy_satisfied(%s)", (release_id,))
    release["payload_hash"] = scalar(conn, "SELECT release_payload_hash(%s)", (release_id,))
    release["approvals"] = fetch_all(conn, """
        SELECT a.id, a.release_revision, a.role_code, a.decision, a.comment, a.acknowledged_risk,
               a.payload_hash, a.signed_at, a.expires_at, a.invalidated_at, a.invalidated_reason,
               u.username, u.full_name,
               (a.invalidated_at IS NULL AND a.expires_at > now()
                AND a.release_revision = r.revision) AS is_live
        FROM approvals a JOIN users u ON u.id = a.user_id JOIN releases r ON r.id = a.release_id
        WHERE a.release_id = %s ORDER BY a.signed_at""", (release_id,))
    release["predictions"] = fetch_all(conn, """
        SELECT p.id, p.release_revision, p.probability, p.risk_tier, p.created_at,
               m.version_tag AS model_version
        FROM risk_predictions p JOIN model_versions m ON m.id = p.model_version_id
        WHERE p.release_id = %s ORDER BY p.id DESC""", (release_id,))
    release["audit_trail"] = fetch_all(conn, """
        SELECT a.seq, a.occurred_at, a.action, a.details, u.username AS actor
        FROM audit_logs a LEFT JOIN users u ON u.id = a.actor_id
        WHERE a.entity = 'release' AND a.entity_id = %s ORDER BY a.seq""", (release_id,))
    release["incident_reviews"] = fetch_all(conn, """
        SELECT * FROM post_incident_reviews WHERE release_id = %s ORDER BY id""", (release_id,))
    return release


# ------------------------------------------------------------------ read
@router.get("")
def list_releases(_: User = Depends(require_permission("release.view"))):
    with transaction() as conn:
        return {"releases": fetch_all(conn, LIST_SQL + " ORDER BY o.target_date, o.id")}


@router.get("/{release_id}")
def get_release(release_id: int, _: User = Depends(require_permission("release.view"))):
    with transaction() as conn:
        return _detail(conn, release_id)


# ------------------------------------------------------------------ create / edit / delete
@router.post("", status_code=201)
def create_release(body: ReleaseCreate, user: User = Depends(require_permission("release.create"))):
    data = body.model_dump()
    cols = list(data) + ["created_by"]
    with transaction(user.id) as conn:
        new_id = scalar(conn,
                        f"INSERT INTO releases ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                        [*data.values(), user.id])
        return _detail(conn, new_id)


@router.patch("/{release_id}")
def update_release(release_id: int, body: ReleaseUpdate,
                   user: User = Depends(require_permission("release.edit"))):
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in UPDATABLE}
    if not changes:
        raise HTTPException(422, "no fields to update")
    sets = ", ".join(f"{k} = %s" for k in changes)
    with transaction(user.id) as conn:
        _get(conn, release_id)
        # Material edits are detected by a trigger: it bumps the revision,
        # returns the release to DRAFT and invalidates existing signatures.
        conn.execute(f"UPDATE releases SET {sets} WHERE id = %s", [*changes.values(), release_id])
        return _detail(conn, release_id)


@router.delete("/{release_id}")
def delete_release(release_id: int, user: User = Depends(require_permission("release.cancel"))):
    with transaction(user.id) as conn:
        conn.execute("SELECT delete_release(%s, %s)", (release_id, user.id))
    return {"message": "release deleted"}


# ------------------------------------------------------------------ predictive engine
@router.post("/{release_id}/score")
def score_release(release_id: int, user: User = Depends(require_permission("risk.score"))):
    eng = risk.engine()
    with transaction(user.id) as conn:
        release = _get(conn, release_id)
        feats = risk.features.from_release(release)
        result = eng.score(feats)
        result["mitigations"] = eng.suggest_mitigations(feats)
        pred_id = scalar(conn, "SELECT record_prediction(%s, %s, %s, %s::numeric, %s::risk_tier, %s::jsonb, %s::jsonb)",
                         (release_id, user.id, risk.state.model_version_id, result["probability"],
                          result["tier"], json.dumps(feats), json.dumps(result["explanation"])))
        result["prediction_id"] = pred_id
        result["release"] = _detail(conn, release_id)
        return result


@router.post("/{release_id}/what-if")
def what_if(release_id: int, body: WhatIfIn, _: User = Depends(require_permission("risk.what_if"))):
    eng = risk.engine()
    with transaction() as conn:
        feats = risk.features.from_release(_get(conn, release_id))
    try:
        return eng.what_if(feats, body.overrides)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/{release_id}/mitigations")
def mitigations(release_id: int, _: User = Depends(require_permission("release.view"))):
    eng = risk.engine()
    with transaction() as conn:
        feats = risk.features.from_release(_get(conn, release_id))
    return {"mitigations": eng.suggest_mitigations(feats)}


# ------------------------------------------------------------------ governance workflow
def _call(user: User, release_id: int, sql: str, params: tuple) -> dict:
    with transaction(user.id) as conn:
        new_status = scalar(conn, sql, params)
        return {"status": new_status, "release": _detail(conn, release_id)}


@router.post("/{release_id}/submit")
def submit(release_id: int, body: SubmitIn, user: User = Depends(require_permission("release.submit"))):
    return _call(user, release_id, "SELECT submit_release(%s, %s, %s)", (release_id, user.id, body.comment))


@router.post("/{release_id}/approvals")
def sign(release_id: int, body: ApprovalIn, user: User = Depends(require_permission("release.view"))):
    if not user.can(APPROVAL_PERMISSION[body.role]):
        raise HTTPException(403, f"only a {body.role.replace('_', ' ').title()} can sign in that role")
    return _call(user, release_id, "SELECT sign_approval(%s, %s, %s, %s, %s, %s)",
                 (release_id, user.id, body.role, body.decision, body.comment, body.acknowledged_risk))


@router.post("/{release_id}/deploy")
def deploy(release_id: int, user: User = Depends(require_permission("release.deploy"))):
    return _call(user, release_id, "SELECT start_deployment(%s, %s)", (release_id, user.id))


@router.post("/{release_id}/outcome")
def outcome(release_id: int, body: OutcomeIn, user: User = Depends(require_permission("release.deploy"))):
    return _call(user, release_id, "SELECT record_outcome(%s, %s, %s::gov_status, %s)",
                 (release_id, user.id, body.outcome, body.notes))


@router.post("/{release_id}/emergency-approval")
def emergency(release_id: int, body: EmergencyIn, user: User = Depends(require_permission("release.break_glass"))):
    return _call(user, release_id, "SELECT emergency_approve(%s, %s, %s)",
                 (release_id, user.id, body.justification))


@router.post("/{release_id}/cancel")
def cancel(release_id: int, body: CancelIn, user: User = Depends(require_permission("release.cancel"))):
    return _call(user, release_id, "SELECT cancel_release(%s, %s, %s)", (release_id, user.id, body.reason))
