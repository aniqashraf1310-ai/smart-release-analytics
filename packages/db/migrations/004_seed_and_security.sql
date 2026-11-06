-- =====================================================================
-- 004_seed_and_security.sql
-- Reference data (roles, policies, state machine), demo users/releases,
-- and least-privilege grants for the API's database role.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Roles - three levels of authority:
--   DEVELOPER        prepares releases and submits them (cannot approve)
--   QA_LEAD          attests testing is complete (first-line approval)
--   RELEASE_MANAGER  approves go-live, deploys, administers accounts
-- ---------------------------------------------------------------------
INSERT INTO roles(code, name, description) VALUES
 ('DEVELOPER',       'Developer',       'Creates releases, records risk scores and submits them for review'),
 ('QA_LEAD',         'QA Lead',         'Attests that testing is complete; first-line approver'),
 ('RELEASE_MANAGER', 'Release Manager', 'Final approver; deploys releases, uses break-glass and manages user accounts');

INSERT INTO governance_settings(key, value, description) VALUES
 ('approval_ttl_hours',     '48', 'A signature expires this many hours after it is given'),
 ('emergency_review_hours', '72', 'Post-incident review due this many hours after break-glass');

-- ---------------------------------------------------------------------
-- Risk-adaptive approval policy (data, not code - change it with SQL)
-- ---------------------------------------------------------------------
INSERT INTO approval_policies(risk_tier, role_code, min_approvals, requires_risk_ack) VALUES
 ('LOW',      'DEVELOPER',       1, FALSE),
 ('LOW',      'QA_LEAD',         1, FALSE),
 ('LOW',      'RELEASE_MANAGER', 1, FALSE),

 ('MEDIUM',   'DEVELOPER',       1, FALSE),
 ('MEDIUM',   'QA_LEAD',         1, FALSE),
 ('MEDIUM',   'RELEASE_MANAGER', 1, TRUE),    -- RM must acknowledge the risk drivers

 ('HIGH',     'DEVELOPER',       1, FALSE),
 ('HIGH',     'QA_LEAD',         1, TRUE),
 ('HIGH',     'RELEASE_MANAGER', 2, TRUE),    -- quorum: two different release managers

 ('CRITICAL', 'DEVELOPER',       1, FALSE),
 ('CRITICAL', 'QA_LEAD',         2, TRUE),    -- two different QA leads
 ('CRITICAL', 'RELEASE_MANAGER', 2, TRUE);    -- and two different release managers

-- ---------------------------------------------------------------------
-- State machine
-- ---------------------------------------------------------------------
INSERT INTO state_transitions(from_status, to_status, actor, description) VALUES
 ('DRAFT',     'IN_REVIEW',   'DEVELOPER',       'Submit a risk-scored release for review'),
 ('DRAFT',     'CANCELLED',   'CREATOR/RELEASE_MANAGER', 'Withdraw'),
 ('IN_REVIEW', 'APPROVED',    'SYSTEM',          'All signatures required by the policy are present (or break-glass by a RELEASE_MANAGER)'),
 ('IN_REVIEW', 'REJECTED',    'REVIEWER',        'A required reviewer rejected the release'),
 ('IN_REVIEW', 'DRAFT',       'SYSTEM',          'Material edit: re-score and re-sign'),
 ('IN_REVIEW', 'CANCELLED',   'CREATOR/RELEASE_MANAGER', 'Withdraw'),
 ('REJECTED',  'DRAFT',       'SYSTEM',          'Material edit after rejection'),
 ('REJECTED',  'CANCELLED',   'CREATOR/RELEASE_MANAGER', 'Withdraw'),
 ('APPROVED',  'DEPLOYING',   'RELEASE_MANAGER', 'Start deployment (signatures re-verified)'),
 ('APPROVED',  'IN_REVIEW',   'SYSTEM',          'Signatures expired before deployment'),
 ('APPROVED',  'DRAFT',       'SYSTEM',          'Material edit after approval'),
 ('APPROVED',  'CANCELLED',   'CREATOR/RELEASE_MANAGER', 'Withdraw'),
 ('DEPLOYING', 'DEPLOYED',    'RELEASE_MANAGER', 'Deployment succeeded'),
 ('DEPLOYING', 'FAILED',      'RELEASE_MANAGER', 'Deployment failed'),
 ('DEPLOYING', 'ROLLED_BACK', 'RELEASE_MANAGER', 'Deployment rolled back'),
 ('DEPLOYED',  'ROLLED_BACK', 'RELEASE_MANAGER', 'Rolled back after go-live');

-- ---------------------------------------------------------------------
-- Model registry writer (used by the API at start-up)
-- ---------------------------------------------------------------------
CREATE FUNCTION register_model(p_tag TEXT, p_algorithm TEXT, p_trained_at TIMESTAMPTZ,
                               p_metrics JSONB, p_thresholds JSONB, p_path TEXT)
RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_id INT;
BEGIN
    SELECT id INTO v_id FROM model_versions WHERE version_tag = p_tag;
    IF v_id IS NULL THEN
        UPDATE model_versions SET is_active = FALSE WHERE is_active;
        INSERT INTO model_versions(version_tag, algorithm, trained_at, metrics, thresholds,
                                   artifact_path, is_active)
        VALUES (p_tag, p_algorithm, p_trained_at, p_metrics, p_thresholds, p_path, TRUE)
        RETURNING id INTO v_id;
        PERFORM write_audit(NULL, 'MODEL_ACTIVATED', 'model_version', v_id,
            jsonb_build_object('version_tag', p_tag, 'algorithm', p_algorithm));
    ELSIF NOT (SELECT is_active FROM model_versions WHERE id = v_id) THEN
        UPDATE model_versions SET is_active = FALSE WHERE is_active;
        UPDATE model_versions SET is_active = TRUE WHERE id = v_id;
        PERFORM write_audit(NULL, 'MODEL_ACTIVATED', 'model_version', v_id,
            jsonb_build_object('version_tag', p_tag, 'reactivated', TRUE));
    END IF;
    RETURN v_id;
END $$;

-- ---------------------------------------------------------------------
-- Authentication helper (bcrypt via pgcrypto)
-- ---------------------------------------------------------------------
CREATE FUNCTION authenticate(p_username TEXT, p_password TEXT)
RETURNS TABLE(id INT, username TEXT, full_name TEXT, status TEXT, roles TEXT[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    -- Returns the account whatever its status, so the API can tell a wrong
    -- password apart from "awaiting approval". Roles are only granted to
    -- ACTIVE accounts, and has_role() also requires ACTIVE.
    SELECT u.id, u.username, u.full_name, u.status,
           coalesce(array_agg(ur.role_code) FILTER (WHERE ur.role_code IS NOT NULL AND u.status = 'ACTIVE'), '{}')
    FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id
    WHERE u.username = lower(p_username)
      AND u.password_hash = crypt(p_password, u.password_hash)
    GROUP BY u.id;
$$;

-- ---------------------------------------------------------------------
-- Demo users (password for all: Passw0rd!)  - change before any real use
-- ---------------------------------------------------------------------
INSERT INTO users(username, full_name, email, password_hash, status, requested_role) VALUES
 ('dev1', 'Aiman (Developer)',          'dev1@example.com', crypt('Passw0rd!', gen_salt('bf', 10)), 'ACTIVE', 'DEVELOPER'),
 ('dev2', 'Hana (Developer)',           'dev2@example.com', crypt('Passw0rd!', gen_salt('bf', 10)), 'ACTIVE', 'DEVELOPER'),
 ('qa1',  'Azila (QA Lead)',            'qa1@example.com',  crypt('Passw0rd!', gen_salt('bf', 10)), 'ACTIVE', 'QA_LEAD'),
 ('qa2',  'Akma (QA Lead)',             'qa2@example.com',  crypt('Passw0rd!', gen_salt('bf', 10)), 'ACTIVE', 'QA_LEAD'),
 ('rm1',  'Farid (Release Manager)',    'rm1@example.com',  crypt('Passw0rd!', gen_salt('bf', 10)), 'ACTIVE', 'RELEASE_MANAGER'),
 ('rm2',  'Mei Ling (Release Manager)', 'rm2@example.com',  crypt('Passw0rd!', gen_salt('bf', 10)), 'ACTIVE', 'RELEASE_MANAGER');

INSERT INTO user_roles(user_id, role_code)
SELECT id, requested_role FROM users;

-- Demo releases are not created here: packages/api/app/demo_seed.py creates
-- 15 of them through the real risk model and approval workflow, so their
-- scores, signatures, audit trail and notifications are all genuine.

-- ---------------------------------------------------------------------
-- Least-privilege role for the API
--   * can read everything
--   * can create releases and edit their content columns
--   * can change governance state ONLY through the functions below
--   * cannot UPDATE/DELETE approvals, predictions or audit logs
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'release_app') THEN
        CREATE ROLE release_app LOGIN PASSWORD 'release_app';
    END IF;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO release_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO release_app;
REVOKE SELECT ON users FROM release_app;
GRANT SELECT (id, username, full_name, email, status, is_active, requested_role, reviewed_by,
              reviewed_at, review_note, created_at) ON users TO release_app;

GRANT INSERT ON releases TO release_app;
GRANT USAGE ON SEQUENCE releases_id_seq TO release_app;
GRANT UPDATE (app_name, version, target_date, qa_status, responsible_qa, ticket_id, fix_type,
              description, progression, notes, change_type, target_env, patch_size_loc,
              files_changed, test_coverage, ticket_complexity, module_failure_rate,
              has_db_migration, dependency_changes)
    ON releases TO release_app;

GRANT EXECUTE ON FUNCTION
    authenticate(TEXT, TEXT),
    register_model(TEXT, TEXT, TIMESTAMPTZ, JSONB, JSONB, TEXT),
    record_prediction(INT, INT, INT, NUMERIC, risk_tier, JSONB, JSONB),
    submit_release(INT, INT, TEXT),
    sign_approval(INT, INT, TEXT, TEXT, TEXT, BOOLEAN),
    start_deployment(INT, INT),
    record_outcome(INT, INT, gov_status, TEXT),
    emergency_approve(INT, INT, TEXT),
    complete_incident_review(INT, INT, TEXT),
    cancel_release(INT, INT, TEXT),
    delete_release(INT, INT),
    approval_progress(INT),
    policy_satisfied(INT),
    release_payload_hash(INT),
    current_prediction(INT),
    verify_audit_chain(),
    has_role(INT, TEXT),
    -- invoked by triggers / views under the caller's privileges
    releases_before_insert(), releases_before_update(), audit_row_digest(BIGINT, TIMESTAMPTZ, INT, TEXT, TEXT, INT, JSONB, TEXT),
    gov_setting_int(TEXT)
TO release_app;
