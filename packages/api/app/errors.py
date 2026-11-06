"""Translate PostgreSQL errors raised by governance functions into HTTP responses."""
import psycopg
from fastapi import Request
from fastapi.responses import JSONResponse

# SQLSTATE -> HTTP status
_MAP = {
    "42501": 403,   # insufficient_privilege  (role / separation-of-duties violations)
    "P0002": 404,   # no_data_found
    "23505": 409,   # unique_violation         (duplicate ticket id, double signature)
    "23514": 409,   # check_violation          (illegal state transition, policy rule)
    "23503": 409,   # foreign_key_violation
    "23502": 422,   # not_null_violation
    "22P02": 422,   # invalid_text_representation (bad enum value etc.)
    "22007": 422,   # invalid_datetime_format
    "22008": 422,   # datetime_field_overflow
}


async def psycopg_error_handler(_: Request, exc: psycopg.Error) -> JSONResponse:
    state = getattr(exc, "sqlstate", None) or ""
    diag = getattr(exc, "diag", None)
    message = (diag.message_primary if diag and diag.message_primary else str(exc)).strip()
    status = _MAP.get(state, 422 if state.startswith("22") else 400 if state == "P0001" else 500)
    if status == 500:
        message = "database error"
    return JSONResponse(status_code=status, content={"detail": message, "sqlstate": state})
