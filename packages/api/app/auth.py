"""
Authentication (who you are) uses a JWT that only carries the user id.
On every request the account's CURRENT status and role are re-read from the
database, so approving, re-roling or disabling an account takes effect
immediately rather than when the token expires.

Authorisation is enforced twice: here with the permission matrix, and again
inside PostgreSQL for every governance action.
"""
import datetime as dt
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import JWT_SECRET, JWT_TTL_MINUTES
from .db import fetch_one, transaction
from .permissions import PERMISSIONS, permissions_for

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    id: int
    username: str
    full_name: str
    roles: tuple[str, ...]

    def has(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)

    def can(self, permission: str) -> bool:
        return any(r in PERMISSIONS[permission][2] for r in self.roles)

    @property
    def role(self) -> str | None:
        return self.roles[0] if self.roles else None

    def to_dict(self) -> dict:
        return {"id": self.id, "username": self.username, "full_name": self.full_name,
                "role": self.role, "roles": list(self.roles),
                "permissions": permissions_for(self.roles)}


def issue_token(user_id: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + dt.timedelta(minutes=JWT_TTL_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def load_user(user_id: int) -> User | None:
    with transaction() as conn:
        row = fetch_one(conn, """
            SELECT u.id, u.username, u.full_name, u.status, ur.role_code
            FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id
            WHERE u.id = %s""", (user_id,))
    if row is None or row["status"] != "ACTIVE":
        return None
    return User(row["id"], row["username"], row["full_name"],
                (row["role_code"],) if row["role_code"] else ())


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        p = jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e
    user = load_user(int(p["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account is not active")
    return user


def require_permission(permission: str):
    """Friendly, early check in the API. The database re-checks independently."""
    def dep(user: User = Depends(current_user)) -> User:
        if not user.can(permission):
            allowed = ", ".join(PERMISSIONS[permission][2])
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"your role cannot do this ({PERMISSIONS[permission][1].lower()}); requires {allowed}")
        return user
    return dep


def require_roles(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if not user.has(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires one of roles: {', '.join(roles)}")
        return user
    return dep
