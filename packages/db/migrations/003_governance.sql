-- =====================================================================
-- 003_governance.sql
-- Hierarchical, risk-adaptive multi-signature governance.
--
-- Design principles (see docs/GOVERNANCE.md):
--  * Each signature is its own short ACID transaction. No transaction is
--    held open while waiting for humans.
--  * The release row is locked (SELECT ... FOR UPDATE) whenever a signature
--    is added, so concurrent signers are serialised and the APPROVED
--    transition is evaluated exactly once, on consistent data.
--  * Three roles only - DEVELOPER, QA_LEAD, RELEASE_MANAGER - each user holds
--    exactly one. Higher risk means MORE independent signatures from the
--    senior roles (quorum), not extra roles.
--  * The required signatures depend on the risk tier of the release's
--    current prediction (approval_policies) - the integration point of the
--    predictive engine and the governance engine.
--  * A trigger on releases makes it impossible for ANY single UPDATE
--    (even by a superuser) to set gov_status = 'APPROVED' unless the
--    policy is satisfied or an audited break-glass approval is in effect.
--  * Separation of duties: the submitter signs only as DEVELOPER, and one
--    person can hold at most one signature per release revision.
--  * Signatures are bound to the release content: any material edit bumps
--    the revision, returns the release to DRAFT and invalidates signatures.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Configuration tables
-- ---------------------------------------------------------------------
CREATE TABLE governance_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE approval_policies (
    risk_tier         risk_tier NOT NULL,
    role_code         TEXT NOT NULL REFERENCES roles(code),
    min_approvals     INT  NOT NULL CHECK (min_approvals >= 1),
    requires_risk_ack BOOLEAN NOT NULL DEFAULT FALSE, -- signer must acknowledge risk drivers
    PRIMARY KEY (risk_tier, role_code)
);

-- Allowed state-machine edges. actor = 'SYSTEM' means the move is made
-- automatically by a governance function, never directly by a user.
CREATE TABLE state_transitions (
    from_status gov_status NOT NULL,
    to_status   gov_status NOT NULL,
    actor       TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (from_status, to_status)
);

-- ---------------------------------------------------------------------
-- Approvals (one row per signature)
-- ---------------------------------------------------------------------
CREATE TABLE approvals (
    id                 SERIAL PRIMARY KEY,
    release_id         INT  NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    release_revision   INT  NOT NULL,
    prediction_id      INT  NOT NULL REFERENCES risk_predictions(id),
    role_code          TEXT NOT NULL REFERENCES roles(code),
    user_id            INT  NOT NULL REFERENCES users(id),
    decision           TEXT NOT NULL CHECK (decision IN ('APPROVE', 'REJECT')),
    comment            TEXT,
    acknowledged_risk  BOOLEAN NOT NULL DEFAULT FALSE,
    payload_hash       TEXT NOT NULL,       -- hash of release content + prediction at signing
    signed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at         TIMESTAMPTZ NOT NULL,
    invalidated_at     TIMESTAMPTZ,
    invalidated_reason TEXT,
    CHECK (decision = 'APPROVE' OR length(trim(coalesce(comment, ''))) > 0)
);
-- One live signature per person per release revision (separation of duties).
CREATE UNIQUE INDEX uq_one_signature_per_user
    ON approvals(release_id, release_revision, user_id) WHERE invalidated_at IS NULL;
CREATE INDEX idx_approvals_release ON approvals(release_id, release_revision);

CREATE TABLE post_incident_reviews (
    id           SERIAL PRIMARY KEY,
    release_id   INT NOT NULL REFERENCES releases(id),
    opened_by    INT NOT NULL REFERENCES users(id),
    justification TEXT NOT NULL,
    due_at       TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'COMPLETED')),
    findings     TEXT,
    completed_by INT REFERENCES users(id),
    completed_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------
CREATE FUNCTION gov_setting_int(p_key TEXT) RETURNS INT
LANGUAGE sql STABLE AS $$ SELECT value::int FROM governance_settings WHERE key = p_key $$;

CREATE FUNCTION has_role(p_user INT, p_role TEXT) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM user_roles ur JOIN users u ON u.id = ur.user_id
                   WHERE ur.user_id = p_user AND ur.role_code = p_role AND u.is_active);
$$;

CREATE FUNCTION require_role(p_user INT, p_role TEXT) RETURNS VOID
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF NOT has_role(p_user, p_role) THEN
        RAISE EXCEPTION 'user % does not hold role %', p_user, p_role
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END $$;

-- Latest prediction for the release's current revision.
CREATE FUNCTION current_prediction(p_release INT)
RETURNS risk_predictions LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT p.* FROM risk_predictions p JOIN releases r ON r.id = p.release_id
    WHERE p.release_id = p_release AND p.release_revision = r.revision
    ORDER BY p.id DESC LIMIT 1;
$$;

-- Hash of everything a signer is vouching for.
CREATE FUNCTION release_payload_hash(p_release INT) RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT encode(digest(jsonb_build_object(
        'id', r.id, 'revision', r.revision, 'app_name', r.app_name, 'version', r.version,
        'target_date', r.target_date, 'ticket_id', r.ticket_id, 'fix_type', r.fix_type,
        'description', r.description, 'change_type', r.change_type, 'target_env', r.target_env,
        'patch_size_loc', r.patch_size_loc, 'files_changed', r.files_changed,
        'test_coverage', r.test_coverage, 'ticket_complexity', r.ticket_complexity,
        'module_failure_rate', r.module_failure_rate, 'has_db_migration', r.has_db_migration,
        'dependency_changes', r.dependency_changes,
        'prediction_id', (current_prediction(r.id)).id
    )::text, 'sha256'), 'hex')
    FROM releases r WHERE r.id = p_release;
$$;

-- Per-role progress of the release's current revision against its policy.
CREATE FUNCTION approval_progress(p_release INT)
RETURNS TABLE(role_code TEXT, required INT, approved INT, requires_risk_ack BOOLEAN,
              signers TEXT[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    WITH r AS (SELECT id, revision, (current_prediction(id)).risk_tier AS tier,
                      release_payload_hash(id) AS h
               FROM releases WHERE id = p_release)
    SELECT pol.role_code, pol.min_approvals,
           count(a.id)::int,
           pol.requires_risk_ack,
           coalesce(array_agg(u.username ORDER BY a.signed_at) FILTER (WHERE a.id IS NOT NULL), '{}')
    FROM r
    JOIN approval_policies pol ON pol.risk_tier = r.tier
    LEFT JOIN approvals a
           ON a.release_id = r.id AND a.release_revision = r.revision
          AND a.role_code = pol.role_code AND a.decision = 'APPROVE'
          AND a.invalidated_at IS NULL AND a.expires_at > now()
          AND a.payload_hash = r.h
    LEFT JOIN users u ON u.id = a.user_id
    GROUP BY pol.role_code, pol.min_approvals, pol.requires_risk_ack
    ORDER BY pol.role_code;
$$;

CREATE FUNCTION policy_satisfied(p_release INT) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM approval_progress(p_release))
       AND NOT EXISTS (SELECT 1 FROM approval_progress(p_release) WHERE approved < required);
$$;

-- ---------------------------------------------------------------------
-- Guard trigger on releases
-- ---------------------------------------------------------------------
CREATE FUNCTION releases_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT (has_role(NEW.created_by, 'DEVELOPER') OR has_role(NEW.created_by, 'QA_LEAD')) THEN
        RAISE EXCEPTION 'only DEVELOPER or QA_LEAD can create releases'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.gov_status := 'DRAFT';
    NEW.revision := 1;
    NEW.is_emergency := FALSE;
    NEW.submitted_by := NULL;
    RETURN NEW;
END $$;

CREATE FUNCTION releases_before_update() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_material BOOLEAN;
BEGIN
    v_material :=
        (OLD.app_name, OLD.version, OLD.target_date, OLD.ticket_id, OLD.fix_type, OLD.description,
         OLD.change_type, OLD.target_env, OLD.patch_size_loc, OLD.files_changed, OLD.test_coverage,
         OLD.ticket_complexity, OLD.module_failure_rate, OLD.has_db_migration, OLD.dependency_changes)
        IS DISTINCT FROM
        (NEW.app_name, NEW.version, NEW.target_date, NEW.ticket_id, NEW.fix_type, NEW.description,
         NEW.change_type, NEW.target_env, NEW.patch_size_loc, NEW.files_changed, NEW.test_coverage,
         NEW.ticket_complexity, NEW.module_failure_rate, NEW.has_db_migration, NEW.dependency_changes);

    IF v_material THEN
        IF OLD.gov_status IN ('DEPLOYING', 'DEPLOYED', 'FAILED', 'ROLLED_BACK', 'CANCELLED') THEN
            RAISE EXCEPTION 'release % is % and can no longer be edited', OLD.id, OLD.gov_status
                USING ERRCODE = 'check_violation';
        END IF;
        NEW.revision := OLD.revision + 1;
        IF OLD.gov_status IN ('IN_REVIEW', 'APPROVED', 'REJECTED') THEN
            NEW.gov_status := 'DRAFT';     -- content changed: must be re-scored and re-signed
            NEW.submitted_by := NULL;
            NEW.is_emergency := FALSE;
        END IF;
    END IF;

    IF NEW.gov_status IS DISTINCT FROM OLD.gov_status THEN
        IF NOT EXISTS (SELECT 1 FROM state_transitions
                       WHERE from_status = OLD.gov_status AND to_status = NEW.gov_status) THEN
            RAISE EXCEPTION 'illegal transition % -> %', OLD.gov_status, NEW.gov_status
                USING ERRCODE = 'check_violation';
        END IF;
        -- The core guarantee: no single row update can approve a release.
        IF NEW.gov_status = 'APPROVED' AND NOT NEW.is_emergency
           AND NOT policy_satisfied(NEW.id) THEN
            RAISE EXCEPTION 'release % cannot be APPROVED: required signatures missing', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- SECURITY DEFINER so it can invalidate approvals even though the
-- application role has no UPDATE privilege on the approvals table.
CREATE FUNCTION releases_after_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_actor INT := nullif(current_setting('app.actor_id', true), '')::int;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM write_audit(NEW.created_by, 'RELEASE_CREATED', 'release', NEW.id,
            jsonb_build_object('ticket_id', NEW.ticket_id, 'version', NEW.version));
    ELSIF NEW.revision <> OLD.revision THEN
        UPDATE approvals SET invalidated_at = now(),
               invalidated_reason = 'release edited (revision ' || NEW.revision || ')'
        WHERE release_id = NEW.id AND release_revision < NEW.revision AND invalidated_at IS NULL;
        PERFORM write_audit(v_actor, 'RELEASE_REVISED', 'release', NEW.id,
            jsonb_build_object('from_revision', OLD.revision, 'to_revision', NEW.revision,
                               'status_before', OLD.gov_status, 'status_after', NEW.gov_status));
    ELSIF (OLD.qa_status, OLD.responsible_qa, OLD.progression, OLD.notes)
          IS DISTINCT FROM (NEW.qa_status, NEW.responsible_qa, NEW.progression, NEW.notes) THEN
        PERFORM write_audit(v_actor, 'RELEASE_TRACKING_UPDATED', 'release', NEW.id,
            jsonb_build_object('qa_status', NEW.qa_status, 'responsible_qa', NEW.responsible_qa));
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER trg_releases_before_insert BEFORE INSERT ON releases
    FOR EACH ROW EXECUTE FUNCTION releases_before_insert();
CREATE TRIGGER trg_releases_before_update BEFORE UPDATE ON releases
    FOR EACH ROW EXECUTE FUNCTION releases_before_update();
CREATE TRIGGER trg_releases_after_write AFTER INSERT OR UPDATE ON releases
    FOR EACH ROW EXECUTE FUNCTION releases_after_write();

-- Approvals are immutable apart from invalidation.
CREATE FUNCTION approvals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'approvals cannot be deleted' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (OLD.release_id, OLD.release_revision, OLD.prediction_id, OLD.role_code, OLD.user_id,
        OLD.decision, OLD.comment, OLD.acknowledged_risk, OLD.payload_hash, OLD.signed_at,
        OLD.expires_at)
       IS DISTINCT FROM
       (NEW.release_id, NEW.release_revision, NEW.prediction_id, NEW.role_code, NEW.user_id,
        NEW.decision, NEW.comment, NEW.acknowledged_risk, NEW.payload_hash, NEW.signed_at,
        NEW.expires_at)
       OR (OLD.invalidated_at IS NOT NULL AND NEW.invalidated_at IS DISTINCT FROM OLD.invalidated_at) THEN
        RAISE EXCEPTION 'approval signatures are immutable (only invalidation is allowed)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_approvals_guard BEFORE UPDATE OR DELETE ON approvals
    FOR EACH ROW EXECUTE FUNCTION approvals_guard();

-- ---------------------------------------------------------------------
-- Governance operations (each is one short transaction when called)
-- ---------------------------------------------------------------------

-- Store a model prediction for the release's current revision.
CREATE FUNCTION record_prediction(
    p_release INT, p_actor INT, p_model_version INT, p_probability NUMERIC,
    p_tier risk_tier, p_features JSONB, p_explanation JSONB
) RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
    v_id INT;
BEGIN
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF r.gov_status <> 'DRAFT' THEN
        RAISE EXCEPTION 'release can only be (re)scored while DRAFT (currently %)', r.gov_status
            USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO risk_predictions(release_id, release_revision, model_version_id, probability,
                                 risk_tier, feature_snapshot, explanation, created_by)
    VALUES (p_release, r.revision, p_model_version, p_probability, p_tier,
            p_features, p_explanation, p_actor)
    RETURNING id INTO v_id;
    PERFORM write_audit(p_actor, 'RISK_SCORED', 'release', p_release,
        jsonb_build_object('prediction_id', v_id, 'probability', p_probability,
                           'tier', p_tier, 'model_version_id', p_model_version,
                           'revision', r.revision));
    RETURN v_id;
END $$;

-- Developer submits a scored DRAFT for review; this is the DEVELOPER signature.
CREATE FUNCTION submit_release(p_release INT, p_actor INT, p_comment TEXT DEFAULT NULL)
RETURNS gov_status
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
    p risk_predictions%ROWTYPE;
BEGIN
    PERFORM require_role(p_actor, 'DEVELOPER');
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF r.gov_status <> 'DRAFT' THEN
        RAISE EXCEPTION 'only DRAFT releases can be submitted (currently %)', r.gov_status
            USING ERRCODE = 'check_violation';
    END IF;
    p := current_prediction(p_release);
    IF p.id IS NULL THEN
        RAISE EXCEPTION 'release must be risk-scored before submission' USING ERRCODE = 'check_violation';
    END IF;

    UPDATE releases SET gov_status = 'IN_REVIEW', submitted_by = p_actor WHERE id = p_release;

    INSERT INTO approvals(release_id, release_revision, prediction_id, role_code, user_id,
                          decision, comment, acknowledged_risk, payload_hash, expires_at)
    VALUES (p_release, r.revision, p.id, 'DEVELOPER', p_actor, 'APPROVE', p_comment, TRUE,
            release_payload_hash(p_release),
            now() + make_interval(hours => gov_setting_int('approval_ttl_hours')));

    PERFORM write_audit(p_actor, 'RELEASE_SUBMITTED', 'release', p_release,
        jsonb_build_object('revision', r.revision, 'prediction_id', p.id, 'tier', p.risk_tier,
                           'probability', p.probability));
    RETURN 'IN_REVIEW';
END $$;

-- A reviewer signs (APPROVE) or rejects. If the policy becomes satisfied,
-- the release moves to APPROVED inside the same transaction.
CREATE FUNCTION sign_approval(
    p_release INT, p_actor INT, p_role TEXT, p_decision TEXT,
    p_comment TEXT DEFAULT NULL, p_ack_risk BOOLEAN DEFAULT FALSE
) RETURNS gov_status
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r        releases%ROWTYPE;
    p        risk_predictions%ROWTYPE;
    pol      approval_policies%ROWTYPE;
    v_have   INT;
BEGIN
    IF p_role = 'DEVELOPER' THEN
        RAISE EXCEPTION 'the DEVELOPER signature is given by submitting the release'
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM require_role(p_actor, p_role);

    -- Serialise all signers of this release.
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF r.gov_status <> 'IN_REVIEW' THEN
        RAISE EXCEPTION 'release is % - signatures are only accepted while IN_REVIEW', r.gov_status
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_actor = r.submitted_by THEN
        RAISE EXCEPTION 'separation of duties: the submitter cannot approve their own release'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF EXISTS (SELECT 1 FROM approvals WHERE release_id = p_release AND release_revision = r.revision
               AND user_id = p_actor AND invalidated_at IS NULL) THEN
        RAISE EXCEPTION 'separation of duties: user % has already signed this revision', p_actor
            USING ERRCODE = 'unique_violation';
    END IF;

    p := current_prediction(p_release);
    SELECT * INTO pol FROM approval_policies WHERE risk_tier = p.risk_tier AND role_code = p_role;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'role % is not required for a % risk release', p_role, p.risk_tier
            USING ERRCODE = 'check_violation';
    END IF;

    IF p_decision = 'APPROVE' THEN
        SELECT approved INTO v_have FROM approval_progress(p_release) WHERE role_code = p_role;
        IF v_have >= pol.min_approvals THEN
            RAISE EXCEPTION 'role % already has the % approval(s) the policy requires',
                p_role, pol.min_approvals USING ERRCODE = 'check_violation';
        END IF;
        IF pol.requires_risk_ack AND NOT coalesce(p_ack_risk, FALSE) THEN
            RAISE EXCEPTION '% risk: % must acknowledge the risk drivers before approving',
                p.risk_tier, p_role USING ERRCODE = 'check_violation';
        END IF;
    ELSIF p_decision <> 'REJECT' THEN
        RAISE EXCEPTION 'decision must be APPROVE or REJECT' USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO approvals(release_id, release_revision, prediction_id, role_code, user_id,
                          decision, comment, acknowledged_risk, payload_hash, expires_at)
    VALUES (p_release, r.revision, p.id, p_role, p_actor, p_decision, p_comment,
            coalesce(p_ack_risk, FALSE), release_payload_hash(p_release),
            now() + make_interval(hours => gov_setting_int('approval_ttl_hours')));

    PERFORM write_audit(p_actor, 'APPROVAL_' || p_decision, 'release', p_release,
        jsonb_build_object('role', p_role, 'revision', r.revision, 'tier', p.risk_tier,
                           'comment', p_comment, 'acknowledged_risk', coalesce(p_ack_risk, FALSE)));

    IF p_decision = 'REJECT' THEN
        UPDATE releases SET gov_status = 'REJECTED' WHERE id = p_release;
        PERFORM write_audit(p_actor, 'STATUS_CHANGED', 'release', p_release,
            jsonb_build_object('from', 'IN_REVIEW', 'to', 'REJECTED'));
        RETURN 'REJECTED';
    END IF;

    IF policy_satisfied(p_release) THEN
        UPDATE releases SET gov_status = 'APPROVED' WHERE id = p_release;
        PERFORM write_audit(NULL, 'STATUS_CHANGED', 'release', p_release,
            jsonb_build_object('from', 'IN_REVIEW', 'to', 'APPROVED', 'tier', p.risk_tier,
                               'by', 'policy evaluation'));
        RETURN 'APPROVED';
    END IF;
    RETURN 'IN_REVIEW';
END $$;

-- Release manager starts the deployment. Approvals are re-verified here,
-- so expired or invalidated signatures send the release back to review.
CREATE FUNCTION start_deployment(p_release INT, p_actor INT)
RETURNS gov_status
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
BEGIN
    PERFORM require_role(p_actor, 'RELEASE_MANAGER');
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF r.gov_status <> 'APPROVED' THEN
        RAISE EXCEPTION 'only APPROVED releases can be deployed (currently %)', r.gov_status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT r.is_emergency AND NOT policy_satisfied(p_release) THEN
        UPDATE releases SET gov_status = 'IN_REVIEW' WHERE id = p_release;
        PERFORM write_audit(p_actor, 'APPROVALS_EXPIRED', 'release', p_release,
            jsonb_build_object('from', 'APPROVED', 'to', 'IN_REVIEW'));
        RETURN 'IN_REVIEW';
    END IF;
    UPDATE releases SET gov_status = 'DEPLOYING' WHERE id = p_release;
    PERFORM write_audit(p_actor, 'DEPLOYMENT_STARTED', 'release', p_release, '{}');
    RETURN 'DEPLOYING';
END $$;

-- Ground-truth outcome; feeds the model retraining dataset.
CREATE FUNCTION record_outcome(p_release INT, p_actor INT, p_outcome gov_status,
                               p_notes TEXT DEFAULT NULL)
RETURNS gov_status
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
BEGIN
    PERFORM require_role(p_actor, 'RELEASE_MANAGER');
    IF p_outcome NOT IN ('DEPLOYED', 'FAILED', 'ROLLED_BACK') THEN
        RAISE EXCEPTION 'outcome must be DEPLOYED, FAILED or ROLLED_BACK' USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;

    UPDATE releases SET gov_status = p_outcome WHERE id = p_release;  -- transition table validates
    INSERT INTO deployment_outcomes(release_id, prediction_id, outcome, notes, recorded_by)
    VALUES (p_release, (current_prediction(p_release)).id, p_outcome, p_notes, p_actor)
    ON CONFLICT (release_id) DO UPDATE
        SET outcome = EXCLUDED.outcome, notes = EXCLUDED.notes,
            recorded_by = EXCLUDED.recorded_by, recorded_at = now();
    PERFORM write_audit(p_actor, 'OUTCOME_RECORDED', 'release', p_release,
        jsonb_build_object('from', r.gov_status, 'outcome', p_outcome, 'notes', p_notes));
    RETURN p_outcome;
END $$;

-- Break-glass: a Release Manager approves an urgent release with reduced
-- sign-off. Always flagged, audited, and followed by a mandatory review that
-- a DIFFERENT Release Manager must complete.
CREATE FUNCTION emergency_approve(p_release INT, p_actor INT, p_justification TEXT)
RETURNS gov_status
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
BEGIN
    PERFORM require_role(p_actor, 'RELEASE_MANAGER');
    IF length(trim(coalesce(p_justification, ''))) < 20 THEN
        RAISE EXCEPTION 'break-glass requires a justification of at least 20 characters'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF r.gov_status <> 'IN_REVIEW' THEN
        RAISE EXCEPTION 'break-glass only applies to releases IN_REVIEW (currently %)', r.gov_status
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_actor = r.submitted_by THEN
        RAISE EXCEPTION 'separation of duties: the submitter cannot break-glass their own release'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    UPDATE releases SET is_emergency = TRUE, gov_status = 'APPROVED' WHERE id = p_release;
    INSERT INTO post_incident_reviews(release_id, opened_by, justification, due_at)
    VALUES (p_release, p_actor, p_justification,
            now() + make_interval(hours => gov_setting_int('emergency_review_hours')));
    PERFORM write_audit(p_actor, 'BREAK_GLASS_APPROVAL', 'release', p_release,
        jsonb_build_object('justification', p_justification,
                           'missing_signatures', (SELECT jsonb_agg(to_jsonb(ap))
                                                  FROM approval_progress(p_release) ap
                                                  WHERE ap.approved < ap.required)));
    RETURN 'APPROVED';
END $$;

CREATE FUNCTION complete_incident_review(p_review INT, p_actor INT, p_findings TEXT)
RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v post_incident_reviews%ROWTYPE;
BEGIN
    PERFORM require_role(p_actor, 'RELEASE_MANAGER');
    SELECT * INTO v FROM post_incident_reviews WHERE id = p_review FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'review % not found', p_review USING ERRCODE = 'no_data_found'; END IF;
    IF v.status = 'COMPLETED' THEN
        RAISE EXCEPTION 'review % is already completed', p_review USING ERRCODE = 'check_violation';
    END IF;
    IF p_actor = v.opened_by THEN
        RAISE EXCEPTION 'separation of duties: the break-glass approver cannot review themselves'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF length(trim(coalesce(p_findings, ''))) < 20 THEN
        RAISE EXCEPTION 'findings must be at least 20 characters' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE post_incident_reviews
       SET status = 'COMPLETED', findings = p_findings, completed_by = p_actor, completed_at = now()
     WHERE id = p_review;
    PERFORM write_audit(p_actor, 'INCIDENT_REVIEW_COMPLETED', 'release', v.release_id,
        jsonb_build_object('review_id', p_review, 'overdue', now() > v.due_at));
END $$;

CREATE FUNCTION cancel_release(p_release INT, p_actor INT, p_reason TEXT)
RETURNS gov_status
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
BEGIN
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF p_actor <> r.created_by AND NOT has_role(p_actor, 'RELEASE_MANAGER') THEN
        RAISE EXCEPTION 'only the creator or a RELEASE_MANAGER can cancel a release'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    UPDATE releases SET gov_status = 'CANCELLED' WHERE id = p_release;   -- transition table validates
    PERFORM write_audit(p_actor, 'RELEASE_CANCELLED', 'release', p_release,
        jsonb_build_object('from', r.gov_status, 'reason', p_reason));
    RETURN 'CANCELLED';
END $$;

-- Hard delete is only allowed for drafts that never entered governance;
-- anything that was ever submitted must be cancelled instead (kept for audit).
CREATE FUNCTION delete_release(p_release INT, p_actor INT)
RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r releases%ROWTYPE;
BEGIN
    SELECT * INTO r FROM releases WHERE id = p_release FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'release % not found', p_release USING ERRCODE = 'no_data_found'; END IF;
    IF p_actor <> r.created_by AND NOT has_role(p_actor, 'RELEASE_MANAGER') THEN
        RAISE EXCEPTION 'only the creator or a RELEASE_MANAGER can delete a release'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF r.gov_status <> 'DRAFT' OR EXISTS (SELECT 1 FROM approvals WHERE release_id = p_release) THEN
        RAISE EXCEPTION 'release has entered governance; cancel it instead of deleting'
            USING ERRCODE = 'check_violation';
    END IF;
    DELETE FROM risk_predictions WHERE release_id = p_release;
    DELETE FROM releases WHERE id = p_release;
    PERFORM write_audit(p_actor, 'RELEASE_DELETED', 'release', p_release,
        jsonb_build_object('ticket_id', r.ticket_id, 'version', r.version));
END $$;

-- ---------------------------------------------------------------------
-- Read model used by the API list endpoint
-- ---------------------------------------------------------------------
CREATE VIEW v_release_overview AS
SELECT r.*,
       p.id          AS prediction_id,
       p.probability AS risk_probability,
       p.risk_tier,
       p.explanation AS risk_explanation,
       (SELECT count(*) FROM post_incident_reviews pir
         WHERE pir.release_id = r.id AND pir.status = 'PENDING') AS pending_reviews
FROM releases r
LEFT JOIN LATERAL (SELECT * FROM risk_predictions rp
                   WHERE rp.release_id = r.id AND rp.release_revision = r.revision
                   ORDER BY rp.id DESC LIMIT 1) p ON TRUE;
