"""
Role -> permission matrix: the single source of truth for what each role may do.

The API enforces it (require_permission), the frontend reads it from
/api/auth/me and /api/governance/permissions to show or hide actions, and
PostgreSQL independently re-checks every governance action, so a bug here can
hide a button but can never grant authority the database would refuse.
"""
DEV, QA, RM = "DEVELOPER", "QA_LEAD", "RELEASE_MANAGER"
ROLES = (DEV, QA, RM)

# code: (group, description, allowed roles)
PERMISSIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "release.view":            ("Releases", "View releases, risk scores and approval progress", (DEV, QA, RM)),
    "release.create":          ("Releases", "Create a new release (draft)", (DEV, QA)),
    "release.edit":            ("Releases", "Edit release details (content edits reset approvals)", (DEV, QA, RM)),
    "release.cancel":          ("Releases", "Cancel or delete a release (own releases; RMs any)", (DEV, QA, RM)),
    "risk.score":              ("Risk",     "Record the official risk score of a draft", (DEV, QA)),
    "risk.what_if":            ("Risk",     "Use the what-if simulator", (DEV, QA, RM)),
    "release.submit":          ("Approval", "Submit a scored release for review (the Developer signature)", (DEV,)),
    "approval.qa":             ("Approval", "Approve or reject as QA Lead", (QA,)),
    "approval.rm":             ("Approval", "Approve or reject as Release Manager (final sign-off)", (RM,)),
    "release.deploy":          ("Deploy",   "Start a deployment and record its outcome", (RM,)),
    "release.break_glass":     ("Deploy",   "Emergency break-glass approval", (RM,)),
    "incident.review":         ("Deploy",   "Complete post-incident reviews (not your own break-glass)", (RM,)),
    "audit.view":              ("Oversight", "View the audit log and verify the hash chain", (DEV, QA, RM)),
    "model.view":              ("Oversight", "View model performance and drift", (DEV, QA, RM)),
    "model.export":            ("Oversight", "Export deployment outcomes for retraining", (RM,)),
    "users.manage":            ("Accounts", "Approve registrations, change roles, disable accounts", (RM,)),
}

APPROVAL_PERMISSION = {QA: "approval.qa", RM: "approval.rm"}


def permissions_for(roles) -> list[str]:
    return [code for code, (_, _, allowed) in PERMISSIONS.items() if any(r in allowed for r in roles)]


def matrix() -> list[dict]:
    return [{"code": code, "group": group, "description": desc,
             "roles": {r: r in allowed for r in ROLES}}
            for code, (group, desc, allowed) in PERMISSIONS.items()]
