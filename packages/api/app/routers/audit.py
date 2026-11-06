from fastapi import APIRouter, Depends, Query

from ..auth import User, require_permission
from ..db import fetch_all, fetch_one, transaction

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def audit_log(limit: int = Query(200, le=2000), entity_id: int | None = None,
              _: User = Depends(require_permission("audit.view"))):
    where, params = "", [limit]
    if entity_id is not None:
        where, params = "WHERE a.entity_id = %s", [entity_id, limit]
    with transaction() as conn:
        return {"entries": fetch_all(conn, f"""
            SELECT a.seq, a.occurred_at, a.action, a.entity, a.entity_id, a.details,
                   a.prev_hash, a.row_hash, u.username AS actor
            FROM audit_logs a LEFT JOIN users u ON u.id = a.actor_id
            {where} ORDER BY a.seq DESC LIMIT %s""", params)}


@router.get("/verify")
def verify(_: User = Depends(require_permission("audit.view"))):
    """Recompute the SHA-256 hash chain and report the first broken link, if any."""
    with transaction() as conn:
        return fetch_one(conn, "SELECT * FROM verify_audit_chain()")
