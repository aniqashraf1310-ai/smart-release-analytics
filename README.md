# Smart Release Analytic System

An intelligent app release management system for a regulated fintech environment. It combines two engines:

1. **Predictive risk engine** (Python, scikit-learn). Scores every release candidate's probability of failing, explains *why*, and suggests mitigations.
2. **Hierarchical governance framework** (PostgreSQL). Risk-adaptive, multi-signature approval with separation of duties, an enforced state machine and a tamper-evident audit log. All of it is enforced **inside the database**.

The integration point is the **risk tier**. The model's score decides how much sign-off a release needs: one QA Lead and one Release Manager for a low-risk change, up to two QA Leads *and* two Release Managers for a critical one.

```
React 19 (Vite, Tailwind)  ──►  FastAPI (JWT auth)  ──►  PostgreSQL 16
  role-aware dashboards          thin API layer           governance functions + triggers
  live risk preview              risk engine (sklearn)    audit hash chain, RBAC grants
```

| Package | What it contains |
|---|---|
| `packages/db` | SQL migrations (schema, audit chain, governance functions, seed data, grants) and database test suites |
| `packages/ml` | Synthetic data generator, training pipeline, risk engine (scoring, explanations, what-if, drift) and tests |
| `packages/api` | FastAPI application and end-to-end API tests |
| `packages/frontend` | React UI |
| `docs/DESIGN.md` | Design decisions, evidence and limitations (source material for the report) |

## Quick start (Docker)

```bash
docker compose up --build
```

- App: http://localhost:3000
- API docs (Swagger): http://localhost:5000/docs

The first build trains the model, which takes about a minute. The database is created automatically, and the API fills it with 15 demo releases the first time it starts. Run `docker compose down -v` to start from scratch again.

### Demo accounts (password `Passw0rd!`)

| User | Role |
|---|---|
| `dev1`, `dev2` | Developer |
| `qa1`, `qa2` | QA Lead |
| `rm1`, `rm2` | Release Manager |

New users can also use **Create an account** on the sign-in page (see below).

### Roles and access

Every user holds exactly one of three roles:

| | Developer | QA Lead | Release Manager |
|---|:-:|:-:|:-:|
| View releases, risk scores, audit log, model | ✓ | ✓ | ✓ |
| Create / edit releases | ✓ | ✓ | edit only |
| Record risk score, use what-if | ✓ | ✓ | what-if only |
| Submit for review (Developer signature) | ✓ | | |
| Approve / reject as QA Lead | | ✓ | |
| Final approval as Release Manager | | | ✓ |
| Deploy, record outcome, break-glass, incident review | | | ✓ |
| Approve registrations, change roles, disable accounts | | | ✓ |

The table lives in one place, `packages/api/app/permissions.py`. The API enforces it, the UI reads it to show or hide menus and buttons, and PostgreSQL re-checks every governance action on its own.

Required signatures per risk tier (`approval_policies` table):

| Tier | Developer | QA Lead | Release Manager |
|---|:-:|:-:|:-:|
| LOW | submits | 1 | 1 |
| MEDIUM | submits | 1 | 1 + acknowledges risk drivers |
| HIGH | submits | 1 + acknowledges | **2 different people** + acknowledge |
| CRITICAL | submits | **2 different people** + acknowledge | **2 different people** + acknowledge |

### Registration

1. On the sign-in page, choose **Create an account**, pick the role you need and fill in your details.
2. The account is created as **pending**. It has no role and cannot do anything, so nobody can make themselves a Release Manager by filling in a form.
3. A Release Manager opens **Accounts**, sees the request (the menu shows a count badge) and approves it. They can grant a different role from the one requested, or reject the request.
4. Release Managers can later change a user's role or disable the account. Changes take effect on the user's next request. Nobody can change or disable their own account, so at least one Release Manager always remains.

### Demo data

When the API starts against an empty database it creates **20 demo releases**, scored by the real model and taken through the real workflow by the demo accounts. Every state is represented: draft, scored draft, in review (waiting and part-signed), approved, rejected, deploying, deployed, failed, rolled back, cancelled, edited after approval, an emergency break-glass release with a pending incident review, and releases staged one click away from each kind of notification (see below). It also adds a pending account request and a few edits made by QA Leads and Release Managers. Each account starts with its 5 newest notifications unread. Set `SEED_DEMO=0` to start empty instead, or run `python packages/api/seed_demo.py --force` to add the set again.

### Notifications

The bell in the header shows unread notifications. They arrive **instantly**: when someone approves or rejects a release, the database signals the API as soon as the action commits, and the API pushes it over a live connection (Server-Sent Events) to every open tab of the people concerned. In testing, a Release Manager's approval appeared on a QA Lead's screen in about 0.3 s, and the reverse in about 0.2 s. A green **● Live** dot on the bell shows the connection is up. If it drops (for example while the API restarts), the app reconnects by itself and checks every 30 seconds in the meantime. Each new notification pops up as a toast you can click to open the release. If you turn on **desktop alerts** in the bell menu, you also get a system notification while the tab is in the background.

| Event | Who is notified |
|---|---|
| Release submitted | Every QA Lead and Release Manager |
| Sign-off given (says which sign-offs are still outstanding) | Every QA Lead and Release Manager, plus the creator, submitter and earlier signers |
| Release rejected (with the reason) | Every QA Lead and Release Manager, plus the creator, submitter and signers |
| Fully approved, ready to deploy | Creator, submitter, signers and all Release Managers |
| Deployment started / deployed / failed / rolled back | Creator, submitter and signers |
| Overview info edited by anyone (says what changed, old → new) | Creator, submitter and signers; once submitted, also every QA Lead and Release Manager |
| Content edited after sign-off (signatures invalidated) | Same as above |
| Approvals expired, cancelled | Creator, submitter and signers |
| Emergency break-glass | Stakeholders and all Release Managers |
| New account request | All Release Managers |
| Account approved / role changed | That user |

Notifications are created by a database trigger on the audit log (`006_notifications.sql`), in the same transaction as the action itself. A second trigger (`007_realtime.sql`) raises a PostgreSQL `NOTIFY`, which is only delivered when that transaction commits, so a failed or rolled-back action never alerts anyone. You are never notified about your own actions.

### Notification demo scenarios

Open two browser windows, for example a normal one and a private one, and sign in as different users (password `Passw0rd!`). Do the action in one window and watch the toast and bell appear in the other.

| # | Sign in as | Do this | Watch this user | They see |
|---|---|---|---|---|
| 1 | `qa1` or `qa2` | **MBA-1310** → Approvals → *Approve as QA Lead* | `rm1` | "Sign-off received … Still waiting for: Release Manager (1 more)" |
| 2 | `rm1` | **WEB-815** → Approvals → *Approve as Release Manager* (QA already signed) | `dev1`, `qa1` | "Release approved … ready to deploy" |
| 3 | `rm2` | **PAY-340** → Approvals → tick the risk box → *Approve as Release Manager* (last signature of a critical release) | `dev1`, `qa1`, `rm1` | "Release approved" |
| 4 | `qa2` | **LOY-201** → Approvals → type a reason → *Reject* | `dev1`, `rm1` | "Release rejected … as QA Lead: "your reason"" |
| 5 | `rm1` | **HP19-506** → Overview → *Edit* → change QA status → *Save* | `dev1`, `qa1` | "Release details updated: QA status: Ready for UAT → Pass" |
| 6 | `qa1` | **MOS-12** → Overview → *Edit* → change responsible QA or progression | `dev2`, `rm1` | "Release details updated" |
| 7 | `dev1` | **ADM-95** → Approvals → *Submit for review* | `qa1`, `rm1` | "Review requested" |
| 8 | `rm1` | **LOY-215** → Approvals → *Start deployment*, then *Mark deployed* | `dev2` | "Deployment started", "Deployed successfully" |
| 9 | `rm1` | Accounts → approve **Nurul Izzati** (`nurul`) | `nurul` (password `Passw0rd1`) | "Welcome aboard" |

Each staged release can only be used once. Run `npm run db:setup` and restart `npm run dev` to get a fresh set.

### Demo walkthrough

1. Sign in as **dev1**, open a draft release, go to **Risk analysis** and press **Record risk score**. Try the what-if sliders.
2. On the **Approvals** tab, press **Submit for review**. The required sign-offs appear, and they depend on the tier.
3. Sign out, sign in as **qa1**, and approve. For MEDIUM risk and above, the approver must tick the risk acknowledgement.
4. Sign in as **rm1** and approve. HIGH risk also needs **rm2**. CRITICAL needs **qa2** and **rm2** as well.
5. Once approved, **rm1** starts the deployment and records the outcome. The outcome feeds retraining.
6. Edit a material field of an approved release (e.g. patch size). It goes back to **Draft** and every signature is invalidated.
7. The **Audit Log** page shows every action and verifies the SHA-256 hash chain.
8. Keep **dev1** signed in in one browser and sign in as **qa2** in another (or a private window). When qa2 rejects one of dev1's releases, dev1 gets a toast and a bell notification within about 10 seconds.
9. Register a new account. The Release Managers get a notification. Sign in as **rm1** and approve it on the **Accounts** page.

## Local development (Windows, macOS, Linux)

Prerequisites: Python 3.11+, Node 20+, and PostgreSQL 16. The easiest way to get PostgreSQL is Docker Desktop.

Run these from the project root, in VS Code's terminal (PowerShell or cmd is fine):

```bash
# 1. install Python + frontend packages (once)
npm install
npm run setup

# 2. start PostgreSQL (Docker creates and migrates the database on first start)
docker compose up -d db

# 3. train the risk model (once, about a minute)
npm run train

# 4. start the API (port 5000) and the frontend (port 3000) together
npm run dev
```

Open http://localhost:3000. Check the API with http://localhost:5000/api/health.

- **Upgrade without losing data:** `npm run db:upgrade` applies only the migrations your database hasn't had yet. Use it after downloading a newer version.
- **Database reset / no Docker:** `npm run db:setup` (re)creates the database from the migrations using Python only. It drops the existing database first. For your own PostgreSQL install, set `PG_ADMIN_URL` first, e.g. PowerShell `$env:PG_ADMIN_URL="postgresql://postgres:YOURPASSWORD@localhost:5432/postgres"`.
- **Database already existed from an older version** (e.g. no registration): `docker compose down -v` then `docker compose up -d db`, or run `npm run db:setup`.
- **`No module named ...`:** the packages went into a different Python. Run `python -m pip install -r packages/api/requirements.txt` with the same `python` that starts the API.

## Tests

| Command | What it proves |
|---|---|
| `npm run test:db` | 96 governance checks hold at the database level, running as the restricted API role: RBAC, registration and account administration, separation of duties, quorum, risk acknowledgement, content binding, expiry, break-glass, state machine, append-only audit, tamper detection, notifications |
| `npm run test:concurrency` | 20 races of 6 release managers signing simultaneously produce exactly 2 signatures and one approval, with no deadlocks; also reports sign-off latency under 8 concurrent workers |
| `npm run test:ml` | Features, reproducibility, additive explanations, what-if, mitigations, drift detection |
| `npm run test:api` | 18 end-to-end HTTP tests: register → approve → login → create → score → submit → sign → deploy → outcome, role-boundary refusals, notifications, the real-time push channel and the demo data |

All four suites run on every push via GitHub Actions (`.github/workflows/ci.yml`), together with a production build of the frontend.

## Retraining with real outcomes

Every recorded deployment outcome is stored with the exact features that were scored. Export them and retrain:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:5000/api/ml/training-export > outcomes.csv
cd packages/ml && python train.py --extra ../../outcomes.csv
```

Restart the API to activate the new model. Each prediction keeps its `model_version_id` and feature snapshot, so old scores remain reproducible.
