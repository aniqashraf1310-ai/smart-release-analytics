"""
End-to-end API tests: full governance workflow through HTTP.

Prerequisites (see README):
    packages/db/reset_db.sh release_mgmt        # fresh database
    python packages/ml/train.py                  # trained model
Run from packages/api:
    python -m pytest tests -q
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "Passw0rd!"
USERS = ["dev1", "dev2", "qa1", "qa2", "rm1", "rm2"]
ROLE_USERS = {"QA_LEAD": ["qa1", "qa2"], "RELEASE_MANAGER": ["rm1", "rm2"]}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def tokens(client):
    out = {}
    for u in USERS:
        r = client.post("/api/auth/login", json={"username": u, "password": PASSWORD})
        assert r.status_code == 200, r.text
        out[u] = {"Authorization": f"Bearer {r.json()['token']}"}
    return out


def new_release(client, tokens, ticket, **overrides):
    body = {"app_name": "Mobile Banking App", "version": "2.0.1", "target_date": "2025-11-04",
            "responsible_qa": "Azila", "ticket_id": ticket, "fix_type": "Binary Changes",
            "description": "API test release", "change_type": "feature", "target_env": "staging",
            "patch_size_loc": 120, "files_changed": 4, "test_coverage": 85, "ticket_complexity": 2,
            "module_failure_rate": 0.03, "has_db_migration": False, "dependency_changes": 0}
    body.update(overrides)
    r = client.post("/api/releases", json=body, headers=tokens["dev1"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def sign_all_required(client, tokens, rid):
    """Sign every outstanding role the policy requires; returns the final status."""
    status = None
    detail = client.get(f"/api/releases/{rid}", headers=tokens["qa1"]).json()
    for prog in detail["approval_progress"]:
        role = prog["role_code"]
        if role == "DEVELOPER":
            continue
        for username in ROLE_USERS[role][prog["approved"]:prog["required"]]:
            r = client.post(f"/api/releases/{rid}/approvals", headers=tokens[username],
                            json={"role": role, "decision": "APPROVE", "acknowledged_risk": True})
            assert r.status_code == 200, r.text
            status = r.json()["status"]
    return status


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["model_loaded"], body


def test_login_rejects_bad_password(client):
    assert client.post("/api/auth/login", json={"username": "dev1", "password": "nope"}).status_code == 401


def test_requires_token(client):
    assert client.get("/api/releases").status_code == 401


def test_full_workflow(client, tokens):
    rid = new_release(client, tokens, "API-FLOW-1")

    r = client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    assert r.status_code == 409 and "risk-scored" in r.json()["detail"]

    r = client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    assert r.status_code == 200, r.text
    score = r.json()
    assert 0 <= score["probability"] <= 1 and score["tier"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert score["explanation"]["contributions"]

    assert client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"]).json()["status"] == "IN_REVIEW"

    # dev2 is a developer: developers cannot approve
    r = client.post(f"/api/releases/{rid}/approvals", headers=tokens["dev2"],
                    json={"role": "QA_LEAD", "decision": "APPROVE"})
    assert r.status_code == 403

    assert sign_all_required(client, tokens, rid) == "APPROVED"

    assert client.post(f"/api/releases/{rid}/deploy", headers=tokens["qa1"]).status_code == 403
    assert client.post(f"/api/releases/{rid}/deploy", headers=tokens["rm1"]).json()["status"] == "DEPLOYING"
    r = client.post(f"/api/releases/{rid}/outcome", headers=tokens["rm1"], json={"outcome": "DEPLOYED"})
    assert r.json()["status"] == "DEPLOYED"

    detail = client.get(f"/api/releases/{rid}", headers=tokens["dev1"]).json()
    actions = [a["action"] for a in detail["audit_trail"]]
    for expected in ("RELEASE_CREATED", "RISK_SCORED", "RELEASE_SUBMITTED", "APPROVAL_APPROVE",
                     "STATUS_CHANGED", "DEPLOYMENT_STARTED", "OUTCOME_RECORDED"):
        assert expected in actions


def test_edit_invalidates_signatures(client, tokens):
    rid = new_release(client, tokens, "API-EDIT-1")
    client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    r = client.post(f"/api/releases/{rid}/approvals", headers=tokens["qa1"],
                    json={"role": "QA_LEAD", "decision": "APPROVE", "acknowledged_risk": True})
    assert r.status_code == 200
    r = client.patch(f"/api/releases/{rid}", headers=tokens["dev1"], json={"patch_size_loc": 3000})
    body = r.json()
    assert body["gov_status"] == "DRAFT" and body["revision"] == 2
    assert not any(a["is_live"] for a in body["approvals"])


def test_high_risk_needs_two_release_managers(client, tokens):
    rid = new_release(client, tokens, "API-HIGH-1", change_type="hotfix", target_env="production",
                      patch_size_loc=4000, files_changed=60, test_coverage=20, ticket_complexity=5,
                      module_failure_rate=0.45, has_db_migration=True, dependency_changes=5,
                      target_date="2025-11-07")
    tier = client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"]).json()["tier"]
    assert tier in ("HIGH", "CRITICAL")
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    progress = client.get(f"/api/releases/{rid}", headers=tokens["dev1"]).json()["approval_progress"]
    rm = next(p for p in progress if p["role_code"] == "RELEASE_MANAGER")
    assert rm["required"] == 2
    assert sign_all_required(client, tokens, rid) == "APPROVED"


def test_role_boundaries(client, tokens):
    rid = new_release(client, tokens, "API-ROLES-1")
    client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    # QA lead cannot submit; developer can
    assert client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["qa1"]).status_code == 403
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    # QA lead cannot sign as Release Manager, RM cannot sign as QA lead
    r = client.post(f"/api/releases/{rid}/approvals", headers=tokens["qa1"],
                    json={"role": "RELEASE_MANAGER", "decision": "APPROVE", "acknowledged_risk": True})
    assert r.status_code == 403
    r = client.post(f"/api/releases/{rid}/approvals", headers=tokens["rm1"],
                    json={"role": "QA_LEAD", "decision": "APPROVE", "acknowledged_risk": True})
    assert r.status_code == 403
    # Release managers cannot create releases; developers and QA leads cannot manage users
    assert client.post("/api/releases", headers=tokens["rm1"], json={}).status_code in (403, 422)
    assert client.get("/api/users", headers=tokens["dev1"]).status_code == 403
    assert client.get("/api/users", headers=tokens["qa1"]).status_code == 403


def test_permissions_reported_per_role(client, tokens):
    me = {u: client.get("/api/auth/me", headers=tokens[u]).json() for u in ("dev1", "qa1", "rm1")}
    assert me["dev1"]["role"] == "DEVELOPER" and "release.submit" in me["dev1"]["permissions"]
    assert "approval.qa" not in me["dev1"]["permissions"]
    assert "approval.qa" in me["qa1"]["permissions"] and "users.manage" not in me["qa1"]["permissions"]
    assert {"approval.rm", "release.deploy", "users.manage"} <= set(me["rm1"]["permissions"])
    matrix = client.get("/api/auth/permissions").json()
    assert matrix["roles"] == ["DEVELOPER", "QA_LEAD", "RELEASE_MANAGER"]


def test_registration_and_approval(client, tokens):
    body = {"username": "newperson", "full_name": "New Person", "email": "newperson@example.com",
            "password": "Passw0rd1", "requested_role": "RELEASE_MANAGER"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    assert client.post("/api/auth/register", json=body).status_code == 409          # duplicate
    weak = {**body, "username": "weakpw", "email": None, "password": "password"}
    assert client.post("/api/auth/register", json=weak).status_code == 409          # no digit

    r = client.post("/api/auth/login", json={"username": "newperson", "password": "Passw0rd1"})
    assert r.status_code == 403 and "awaiting approval" in r.json()["detail"]

    pending = client.get("/api/users?status=PENDING", headers=tokens["rm1"]).json()["users"]
    new = next(u for u in pending if u["username"] == "newperson")
    assert new["requested_role"] == "RELEASE_MANAGER" and new["role"] is None

    # QA lead cannot approve accounts; RM approves but grants Developer instead
    assert client.post(f"/api/users/{new['id']}/approve", headers=tokens["qa1"], json={}).status_code == 403
    r = client.post(f"/api/users/{new['id']}/approve", headers=tokens["rm1"], json={"role": "DEVELOPER"})
    assert r.status_code == 200 and r.json()["role"] == "DEVELOPER" and r.json()["status"] == "ACTIVE"

    r = client.post("/api/auth/login", json={"username": "newperson", "password": "Passw0rd1"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "DEVELOPER"
    token = {"Authorization": f"Bearer {r.json()['token']}"}

    # Nobody can disable themselves; disabling takes effect immediately, even for an issued token
    rm1_id = client.get("/api/auth/me", headers=tokens["rm1"]).json()["id"]
    assert client.put(f"/api/users/{rm1_id}/status", headers=tokens["rm1"],
                      json={"status": "DISABLED"}).status_code == 403
    client.put(f"/api/users/{new['id']}/status", headers=tokens["rm1"], json={"status": "DISABLED"})
    assert client.get("/api/auth/me", headers=token).status_code == 401


def test_break_glass_and_review(client, tokens):
    rid = new_release(client, tokens, "API-GLASS-1")
    client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    assert client.post(f"/api/releases/{rid}/emergency-approval", headers=tokens["qa1"],
                       json={"justification": "Login outage affecting all customers since 09:00"}).status_code == 403
    r = client.post(f"/api/releases/{rid}/emergency-approval", headers=tokens["rm1"],
                    json={"justification": "Login outage affecting all customers since 09:00"})
    assert r.json()["status"] == "APPROVED"
    reviews = client.get("/api/governance/incident-reviews", headers=tokens["rm1"]).json()["reviews"]
    review = next(v for v in reviews if v["release_id"] == rid)
    findings = {"findings": "Expired certificate; renewal is now automated and monitored."}
    r = client.post(f"/api/governance/incident-reviews/{review['id']}/complete", headers=tokens["rm1"], json=findings)
    assert r.status_code == 403                                                     # own break-glass
    r = client.post(f"/api/governance/incident-reviews/{review['id']}/complete", headers=tokens["rm2"], json=findings)
    assert r.status_code == 200


def test_what_if_and_preview(client, tokens):
    rid = new_release(client, tokens, "API-WHATIF-1", test_coverage=40, patch_size_loc=900)
    r = client.post(f"/api/releases/{rid}/what-if", headers=tokens["dev1"],
                    json={"overrides": {"test_coverage": 90}})
    assert r.status_code == 200 and r.json()["delta_pp"] < 0
    r = client.post("/api/ml/score", headers=tokens["dev1"],
                    json={"test_coverage": 150})
    assert r.status_code == 422


def test_audit_chain_and_ml_endpoints(client, tokens):
    assert client.get("/api/audit/verify", headers=tokens["rm1"]).json()["is_valid"] is True
    assert client.get("/api/ml/model", headers=tokens["rm1"]).json()["version_tag"]
    assert "overall" in client.get("/api/ml/drift", headers=tokens["rm1"]).json()
    r = client.get("/api/ml/training-export", headers=tokens["rm1"])
    assert r.status_code == 200 and r.text.startswith("change_type,")
    assert client.get("/api/ml/training-export", headers=tokens["dev1"]).status_code == 403


def test_demo_data_seeded(client, tokens):
    releases = client.get("/api/releases", headers=tokens["qa1"]).json()["releases"]
    tickets = {r["ticket_id"]: r for r in releases}
    demo = ["ISD-11940", "HP19-506", "ISD-8976", "ISD-12011", "WEB-771", "PAY-310", "PAY-322", "WEB-760",
            "MOS-12", "ADM-88", "NTF-45", "LOY-201", "MBA-1300", "FRD-77", "WEB-802",
            "WEB-815", "MBA-1310", "PAY-340", "ADM-95", "LOY-215"]
    assert all(t in tickets for t in demo)
    states = {tickets[t]["gov_status"] for t in demo}
    assert {"DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "DEPLOYING", "DEPLOYED", "FAILED",
            "ROLLED_BACK", "CANCELLED"} <= states
    assert tickets["PAY-322"]["is_emergency"] and tickets["PAY-322"]["pending_reviews"] == 1
    assert not any("KAF" in r["app_name"] for r in releases)
    # staged demo: one RM signature away from approval
    detail = client.get(f"/api/releases/{tickets['PAY-340']['id']}", headers=tokens["rm2"]).json()
    missing = [p for p in detail["approval_progress"] if p["approved"] < p["required"]]
    assert [(p["role_code"], p["required"] - p["approved"]) for p in missing] == [("RELEASE_MANAGER", 1)]
    pending = client.get("/api/users?status=PENDING", headers=tokens["rm1"]).json()["users"]
    assert any(u["username"] == "nurul" for u in pending)


def test_edit_by_reviewer_notifies_developer(client, tokens):
    rid = new_release(client, tokens, "API-EDIT-NOTIFY")
    client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    client.post("/api/notifications/read-all", headers=tokens["dev1"])
    r = client.patch(f"/api/releases/{rid}", headers=tokens["rm1"], json={"qa_status": "Pass", "progression": "ok"})
    assert r.status_code == 200
    note = client.get("/api/notifications?unread_only=true", headers=tokens["dev1"]).json()["notifications"][0]
    assert note["kind"] == "edited" and note["release_id"] == rid
    assert "QA status: Open → Pass" in note["message"] and "Progression updated" in note["message"]


def test_notifications_on_reject(client, tokens):
    rid = new_release(client, tokens, "API-NOTIFY-1")
    client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])

    inbox = client.get("/api/notifications", headers=tokens["qa2"]).json()
    req = [n for n in inbox["notifications"] if n["release_id"] == rid]
    assert req and req[0]["kind"] == "review_requested" and req[0]["read_at"] is None

    before = client.get("/api/notifications/unread-count", headers=tokens["dev1"]).json()
    client.post(f"/api/releases/{rid}/approvals", headers=tokens["qa2"],
                json={"role": "QA_LEAD", "decision": "REJECT", "comment": "Screenshots missing from UAT"})
    after = client.get("/api/notifications/unread-count", headers=tokens["dev1"]).json()
    assert after["unread"] == before["unread"] + 1 and after["latest_id"] > before["latest_id"]

    note = client.get("/api/notifications?unread_only=true", headers=tokens["dev1"]).json()["notifications"][0]
    assert note["kind"] == "rejected" and note["release_id"] == rid and "Screenshots missing" in note["message"]
    assert note["actor"] == "qa2"

    # Nobody else can mark it read; the owner can.
    assert client.post(f"/api/notifications/{note['id']}/read", headers=tokens["qa1"]).status_code == 404
    assert client.post(f"/api/notifications/{note['id']}/read", headers=tokens["dev1"]).status_code == 200
    assert client.get("/api/notifications/unread-count", headers=tokens["dev1"]).json()["unread"] == before["unread"]
    client.post("/api/notifications/read-all", headers=tokens["dev1"])
    assert client.get("/api/notifications/unread-count", headers=tokens["dev1"]).json()["unread"] == 0


def test_notifications_on_approval(client, tokens):
    rid = new_release(client, tokens, "API-NOTIFY-2")
    client.post(f"/api/releases/{rid}/score", headers=tokens["dev1"])
    client.post(f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
    assert sign_all_required(client, tokens, rid) == "APPROVED"
    kinds = [n["kind"] for n in client.get("/api/notifications?limit=50", headers=tokens["dev1"]).json()["notifications"]
             if n["release_id"] == rid]
    assert "approved" in kinds and "signed" in kinds


def test_stream_requires_valid_token(client):
    assert client.get("/api/notifications/stream?token=not-a-jwt").status_code == 401


def test_realtime_hub_delivers_committed_notifications(client, tokens):
    """The database NOTIFY reaches the subscriber queue of the right user only."""
    import asyncio
    import time

    from app.realtime import hub

    me = {u: client.get("/api/auth/me", headers=tokens[u]).json()["id"] for u in ("rm2", "dev2")}
    deadline = time.time() + 10
    while not hub.connected and time.time() < deadline:
        time.sleep(0.1)
    assert hub.connected, "listener did not connect to PostgreSQL"

    async def scenario():
        rm_q, dev_q = hub.subscribe(me["rm2"]), hub.subscribe(me["dev2"])
        try:
            rid = await asyncio.to_thread(new_release, client, tokens, "API-PUSH-1")
            await asyncio.to_thread(client.post, f"/api/releases/{rid}/score", headers=tokens["dev1"])
            await asyncio.to_thread(client.post, f"/api/releases/{rid}/submit", json={}, headers=tokens["dev1"])
            got = await asyncio.wait_for(rm_q.get(), timeout=5)       # review_requested for RMs
            assert isinstance(got, int)
            await asyncio.sleep(0.5)
            assert dev_q.empty(), "an unrelated developer must not receive the push"
        finally:
            hub.unsubscribe(me["rm2"], rm_q)
            hub.unsubscribe(me["dev2"], dev_q)

    asyncio.run(scenario())
