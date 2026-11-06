"""Request bodies. Response bodies are plain dicts from the database views."""
import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

ChangeType = Literal["hotfix", "feature", "config", "refactor"]
TargetEnv = Literal["development", "staging", "production"]


Role = Literal["DEVELOPER", "QA_LEAD", "RELEASE_MANAGER"]


class LoginIn(BaseModel):
    username: str
    password: str


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=100)
    email: str | None = Field(None, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    requested_role: Role


class ReviewRegistrationIn(BaseModel):
    role: Role | None = None          # defaults to the requested role when approving
    note: str | None = Field(None, max_length=500)


class RoleChangeIn(BaseModel):
    role: Role
    note: str | None = Field(None, max_length=500)


class StatusChangeIn(BaseModel):
    status: Literal["ACTIVE", "DISABLED"]
    note: str | None = Field(None, max_length=500)


class RiskFeaturesIn(BaseModel):
    change_type: ChangeType = "feature"
    target_env: TargetEnv = "production"
    patch_size_loc: int = Field(0, ge=0)
    files_changed: int = Field(1, ge=0)
    test_coverage: float = Field(0, ge=0, le=100)
    ticket_complexity: int = Field(3, ge=1, le=5)
    module_failure_rate: float = Field(0, ge=0, le=1)
    has_db_migration: bool = False
    dependency_changes: int = Field(0, ge=0)


class ReleaseCreate(RiskFeaturesIn):
    app_name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+(\.[0-9]+)*$")
    target_date: dt.date
    qa_status: str = "Open"
    responsible_qa: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9,-]+$")
    fix_type: str
    description: str = Field(min_length=1)
    progression: str = ""
    notes: str | None = None


class ReleaseUpdate(BaseModel):
    """All optional: only the fields sent are changed."""
    app_name: str | None = None
    version: str | None = Field(None, pattern=r"^[0-9]+(\.[0-9]+)*$")
    target_date: dt.date | None = None
    qa_status: str | None = None
    responsible_qa: str | None = None
    ticket_id: str | None = None
    fix_type: str | None = None
    description: str | None = None
    progression: str | None = None
    notes: str | None = None
    change_type: ChangeType | None = None
    target_env: TargetEnv | None = None
    patch_size_loc: int | None = Field(None, ge=0)
    files_changed: int | None = Field(None, ge=0)
    test_coverage: float | None = Field(None, ge=0, le=100)
    ticket_complexity: int | None = Field(None, ge=1, le=5)
    module_failure_rate: float | None = Field(None, ge=0, le=1)
    has_db_migration: bool | None = None
    dependency_changes: int | None = Field(None, ge=0)


class ScoreIn(RiskFeaturesIn):
    target_date: dt.date | None = None


class WhatIfIn(BaseModel):
    overrides: dict[str, float | int | str | bool]


class SubmitIn(BaseModel):
    comment: str | None = None


class ApprovalIn(BaseModel):
    role: Literal["QA_LEAD", "RELEASE_MANAGER"]
    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = None
    acknowledged_risk: bool = False


class OutcomeIn(BaseModel):
    outcome: Literal["DEPLOYED", "FAILED", "ROLLED_BACK"]
    notes: str | None = None


class EmergencyIn(BaseModel):
    justification: str = Field(min_length=20)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3)


class ReviewIn(BaseModel):
    findings: str = Field(min_length=20)
