from fastapi import APIRouter, Depends

from ..auth import User, require_permission
from ..permissions import matrix
from ..db import fetch_all, transaction
from ..schemas import ReviewIn

router = APIRouter(prefix="/api/governance", tags=["governance"])


@router.get("/policies")
def policies(_: User = Depends(require_permission("release.view"))):
    """The risk-adaptive approval matrix, state machine and settings, for display."""
    with transaction() as conn:
        return {
            "policies": fetch_all(conn, """
                SELECT p.risk_tier, p.role_code, r.name AS role_name, p.min_approvals, p.requires_risk_ack
                FROM approval_policies p JOIN roles r ON r.code = p.role_code
                ORDER BY p.risk_tier, p.role_code"""),
            "transitions": fetch_all(conn, "SELECT * FROM state_transitions ORDER BY from_status, to_status"),
            "settings": fetch_all(conn, "SELECT * FROM governance_settings ORDER BY key"),
            "roles": fetch_all(conn, "SELECT * FROM roles ORDER BY code"),
            "permissions": matrix(),
        }


@router.get("/incident-reviews")
def incident_reviews(_: User = Depends(require_permission("release.view"))):
    with transaction() as conn:
        return {"reviews": fetch_all(conn, """
            SELECT pir.*, r.app_name, r.version, r.ticket_id, u.username AS opened_by_username,
                   (pir.status = 'PENDING' AND now() > pir.due_at) AS overdue
            FROM post_incident_reviews pir
            JOIN releases r ON r.id = pir.release_id
            JOIN users u ON u.id = pir.opened_by
            ORDER BY pir.status DESC, pir.due_at""")}


@router.post("/incident-reviews/{review_id}/complete")
def complete_review(review_id: int, body: ReviewIn, user: User = Depends(require_permission("incident.review"))):
    with transaction(user.id) as conn:
        conn.execute("SELECT complete_incident_review(%s, %s, %s)", (review_id, user.id, body.findings))
    return {"message": "review completed"}
