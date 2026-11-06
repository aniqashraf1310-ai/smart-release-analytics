"""Account administration - Release Managers only (also enforced in PostgreSQL)."""
from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..auth import User, require_permission
from ..db import fetch_all, fetch_one, scalar, transaction
from ..schemas import ReviewRegistrationIn, RoleChangeIn, StatusChangeIn

router = APIRouter(prefix="/api/users", tags=["accounts"])
admin = require_permission("users.manage")


def _user(conn, user_id: int) -> dict:
    return fetch_one(conn, "SELECT * FROM v_users WHERE id = %s", (user_id,))


@router.get("")
def list_users(status: Literal["PENDING", "ACTIVE", "REJECTED", "DISABLED"] | None = Query(None),
               _: User = Depends(admin)):
    with transaction() as conn:
        if status:
            rows = fetch_all(conn, "SELECT * FROM v_users WHERE status = %s ORDER BY created_at", (status,))
        else:
            rows = fetch_all(conn, """SELECT * FROM v_users
                ORDER BY (status = 'PENDING') DESC, role NULLS LAST, username""")
        return {"users": rows}


@router.post("/{user_id}/approve")
def approve(user_id: int, body: ReviewRegistrationIn, actor: User = Depends(admin)):
    with transaction(actor.id) as conn:
        scalar(conn, "SELECT review_registration(%s, %s, TRUE, %s, %s)", (user_id, actor.id, body.role, body.note))
        return _user(conn, user_id)


@router.post("/{user_id}/reject")
def reject(user_id: int, body: ReviewRegistrationIn, actor: User = Depends(admin)):
    with transaction(actor.id) as conn:
        scalar(conn, "SELECT review_registration(%s, %s, FALSE, NULL, %s)", (user_id, actor.id, body.note))
        return _user(conn, user_id)


@router.put("/{user_id}/role")
def change_role(user_id: int, body: RoleChangeIn, actor: User = Depends(admin)):
    with transaction(actor.id) as conn:
        scalar(conn, "SELECT change_user_role(%s, %s, %s, %s)", (user_id, actor.id, body.role, body.note))
        return _user(conn, user_id)


@router.put("/{user_id}/status")
def change_status(user_id: int, body: StatusChangeIn, actor: User = Depends(admin)):
    with transaction(actor.id) as conn:
        scalar(conn, "SELECT set_user_status(%s, %s, %s, %s)", (user_id, actor.id, body.status, body.note))
        return _user(conn, user_id)
