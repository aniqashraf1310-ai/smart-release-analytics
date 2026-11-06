-- =====================================================================
-- 001_core.sql
-- Core entities: roles, users, releases (deployment requests),
-- model versions and risk predictions.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- Roles and users (RBAC)
-- ---------------------------------------------------------------------
CREATE TABLE roles (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE users (
    id             SERIAL PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL CHECK (username ~ '^[a-z0-9._-]{3,32}$'),
    full_name      TEXT NOT NULL CHECK (length(trim(full_name)) BETWEEN 2 AND 100),
    email          TEXT UNIQUE CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    password_hash  TEXT NOT NULL,             -- bcrypt via pgcrypto crypt()
    -- Self-registered accounts start PENDING and cannot do anything until a
    -- Release Manager approves them and assigns the role.
    status         TEXT NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING', 'ACTIVE', 'REJECTED', 'DISABLED')),
    is_active      BOOLEAN GENERATED ALWAYS AS (status = 'ACTIVE') STORED,
    requested_role TEXT REFERENCES roles(code),
    reviewed_by    INT REFERENCES users(id),
    reviewed_at    TIMESTAMPTZ,
    review_note    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Exactly one role per user: a person is a Developer OR a QA Lead OR a
-- Release Manager. This keeps separation of duties simple to reason about.
CREATE TABLE user_roles (
    user_id   INT  PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    role_code TEXT NOT NULL REFERENCES roles(code)
);

-- ---------------------------------------------------------------------
-- Governance status of a release (the state machine is in 002)
-- ---------------------------------------------------------------------
CREATE TYPE gov_status AS ENUM (
    'DRAFT',        -- being prepared by a developer; editable
    'IN_REVIEW',    -- submitted with a risk score; collecting sign-offs
    'APPROVED',     -- every signature the policy requires is present and valid
    'REJECTED',     -- an approver rejected it; developer may revise and resubmit
    'DEPLOYING',    -- release manager has started the deployment
    'DEPLOYED',     -- terminal: success
    'FAILED',       -- terminal: deployment failed
    'ROLLED_BACK',  -- terminal: deployed then rolled back
    'CANCELLED'     -- terminal: withdrawn
);

CREATE TYPE risk_tier AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');

-- ---------------------------------------------------------------------
-- Releases (= deployment requests)
-- The first block keeps the fields the existing QA tracker uses.
-- The second block holds the metadata the predictive risk model consumes.
-- ---------------------------------------------------------------------
CREATE TABLE releases (
    id                  SERIAL PRIMARY KEY,

    -- QA tracker fields (existing app)
    app_name            TEXT NOT NULL,
    version             TEXT NOT NULL CHECK (version ~ '^[0-9]+(\.[0-9]+)*$'),
    target_date         DATE NOT NULL,
    qa_status           TEXT NOT NULL DEFAULT 'Open',
    responsible_qa      TEXT NOT NULL,
    ticket_id           TEXT UNIQUE NOT NULL,
    fix_type            TEXT NOT NULL,
    description         TEXT NOT NULL,
    progression         TEXT NOT NULL DEFAULT '',
    notes               TEXT,

    -- Risk model features
    change_type         TEXT NOT NULL DEFAULT 'feature'
                        CHECK (change_type IN ('hotfix', 'feature', 'config', 'refactor')),
    target_env          TEXT NOT NULL DEFAULT 'production'
                        CHECK (target_env IN ('development', 'staging', 'production')),
    patch_size_loc      INT  NOT NULL DEFAULT 0   CHECK (patch_size_loc >= 0),
    files_changed       INT  NOT NULL DEFAULT 1   CHECK (files_changed >= 0),
    test_coverage       NUMERIC(5,2) NOT NULL DEFAULT 0
                        CHECK (test_coverage BETWEEN 0 AND 100),
    ticket_complexity   SMALLINT NOT NULL DEFAULT 3 CHECK (ticket_complexity BETWEEN 1 AND 5),
    module_failure_rate NUMERIC(4,3) NOT NULL DEFAULT 0
                        CHECK (module_failure_rate BETWEEN 0 AND 1),
    has_db_migration    BOOLEAN NOT NULL DEFAULT FALSE,
    dependency_changes  INT NOT NULL DEFAULT 0 CHECK (dependency_changes >= 0),

    -- Governance
    gov_status          gov_status NOT NULL DEFAULT 'DRAFT',
    revision            INT NOT NULL DEFAULT 1,   -- bumped on every material edit
    is_emergency        BOOLEAN NOT NULL DEFAULT FALSE,
    created_by          INT NOT NULL REFERENCES users(id),
    submitted_by        INT REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_releases_status ON releases(gov_status);
CREATE INDEX idx_releases_target ON releases(target_date);

-- ---------------------------------------------------------------------
-- Model registry and predictions
-- ---------------------------------------------------------------------
CREATE TABLE model_versions (
    id            SERIAL PRIMARY KEY,
    version_tag   TEXT UNIQUE NOT NULL,
    algorithm     TEXT NOT NULL,
    trained_at    TIMESTAMPTZ NOT NULL,
    metrics       JSONB NOT NULL,
    thresholds    JSONB NOT NULL,        -- tier cut-offs used by this model
    artifact_path TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT FALSE,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one active model at a time.
CREATE UNIQUE INDEX uq_one_active_model ON model_versions(is_active) WHERE is_active;

CREATE TABLE risk_predictions (
    id               SERIAL PRIMARY KEY,
    release_id       INT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    release_revision INT NOT NULL,
    model_version_id INT NOT NULL REFERENCES model_versions(id),
    probability      NUMERIC(6,5) NOT NULL CHECK (probability BETWEEN 0 AND 1),
    risk_tier        risk_tier NOT NULL,
    feature_snapshot JSONB NOT NULL,     -- exact inputs, so any score can be reproduced
    explanation      JSONB NOT NULL,     -- per-feature contributions
    created_by       INT REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pred_release ON risk_predictions(release_id, release_revision, id DESC);

-- ---------------------------------------------------------------------
-- Deployment outcomes: ground truth fed back into model retraining
-- ---------------------------------------------------------------------
CREATE TABLE deployment_outcomes (
    id             SERIAL PRIMARY KEY,
    release_id     INT NOT NULL UNIQUE REFERENCES releases(id),
    prediction_id  INT REFERENCES risk_predictions(id),
    outcome        gov_status NOT NULL CHECK (outcome IN ('DEPLOYED', 'FAILED', 'ROLLED_BACK')),
    notes          TEXT,
    recorded_by    INT NOT NULL REFERENCES users(id),
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
