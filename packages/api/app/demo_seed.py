"""
Demo data: 20 releases spread across every stage of the workflow, plus a
pending account request and a few edits, so every kind of notification can be
shown live (see "Notification demo scenarios" in the README).

Each release is created, scored by the trained model and taken through the
real governance functions (submit, sign, reject, deploy, break-glass, ...),
one short transaction per step, exactly as the UI would. So the risk scores,
signatures, audit trail and notifications it leaves behind are all genuine.

Runs automatically when the API starts against an empty database (disable
with SEED_DEMO=0), or by hand:   python packages/api/seed_demo.py
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from . import risk
from .db import fetch_all, fetch_one, scalar, transaction

log = logging.getLogger("seed")

QA = ["qa1", "qa2"]
RM = ["rm1", "rm2"]

# (fields, plan). Target dates are relative to today so the board looks current.
DEMO = [
    (dict(app_name="Mobile Banking App", version="1.2.1", days=-30, qa_status="Pass", responsible_qa="Azila",
          ticket_id="ISD-11940", fix_type="Binary Changes", change_type="feature", target_env="production",
          patch_size_loc=180, files_changed=6, test_coverage=82, ticket_complexity=2, module_failure_rate=0.05,
          description="Ready for UAT in v3; received congratulatory email from the business owner.",
          progression="Released to all users"),
     dict(plan="deployed", by="dev1")),
    (dict(app_name="Mobile Banking App", version="1.2.1", days=2, qa_status="Under review", responsible_qa="Akma",
          ticket_id="HP19-506", fix_type="Binary Changes", change_type="hotfix", target_env="production",
          patch_size_loc=640, files_changed=14, test_coverage=55, ticket_complexity=4, module_failure_rate=0.22,
          dependency_changes=2,
          description="Unable to scan QR using Android & iOS. Workaround: scan the QR code from the gallery.",
          progression="Fix verified on Android, iOS regression in progress"),
     dict(plan="partial", by="dev1", signs=[("qa1", "QA_LEAD")])),
    (dict(app_name="Mobile Banking App", version="1.2.0", days=5, qa_status="Failed", responsible_qa="Azila",
          ticket_id="ISD-8976", fix_type="Data Model Update", change_type="feature", target_env="production",
          patch_size_loc=1450, files_changed=31, test_coverage=38, ticket_complexity=5, module_failure_rate=0.31,
          has_db_migration=True, dependency_changes=4,
          description="Unable to download attachment in Secure Message. Issue persists after first fix.",
          progression="Waiting for developer"),
     dict(plan="rejected", by="dev2", rejector=("qa2", "QA_LEAD"),
          comment="UAT evidence missing for the attachment download; please attach the test run.")),
    (dict(app_name="Mobile Banking App", version="1.2.2", days=1, qa_status="Ready for UAT", responsible_qa="Akma",
          ticket_id="ISD-12011", fix_type="SSL Pinning", change_type="config", target_env="staging",
          patch_size_loc=40, files_changed=2, test_coverage=91, ticket_complexity=1, module_failure_rate=0.02,
          description="Rotate SSL pinning certificates ahead of expiry.", progression="All checks green"),
     dict(plan="approved", by="dev1")),
    (dict(app_name="Customer Web Portal", version="3.4.0", days=7, qa_status="Open", responsible_qa="Azila",
          ticket_id="WEB-771", fix_type="Spotlight Configuration", change_type="config", target_env="production",
          patch_size_loc=90, files_changed=3, test_coverage=70, ticket_complexity=2, module_failure_rate=0.10,
          dependency_changes=1, description="Enable the new Spotlight campaign banner configuration.",
          progression="Drafting test plan"),
     dict(plan="scored", by="dev2")),
    (dict(app_name="Payments Gateway API", version="2.8.0", days=-12, qa_status="Failed", responsible_qa="Akma",
          ticket_id="PAY-310", fix_type="Data Model Update", change_type="feature", target_env="production",
          patch_size_loc=2200, files_changed=45, test_coverage=48, ticket_complexity=5, module_failure_rate=0.28,
          has_db_migration=True, dependency_changes=3,
          description="Split the ledger table and add settlement batching.", progression="Post-mortem done"),
     dict(plan="failed", by="dev1", notes="Migration timed out on the ledger table; fix-forward in PAY-322.")),
    (dict(app_name="Payments Gateway API", version="2.8.1", days=-2, qa_status="Pass", responsible_qa="Akma",
          ticket_id="PAY-322", fix_type="Other", change_type="hotfix", target_env="production",
          patch_size_loc=120, files_changed=4, test_coverage=66, ticket_complexity=3, module_failure_rate=0.28,
          description="Replace the expired payment-vendor certificate.", progression="Live, monitoring"),
     dict(plan="break_glass", by="dev2", glass_by="rm1",
          justification="Card payments failing for all customers since 09:10; vendor certificate expired.")),
    (dict(app_name="Customer Web Portal", version="3.3.2", days=-20, qa_status="Closed", responsible_qa="Azila",
          ticket_id="WEB-760", fix_type="Binary Changes", change_type="feature", target_env="production",
          patch_size_loc=820, files_changed=19, test_coverage=60, ticket_complexity=4, module_failure_rate=0.15,
          dependency_changes=1, description="New one-page checkout flow.", progression="Rolled back, redesign planned"),
     dict(plan="rolled_back", by="dev1", notes="Checkout error rate rose to 4% within an hour of go-live.")),
    (dict(app_name="Merchant Onboarding Service", version="1.0.0", days=4, qa_status="Under review",
          responsible_qa="Akma", ticket_id="MOS-12", fix_type="Other", change_type="feature",
          target_env="production", patch_size_loc=980, files_changed=22, test_coverage=58, ticket_complexity=4,
          module_failure_rate=0.12, dependency_changes=2, description="First release of self-service merchant onboarding.",
          progression="Pen-test findings closed"),
     dict(plan="partial", by="dev2", signs=[("rm1", "RELEASE_MANAGER")])),
    (dict(app_name="Admin Console", version="5.1.0", days=-8, qa_status="Pass", responsible_qa="Azila",
          ticket_id="ADM-88", fix_type="Other", change_type="refactor", target_env="staging",
          patch_size_loc=300, files_changed=9, test_coverage=84, ticket_complexity=2, module_failure_rate=0.04,
          description="Refactor role management screens to the new component library.", progression="Done"),
     dict(plan="deployed", by="dev2")),
    (dict(app_name="Notification Service", version="0.9.3", days=10, qa_status="Open", responsible_qa="Akma",
          ticket_id="NTF-45", fix_type="Other", change_type="feature", target_env="development",
          patch_size_loc=60, files_changed=2, test_coverage=75, ticket_complexity=1, module_failure_rate=0.03,
          description="Add retry with back-off for push notification delivery.", progression="In development"),
     dict(plan="draft", by="dev1")),
    (dict(app_name="Loyalty Rewards Module", version="2.0.0", days=6, qa_status="Ready for UAT",
          responsible_qa="Azila", ticket_id="LOY-201", fix_type="Binary Changes", change_type="feature",
          target_env="production", patch_size_loc=520, files_changed=12, test_coverage=72, ticket_complexity=3,
          module_failure_rate=0.08, dependency_changes=1, description="Points redemption at partner merchants.",
          progression="UAT sign-off received"),
     dict(plan="submitted", by="dev1")),
    (dict(app_name="Mobile Banking App", version="1.3.0", days=0, qa_status="Pass", responsible_qa="Akma",
          ticket_id="MBA-1300", fix_type="Binary Changes", change_type="feature", target_env="production",
          patch_size_loc=400, files_changed=10, test_coverage=80, ticket_complexity=3, module_failure_rate=0.06,
          description="Dark mode and redesigned account overview.", progression="Rolling out to 10% of users"),
     dict(plan="deploying", by="dev2")),
    (dict(app_name="Fraud Detection Engine", version="4.2.0", days=3, qa_status="Descoped", responsible_qa="Azila",
          ticket_id="FRD-77", fix_type="Other", change_type="refactor", target_env="production",
          patch_size_loc=1600, files_changed=28, test_coverage=65, ticket_complexity=4, module_failure_rate=0.10,
          dependency_changes=6, description="Upgrade the scoring library and retrain thresholds.",
          progression="Superseded"),
     dict(plan="cancelled", by="dev1", reason="Superseded by 4.2.1 after the scoring vendor's update.")),
    (dict(app_name="Customer Web Portal", version="3.5.0", days=9, qa_status="Under review", responsible_qa="Akma",
          ticket_id="WEB-802", fix_type="Spotlight Configuration", change_type="feature", target_env="production",
          patch_size_loc=260, files_changed=7, test_coverage=77, ticket_complexity=3, module_failure_rate=0.09,
          description="Personalised offers carousel on the dashboard.", progression="Scope grew after approval"),
     dict(plan="revised", by="dev2", edit={"patch_size_loc": 900, "files_changed": 18})),
    # ---- staged for the notification demo (README: "Notification demo scenarios")
    (dict(app_name="Customer Web Portal", version="3.6.0", days=3, qa_status="Pass", responsible_qa="Azila",
          ticket_id="WEB-815", fix_type="Other", change_type="config", target_env="staging",
          patch_size_loc=35, files_changed=2, test_coverage=92, ticket_complexity=1, module_failure_rate=0.02,
          description="Update the help-centre links in the footer.", progression="QA passed, waiting for go-live approval"),
     dict(plan="awaiting_rm", by="dev1")),
    (dict(app_name="Mobile Banking App", version="1.3.1", days=4, qa_status="Ready for UAT", responsible_qa="Akma",
          ticket_id="MBA-1310", fix_type="Binary Changes", change_type="feature", target_env="staging",
          patch_size_loc=150, files_changed=5, test_coverage=86, ticket_complexity=2, module_failure_rate=0.04,
          description="Add biometric login prompt on first launch.", progression="Submitted, waiting for QA"),
     dict(plan="submitted", by="dev2")),
    (dict(app_name="Payments Gateway API", version="2.9.0", days=5, qa_status="Pass", responsible_qa="Akma",
          ticket_id="PAY-340", fix_type="Data Model Update", change_type="feature", target_env="production",
          patch_size_loc=1300, files_changed=26, test_coverage=52, ticket_complexity=4, module_failure_rate=0.20,
          has_db_migration=True, dependency_changes=2,
          description="Instant refunds to the original card.", progression="One final Release Manager sign-off needed"),
     dict(plan="almost", by="dev1")),
    (dict(app_name="Admin Console", version="5.2.0", days=8, qa_status="Open", responsible_qa="Azila",
          ticket_id="ADM-95", fix_type="Other", change_type="feature", target_env="staging",
          patch_size_loc=210, files_changed=6, test_coverage=81, ticket_complexity=2, module_failure_rate=0.05,
          description="Bulk-disable inactive staff accounts.", progression="Scored, ready to submit"),
     dict(plan="scored", by="dev1")),
    (dict(app_name="Loyalty Rewards Module", version="2.0.1", days=1, qa_status="Pass", responsible_qa="Azila",
          ticket_id="LOY-215", fix_type="Binary Changes", change_type="hotfix", target_env="production",
          patch_size_loc=60, files_changed=3, test_coverage=88, ticket_complexity=2, module_failure_rate=0.08,
          description="Fix points rounding for partial redemptions.", progression="Approved, deploy tomorrow 10:00"),
     dict(plan="approved", by="dev2")),
]

# Overview edits made after the workflow above, so inboxes show edit
# notifications too: (editor, ticket, changes)
EDITS = [
    ("qa2", "HP19-506", {"qa_status": "Ready for UAT", "progression": "iOS regression passed; Android retest booked"}),
    ("rm1", "ISD-12011", {"progression": "Scheduled for Thursday 10:00 with the ops team"}),
    ("qa1", "LOY-201", {"responsible_qa": "Akma", "notes": "Partner sandbox credentials received"}),
]

# A registration waiting for a Release Manager (Accounts page).
PENDING_ACCOUNT = dict(username="nurul", full_name="Nurul Izzati", email="nurul@example.com",
                       password="Passw0rd1", requested_role="DEVELOPER")


def _uid(username: str) -> int:
    with transaction() as conn:
        return scalar(conn, "SELECT id FROM users WHERE username = %s", (username,))


def _create(fields: dict, creator: int) -> int:
    f = dict(fields)
    days = f.pop("days")
    f["target_date"] = dt.date.today() + dt.timedelta(days=days)
    f.setdefault("has_db_migration", False)
    f.setdefault("dependency_changes", 0)
    cols = list(f) + ["created_by", "created_at"]
    created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(3, 14 - days))
    with transaction(creator) as conn:
        return scalar(conn, f"INSERT INTO releases ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                      [*f.values(), creator, created])


def _score(rid: int, actor: int) -> str:
    eng = risk.engine()
    with transaction(actor) as conn:
        row = fetch_one(conn, "SELECT * FROM releases WHERE id = %s", (rid,))
        feats = risk.features.from_release(row)
        res = eng.score(feats)
        scalar(conn, "SELECT record_prediction(%s, %s, %s, %s::numeric, %s::risk_tier, %s::jsonb, %s::jsonb)",
               (rid, actor, risk.state.model_version_id, res["probability"], res["tier"],
                json.dumps(feats), json.dumps(res["explanation"])))
    return res["tier"]


def _call(actor: int, sql: str, params: tuple):
    with transaction(actor) as conn:
        return scalar(conn, sql, params)


def _sign(rid: int, username: str, role: str, decision: str = "APPROVE", comment: str | None = None):
    actor = _uid(username)
    return _call(actor, "SELECT sign_approval(%s, %s, %s, %s, %s, TRUE)", (rid, actor, role, decision, comment))


def _approve_fully(rid: int, roles: tuple[str, ...] = ("QA_LEAD", "RELEASE_MANAGER"), leave_rm: int = 0) -> None:
    """Add signatures from distinct people until the risk-tier policy is met
    (for the given roles), optionally leaving `leave_rm` RM signatures missing."""
    pools = {"QA_LEAD": QA, "RELEASE_MANAGER": RM}
    with transaction() as conn:
        progress = fetch_all(conn, "SELECT * FROM approval_progress(%s)", (rid,))
    for p in progress:
        if p["role_code"] not in roles:
            continue
        free = [u for u in pools[p["role_code"]] if u not in p["signers"]]
        need = p["required"] - p["approved"] - (leave_rm if p["role_code"] == "RELEASE_MANAGER" else 0)
        for username in free[: max(0, need)]:
            _sign(rid, username, p["role_code"])


def seed(force: bool = False) -> int:
    with transaction() as conn:
        if not force and scalar(conn, "SELECT count(*) FROM releases"):
            return 0
    rm1, rm2 = _uid("rm1"), _uid("rm2")
    for fields, spec in DEMO:
        plan, dev = spec["plan"], _uid(spec["by"])
        rid = _create(fields, dev)
        if plan == "draft":
            continue
        _score(rid, dev)
        if plan == "scored":
            continue
        _call(dev, "SELECT submit_release(%s, %s, %s)", (rid, dev, None))
        if plan == "submitted":
            continue
        if plan == "partial":
            for username, role in spec["signs"]:
                _sign(rid, username, role)
        elif plan == "rejected":
            username, role = spec["rejector"]
            _sign(rid, username, role, "REJECT", spec["comment"])
        elif plan == "awaiting_rm":                       # QA done, Release Manager next
            _approve_fully(rid, roles=("QA_LEAD",))
        elif plan == "almost":                            # everything but the last RM signature
            _approve_fully(rid, leave_rm=1)
        elif plan == "cancelled":
            _call(dev, "SELECT cancel_release(%s, %s, %s)", (rid, dev, spec["reason"]))
        elif plan == "break_glass":
            g = _uid(spec["glass_by"])
            _call(g, "SELECT emergency_approve(%s, %s, %s)", (rid, g, spec["justification"]))
            _call(g, "SELECT start_deployment(%s, %s)", (rid, g))
            _call(g, "SELECT record_outcome(%s, %s, 'DEPLOYED'::gov_status, %s)", (rid, g, "Payments recovered at 09:42"))
        else:
            _approve_fully(rid)
            if plan == "revised":
                sets = ", ".join(f"{k} = %s" for k in spec["edit"])
                with transaction(dev) as conn:
                    conn.execute(f"UPDATE releases SET {sets} WHERE id = %s", [*spec["edit"].values(), rid])
            elif plan in ("deploying", "deployed", "failed", "rolled_back"):
                _call(rm1, "SELECT start_deployment(%s, %s)", (rid, rm1))
                if plan != "deploying":
                    outcome = {"deployed": "DEPLOYED", "failed": "FAILED", "rolled_back": "ROLLED_BACK"}[plan]
                    _call(rm2, "SELECT record_outcome(%s, %s, %s::gov_status, %s)",
                          (rid, rm2, outcome, spec.get("notes")))
    ids = {}
    with transaction() as conn:
        for r in fetch_all(conn, "SELECT id, ticket_id FROM releases"):
            ids[r["ticket_id"]] = r["id"]
    for username, ticket, changes in EDITS:
        actor = _uid(username)
        sets = ", ".join(f"{k} = %s" for k in changes)
        with transaction(actor) as conn:
            conn.execute(f"UPDATE releases SET {sets} WHERE id = %s", [*changes.values(), ids[ticket]])
    with transaction() as conn:
        if not scalar(conn, "SELECT count(*) FROM users WHERE username = %s", (PENDING_ACCOUNT["username"],)):
            scalar(conn, "SELECT register_user(%s, %s, %s, %s, %s)", tuple(PENDING_ACCOUNT.values()))

    # Keep the demo inbox tidy: only each user's 5 newest notifications stay unread.
    with transaction() as conn:
        conn.execute("""SELECT mark_notification_read(id, user_id) FROM (
                            SELECT id, user_id, row_number() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn
                            FROM notifications WHERE read_at IS NULL) t
                        WHERE rn > 5""")
    log.info("seeded %d demo releases", len(DEMO))
    return len(DEMO)
