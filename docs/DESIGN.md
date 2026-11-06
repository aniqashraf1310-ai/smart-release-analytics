# Design notes and evidence

Source material for FYP II Chapters 3–5. Numbers below come from `packages/ml/reports/metrics.json` and the test suites in this repository. Re-run them after any change and update the numbers.

## 1. Architecture and mapping to the objectives

| Objective (report §1.3) | Where it is implemented |
|---|---|
| O1: ACID multi-signature governance with RBAC | `packages/db/migrations/003_governance.sql`, `004_seed_and_security.sql` |
| O2: Predictive deployment-risk model | `packages/ml/` (training, calibration, explanations) |
| O3: Unified dashboard | `packages/frontend/`: risk panel and approval panel in one release view |

The **integration point** is `approval_policies`. The tier of a release's current prediction decides how many people from each role must sign, and whether they must explicitly acknowledge the model's top risk drivers.

### 1.1 Three roles, one per user

| Role | Authority |
|---|---|
| Developer | Creates releases, records risk scores, submits. Submitting is the Developer signature. Cannot approve. |
| QA Lead | Attests testing is complete (first-line approval). Can also create releases (QA input). |
| Release Manager | Final approval, deployment, outcomes, break-glass, incident reviews and account administration. |

Each user holds exactly one role (`user_roles.user_id` is the primary key). Escalation for risk is **hierarchical through quorum**: higher tiers need *more independent senior signatures*, not extra job titles.

| Tier | QA Lead | Release Manager |
|---|---|---|
| LOW | 1 | 1 |
| MEDIUM | 1 | 1, acknowledges risk drivers |
| HIGH | 1, acknowledges | 2 different people, acknowledge |
| CRITICAL | 2 different people, acknowledge | 2 different people, acknowledge |

A role → permission matrix (`packages/api/app/permissions.py`) drives API checks and the UI. The database independently enforces every governance rule, so a mistake in the matrix could at worst hide a button or show one the database then refuses.

### 1.2 Registration and account administration

- Anyone can register (`register_user()`) and *request* a role. The account is `PENDING`, holds no role and can do nothing. This prevents self-escalation.
- A Release Manager approves (choosing the granted role, which may differ from the request), rejects, re-roles or disables accounts (`review_registration()`, `change_user_role()`, `set_user_status()`).
- Nobody can administer their own account, so at least one active Release Manager always remains.
- JWTs carry only the user id. The role and status are re-read on every request, so changes take effect immediately.
- Passwords are bcrypt-hashed in PostgreSQL (`pgcrypto`), with a policy of ≥ 8 characters including a letter and a digit. Every account action is audited.
- A promoted user cannot sign the same release revision twice (tested: "role-switch attack").

## 2. Governance framework

### 2.1 Transaction design (correction to interim report §4.2)

The interim report describes opening one `BEGIN` block per release and committing once all signatures are in. Sign-offs happen hours or days apart, so a transaction held open that long would hold locks and a connection, and it would be lost on restart. The implemented design keeps the same guarantee with short transactions:

- Each signature is **one short transaction** (`sign_approval()`).
- Inside it, the release row is locked with `SELECT … FOR UPDATE`, so concurrent signers are serialised.
- After inserting the signature, the function evaluates the policy. If it is satisfied, the release moves to `APPROVED` **in the same transaction**. Any error rolls back the whole step.
- As a second line of defence, a `BEFORE UPDATE` trigger on `releases` refuses any update that sets `APPROVED` while the policy is unsatisfied. This applies even to the database owner, so *no single row update can approve a release*.

### 2.2 Rules enforced in the database

| Rule | Mechanism |
|---|---|
| RBAC | `user_roles`; every governance function calls `require_role()` |
| Least privilege | API connects as `release_app`: column-level `UPDATE` grants on content only; no direct write access to status, approvals, predictions or audit |
| Separation of duties | one role per user; the submitter signs only as Developer; one live signature per person per revision (partial unique index), which also blocks the promote-then-sign-again attack |
| Quorum | `approval_policies.min_approvals` (2 RMs for HIGH; 2 QA Leads + 2 RMs for CRITICAL) |
| Risk acknowledgement | `requires_risk_ack`, checked in `sign_approval()` |
| Content binding | signatures store `payload_hash` = SHA-256(release content + prediction id); any material edit bumps `revision`, returns to DRAFT and invalidates signatures (trigger) |
| Expiry | signatures expire after 48 h (`governance_settings`); re-verified at deployment |
| State machine | `state_transitions` table; trigger rejects any other move |
| Break-glass | `emergency_approve()`: Release Manager only, ≥ 20-character justification, flagged, audited, opens a mandatory post-incident review that a *different* Release Manager must complete |
| Tamper-evident audit | append-only `audit_logs` (triggers block UPDATE/DELETE/TRUNCATE); each row hashes the previous one; `verify_audit_chain()` finds the first altered row |

### 2.3 Evidence

- `tests/governance_tests.sql`: **72/72 pass**. Most run as `release_app` to show the rules hold even if the API is bypassed.
- `tests/concurrency_test.py`: **20/20 quorum races correct.** In each, 6 release managers sign the same CRITICAL release at the same instant (after its two QA sign-offs): exactly 2 signatures are accepted, the release is approved exactly once, and the other 4 are refused cleanly.
- Load test: 8 concurrent workers, 80 releases, 160 sign-offs, **0 deadlocks**, audit chain intact. In-database `sign_approval()` latency was p50 ≈ 21–25 ms and p95 ≈ 42–54 ms across runs on the development sandbox. Re-measure on your own machine for the report.

## 3. Predictive risk engine

### 3.1 Data and the circularity problem

Real deployment logs are confidential, so data is synthetic (`risk_model/synthetic.py`, 6,000 records, 19.4% failures). If labels were a deterministic function of the features, any model would rediscover the rule and accuracy would prove nothing. The generator therefore includes:

- **hidden factors** the model never sees (team experience, infrastructure instability);
- Gaussian noise on the log-odds;
- **3% flipped labels**.

The true probability of each record is kept only to compute the **Bayes ceiling**, the best ROC-AUC any model could reach on this data: **0.740** on the test set.

**Features:** change type, target environment, patch size, files changed, test coverage, ticket complexity, module failure history, DB migration, dependency changes, and Friday/weekend deployment (derived from the target date).

### 3.2 Method

1. 80/20 stratified split (test set untouched until the end).
2. Random-forest grid search (5-fold, PR-AUC). Best: `max_depth=8, min_samples_leaf=20, max_features=sqrt`.
3. 5-fold stratified CV comparison with a logistic-regression baseline.
4. **Selection rule, fixed in advance:** the higher mean CV PR-AUC wins. If the gap is under one standard deviation, the models count as tied and the random forest is kept, as pre-registered in report §3.3.
5. Sigmoid (Platt) calibration, so displayed probabilities are meaningful.
6. Risk tiers derived from out-of-fold predictions (never from the test set).

### 3.3 Results

5-fold CV on the training set (mean ± sd):

| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| Logistic regression | 0.679 ± 0.019 | 0.356 ± 0.018 | 0.145 |
| Random forest (tuned) | 0.672 ± 0.010 | 0.345 ± 0.015 | 0.147 |

The PR-AUC gap (0.012) is inside one SD, so the models are **statistically tied**. This is a legitimate finding worth discussing. The generator is mostly additive in log-odds, with one interaction (patch size × coverage), and that structure suits a linear model. It also shows the synthetic data was not tailored to favour the random forest.

Held-out test set (n = 1,200):

| | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| Selected (RF, calibrated) | 0.720 | 0.370 | 0.142 |
| Logistic regression | 0.716 | 0.361 | 0.142 |
| No-skill baseline | 0.500 | 0.193 | — |
| Bayes ceiling | 0.740 | — | — |

The selected model is within 0.02 ROC-AUC of the ceiling. Most of the remaining error is the irreducible noise built into the data.

### 3.4 Risk tiers

A single cost-optimal cut-off (a missed failure costing 10× an unnecessary review) would flag **88%** of releases, which is operationally useless. Tiers are therefore sized to review capacity: LOW 50%, MEDIUM 30%, HIGH 15%, CRITICAL 5%.

Observed failure rates per tier on the test set show clean separation:

| Tier | Share | Actually failed |
|---|---|---|
| LOW | 55% | 10.6% |
| MEDIUM | 26% | 23.1% |
| HIGH | 15% | 36.0% |
| CRITICAL | 4% | 50.9% |

Figures in `packages/ml/reports/`: `reliability_diagram.png`, `precision_recall.png`, `tier_failure_rates.png` and `feature_importance.png`. Test coverage is by far the most important feature, followed by target environment and patch size.

### 3.5 Explanations, what-if, drift

- **Per-release explanation:** tree-path decomposition (Saabas). It is exactly additive (base value + contributions = the forest's raw vote), which the unit tests check. SHAP would share credit between interacting features more fairly; it is listed as future work.
- **What-if and mitigations:** re-score with changed actionable features, e.g. "raise test coverage to 80%", showing the new probability and tier for each option.
- **Reproducibility:** every prediction stores `model_version_id`, the exact `feature_snapshot` and the explanation.
- **Drift:** Population Stability Index of recent inputs against the training data (`/api/ml/drift`).
- **Feedback loop:** outcomes are exported with the features that were scored, for retraining.

## 4. Notifications

- `notifications` rows are created by an `AFTER INSERT` trigger on `audit_logs` (`notify_from_audit()`). Every governance action is already audited, so the audit log is the single source for both history and alerts. Alerts are produced in the same transaction as the action, so a rolled-back action alerts nobody.
- Recipients are computed in the database: role members (e.g. all active QA Leads and Release Managers for a new submission) and release stakeholders (creator, submitter and every signer). The actor is always excluded.
- The API only returns the caller's own notifications, and marking one as read goes through functions that check ownership. The application role cannot modify notification content.
- **Real-time delivery.** An `AFTER INSERT` trigger on `notifications` calls `pg_notify('sras_notifications', '<user>:<id>')`. PostgreSQL delivers NOTIFY only on COMMIT. One API thread `LISTEN`s on the channel and forwards each event to that user's open Server-Sent-Events connections (`/api/notifications/stream`). The browser then fetches the notification through the normal authenticated endpoint, so the push carries no content. Measured end to end through the dev server: about 0.2–0.3 s from an approval/rejection to the toast on the other role's screen (about 24 ms at the API). A 30-second poll is kept only as a fallback while the live connection is down. The stream re-checks every minute that the account is still active.
- **Edits.** Every change to a release's overview information is audited with each changed field's old and new value (`details.changes`, `008_edit_notifications.sql`). It notifies the creator, submitter and signers, and every QA Lead and Release Manager once the release has been submitted, e.g. "Farid (Release Manager) updated … : QA status: Ready for UAT → Pass; Progression updated". Content edits after sign-off also say that signatures were invalidated.
- **Recipients for sign-offs.** Approvals and rejections notify every active QA Lead and Release Manager as well as the stakeholders, so a QA approval reaches the Release Managers and vice versa. Approval messages state which sign-offs are still outstanding.
- Tests: 24 database checks (recipients in both directions, edits by QA Leads and Release Managers with old → new values, actor exclusion, ownership of read state, immutability) and 5 API tests (rejection reason reaches the developer, approval reaches the developer, a reviewer's edit reaches the developer, the push reaches only the right user, the stream rejects bad tokens). `pg_notify` was also checked to fire only on commit.

## 5. Demo data

`packages/api/app/demo_seed.py` creates 20 releases (five staged one action away from each kind of notification), a pending account request and some reviewer edits, through the real model and governance functions, one short transaction per step, so the scores, signatures, audit chain and notifications are genuine. It runs on API start-up when the database is empty.

## 6. Limitations (for Chapter 5)

- The model is trained on synthetic data. The discrimination reported is bounded by the generator's own noise, and real-world performance is unknown until real outcomes accumulate. Validating on a public just-in-time defect dataset would strengthen external validity.
- The latency figures come from a single-machine sandbox, not a production-like deployment.
- Authentication uses demo accounts and a shared JWT secret. Production would need SSO/MFA. The database trusts the API for *identity*, but not for *authorisation*.
- Signatures are recorded approvals bound to a content hash, not cryptographic per-user signatures. Ed25519 keys per approver are possible future work.
- Delegation of approval authority (e.g. during leave) is not implemented.
- There is no email or mobile push delivery; notifications reach users only while the web app is open (or via browser desktop notifications).
- With several API worker processes, each one listens and serves its own connections, which works but means one database connection per worker.
