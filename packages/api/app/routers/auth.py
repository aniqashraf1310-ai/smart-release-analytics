from fastapi import APIRouter, Depends, HTTPException

from ..auth import User, current_user, issue_token, load_user
from ..db import fetch_one, scalar, transaction
from ..permissions import matrix
from ..schemas import LoginIn, RegisterIn

router = APIRouter(prefix="/api/auth", tags=["auth"])

_STATUS_MESSAGES = {
    "PENDING": "Your account is awaiting approval by a Release Manager.",
    "REJECTED": "Your registration was not approved. Contact a Release Manager.",
    "DISABLED": "Your account has been disabled. Contact a Release Manager.",
}


@router.post("/register", status_code=201)
def register(body: RegisterIn):
    """Public. Creates a PENDING account; a Release Manager must approve it."""
    with transaction() as conn:
        user_id = scalar(conn, "SELECT register_user(%s, %s, %s, %s, %s)",
                         (body.username, body.full_name, body.email, body.password, body.requested_role))
    return {"id": user_id, "status": "PENDING",
            "message": "Registration received. A Release Manager will review it and assign your role."}


@router.post("/login")
def login(body: LoginIn):
    with transaction() as conn:
        row = fetch_one(conn, "SELECT * FROM authenticate(%s, %s)", (body.username, body.password))
    if row is None:
        raise HTTPException(401, "invalid username or password")
    if row["status"] != "ACTIVE":
        raise HTTPException(403, _STATUS_MESSAGES[row["status"]])
    user = load_user(row["id"])
    return {"token": issue_token(user.id), "user": user.to_dict()}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user.to_dict()


@router.get("/permissions")
def permission_matrix():
    """Public: which role may do what (shown on the registration page)."""
    return {"roles": ["DEVELOPER", "QA_LEAD", "RELEASE_MANAGER"], "permissions": matrix()}
