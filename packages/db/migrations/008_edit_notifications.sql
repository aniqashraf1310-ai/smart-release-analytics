-- =====================================================================
-- 008_edit_notifications.sql
-- Notify people when anyone edits a release's overview information.
--
-- 1. The audit entry for every edit now records each changed field with its
--    old and new value (details.changes), so the history shows exactly what
--    was changed and by whom.
-- 2. Every edit notifies the release's stakeholders (creator, submitter,
--    signers) and, once it has been submitted, every QA Lead and Release
--    Manager - e.g. "Azila (QA Lead) updated ... : QA status: Under review ->
--    Ready for UAT; Progression updated." Content edits after sign-off say
--    that signatures were invalidated. The editor is never notified.
-- Apply to an existing database with:  npm run db:upgrade
-- =====================================================================

-- Fields shown in the overview, with their display labels and whether the
-- value is short enough to show "old -> new".
CREATE TABLE IF NOT EXISTS release_field_labels (
    field      TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    show_value BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL
);
INSERT INTO release_field_labels(field, label, show_value, sort_order) VALUES
 ('qa_status', 'QA status', TRUE, 1), ('responsible_qa', 'Responsible QA', TRUE, 2),
 ('target_date', 'Target date', TRUE, 3), ('version', 'Version', TRUE, 4),
 ('app_name', 'App name', TRUE, 5), ('ticket_id', 'Ticket ID', TRUE, 6),
 ('fix_type', 'Fix type', TRUE, 7), ('change_type', 'Change type', TRUE, 8),
 ('target_env', 'Environment', TRUE, 9), ('patch_size_loc', 'Patch size', TRUE, 10),
 ('files_changed', 'Files changed', TRUE, 11), ('test_coverage', 'Test coverage', TRUE, 12),
 ('ticket_complexity', 'Complexity', TRUE, 13), ('module_failure_rate', 'Module failure rate', TRUE, 14),
 ('has_db_migration', 'DB migration', TRUE, 15), ('dependency_changes', 'Dependency changes', TRUE, 16),
 ('description', 'Release notes', FALSE, 17), ('progression', 'Progression', FALSE, 18),
 ('notes', 'Notes', FALSE, 19)
ON CONFLICT (field) DO NOTHING;
GRANT SELECT ON release_field_labels TO release_app;

-- {"field": {"from": x, "to": y}, ...} for every overview field that differs.
CREATE OR REPLACE FUNCTION release_changes(p_old releases, p_new releases) RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT coalesce(jsonb_object_agg(l.field, jsonb_build_object('from', o.value, 'to', n.value)), '{}'::jsonb)
    FROM release_field_labels l
    JOIN jsonb_each(to_jsonb(p_old)) o ON o.key = l.field
    JOIN jsonb_each(to_jsonb(p_new)) n ON n.key = l.field
    WHERE o.value IS DISTINCT FROM n.value;
$$;

-- Human-readable summary, e.g. "QA status: Open -> Pass; Progression updated".
CREATE OR REPLACE FUNCTION describe_release_changes(p_changes JSONB) RETURNS TEXT
LANGUAGE sql STABLE AS $$
    WITH c AS (
        SELECT l.label, l.show_value, l.sort_order, v.value AS ch
        FROM jsonb_each(coalesce(p_changes, '{}'::jsonb)) v
        JOIN release_field_labels l ON l.field = v.key
    ), parts AS (
        SELECT sort_order,
               CASE WHEN show_value
                    THEN label || ': ' || coalesce(ch->>'from', '-') || ' → ' || coalesce(ch->>'to', '-')
                    ELSE label || ' updated' END AS txt,
               row_number() OVER (ORDER BY sort_order) AS rn,
               count(*) OVER () AS total
        FROM c
    )
    SELECT nullif(string_agg(txt, '; ' ORDER BY sort_order) FILTER (WHERE rn <= 4)
                  || CASE WHEN max(total) > 4 THEN '; and ' || (max(total) - 4) || ' more change(s)' ELSE '' END, '')
    FROM parts;
$$;

CREATE OR REPLACE FUNCTION releases_after_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_actor   INT := nullif(current_setting('app.actor_id', true), '')::int;
    v_changes JSONB;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM write_audit(NEW.created_by, 'RELEASE_CREATED', 'release', NEW.id,
            jsonb_build_object('ticket_id', NEW.ticket_id, 'version', NEW.version));
        RETURN NULL;
    END IF;
    v_changes := release_changes(OLD, NEW);
    IF NEW.revision <> OLD.revision THEN
        UPDATE approvals SET invalidated_at = now(),
               invalidated_reason = 'release edited (revision ' || NEW.revision || ')'
        WHERE release_id = NEW.id AND release_revision < NEW.revision AND invalidated_at IS NULL;
        PERFORM write_audit(v_actor, 'RELEASE_REVISED', 'release', NEW.id,
            jsonb_build_object('from_revision', OLD.revision, 'to_revision', NEW.revision,
                               'status_before', OLD.gov_status, 'status_after', NEW.gov_status,
                               'changes', v_changes));
    ELSIF (OLD.qa_status, OLD.responsible_qa, OLD.progression, OLD.notes)
          IS DISTINCT FROM (NEW.qa_status, NEW.responsible_qa, NEW.progression, NEW.notes) THEN
        PERFORM write_audit(v_actor, 'RELEASE_TRACKING_UPDATED', 'release', NEW.id,
            jsonb_build_object('qa_status', NEW.qa_status, 'responsible_qa', NEW.responsible_qa,
                               'status', NEW.gov_status, 'changes', v_changes));
    END IF;
    RETURN NULL;
END $$;

REVOKE EXECUTE ON FUNCTION release_changes(releases, releases), describe_release_changes(JSONB) FROM PUBLIC;

CREATE OR REPLACE FUNCTION notify_from_audit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    d        JSONB := NEW.details;
    r        releases%ROWTYPE;
    v_name   TEXT;
    v_actor  TEXT;
    v_stake  INT[];
    v_rms    INT[] := ARRAY(SELECT role_members('RELEASE_MANAGER'));
    v_qas    INT[] := ARRAY(SELECT role_members('QA_LEAD'));
    v_role   TEXT;
    v_waiting TEXT;
    v_changes TEXT;
    v_after_signoff BOOLEAN;
    v_actor_role TEXT := '';
BEGIN
    SELECT coalesce(full_name, username) INTO v_actor FROM users WHERE id = NEW.actor_id;
    v_actor := coalesce(v_actor, 'The system');
    -- Show the actor's role unless their display name already contains it.
    SELECT ' (' || ro.name || ')' INTO v_actor_role
      FROM user_roles ur JOIN roles ro ON ro.code = ur.role_code
     WHERE ur.user_id = NEW.actor_id AND position(ro.name IN v_actor) = 0;
    v_actor_role := coalesce(v_actor_role, '');

    IF NEW.entity = 'release' THEN
        SELECT * INTO r FROM releases WHERE id = NEW.entity_id;
        IF NOT FOUND THEN RETURN NULL; END IF;           -- e.g. a deleted draft
        v_name  := r.app_name || ' ' || r.version || ' (' || r.ticket_id || ')';
        v_stake := ARRAY(SELECT release_stakeholders(r.id));
        v_role  := (SELECT name FROM roles WHERE code = d->>'role');

        CASE NEW.action
        WHEN 'RELEASE_SUBMITTED' THEN
            PERFORM notify_users(v_qas || v_rms, 'review_requested', 'Review requested',
                v_actor || ' submitted ' || v_name || ' for approval (' || lower(d->>'tier') || ' risk, '
                || round((d->>'probability')::numeric * 100) || '% failure risk).',
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'APPROVAL_APPROVE' THEN
            -- Everyone in the review loop hears about it: the developer, earlier
            -- signers, and every QA Lead and Release Manager - so a QA approval
            -- reaches the Release Managers and an RM approval reaches the QA Leads.
            SELECT string_agg(coalesce(ro.name, ap.role_code) || ' (' || (ap.required - ap.approved) || ' more)', ', ')
              INTO v_waiting
              FROM approval_progress(r.id) ap LEFT JOIN roles ro ON ro.code = ap.role_code
             WHERE ap.approved < ap.required;
            PERFORM notify_users(v_stake || v_qas || v_rms, 'signed', 'Sign-off received',
                v_actor || ' approved ' || v_name || ' as ' || coalesce(v_role, d->>'role') || '.'
                || coalesce(' Still waiting for: ' || v_waiting || '.', ''),
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'APPROVAL_REJECT' THEN
            PERFORM notify_users(v_stake || v_qas || v_rms, 'rejected', 'Release rejected',
                v_actor || ' rejected ' || v_name || ' as ' || coalesce(v_role, d->>'role')
                || coalesce(': "' || (d->>'comment') || '"', '.'),
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'STATUS_CHANGED' THEN
            IF d->>'to' = 'APPROVED' THEN
                PERFORM notify_users(v_stake || v_rms, 'approved', 'Release approved',
                    v_name || ' has every required sign-off and is ready to deploy.',
                    r.id, NULL, NEW.seq);
            END IF;
        WHEN 'BREAK_GLASS_APPROVAL' THEN
            PERFORM notify_users(v_stake || v_rms, 'break_glass', 'Emergency approval',
                v_actor || ' used break-glass to approve ' || v_name || ': "' || (d->>'justification')
                || '". A post-incident review is now due.',
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'DEPLOYMENT_STARTED' THEN
            PERFORM notify_users(v_stake, 'deploying', 'Deployment started',
                v_actor || ' started deploying ' || v_name || '.', r.id, NEW.actor_id, NEW.seq);
        WHEN 'OUTCOME_RECORDED' THEN
            PERFORM notify_users(v_stake, CASE d->>'outcome' WHEN 'DEPLOYED' THEN 'deployed' ELSE 'failed' END,
                CASE d->>'outcome' WHEN 'DEPLOYED' THEN 'Deployed successfully'
                                   WHEN 'FAILED' THEN 'Deployment failed' ELSE 'Release rolled back' END,
                v_name || CASE d->>'outcome' WHEN 'DEPLOYED' THEN ' is live.'
                                            WHEN 'FAILED' THEN ' failed to deploy'
                                            ELSE ' was rolled back' END
                || CASE WHEN d->>'outcome' <> 'DEPLOYED' THEN coalesce(': ' || (d->>'notes'), '.') ELSE '' END,
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'RELEASE_REVISED', 'RELEASE_TRACKING_UPDATED' THEN
            -- Any edit of the overview info. Content edits after submission
            -- also invalidate signatures, which the message says.
            v_changes := describe_release_changes(d->'changes');
            IF v_changes IS NOT NULL THEN
                v_after_signoff := NEW.action = 'RELEASE_REVISED'
                                   AND d->>'status_before' IN ('IN_REVIEW', 'APPROVED');
                PERFORM notify_users(
                    -- once a release has been submitted, the whole review team cares
                    v_stake || CASE WHEN r.submitted_by IS NOT NULL OR d->>'status_before' <> 'DRAFT'
                                    THEN v_qas || v_rms ELSE '{}'::int[] END,
                    CASE WHEN v_after_signoff THEN 'revised' ELSE 'edited' END,
                    CASE WHEN v_after_signoff THEN 'Release changed after sign-off' ELSE 'Release details updated' END,
                    v_actor || v_actor_role || ' updated ' || v_name || ': ' || v_changes || '.'
                    || CASE WHEN v_after_signoff
                            THEN ' Earlier signatures were invalidated; it must be re-scored and re-approved.'
                            WHEN NEW.action = 'RELEASE_REVISED' AND EXISTS (
                                     SELECT 1 FROM risk_predictions rp WHERE rp.release_id = r.id
                                     AND rp.release_revision = (d->>'from_revision')::int)
                                 THEN ' It needs a fresh risk score.'
                            ELSE '' END,
                    r.id, NEW.actor_id, NEW.seq);
            END IF;
        WHEN 'APPROVALS_EXPIRED' THEN
            PERFORM notify_users(v_stake, 'expired', 'Approvals expired',
                'Sign-offs for ' || v_name || ' expired before deployment. It is back in review.',
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'RELEASE_CANCELLED' THEN
            PERFORM notify_users(v_stake, 'cancelled', 'Release cancelled',
                v_actor || ' cancelled ' || v_name || coalesce(': ' || (d->>'reason'), '.'),
                r.id, NEW.actor_id, NEW.seq);
        WHEN 'INCIDENT_REVIEW_COMPLETED' THEN
            PERFORM notify_users(v_rms || v_stake, 'review_completed', 'Incident review completed',
                v_actor || ' completed the post-incident review for ' || v_name || '.',
                r.id, NEW.actor_id, NEW.seq);
        ELSE
            NULL;
        END CASE;

    ELSIF NEW.entity = 'user' THEN
        CASE NEW.action
        WHEN 'USER_REGISTERED' THEN
            PERFORM notify_users(v_rms, 'account_request', 'New account request',
                (d->>'username') || ' registered and requested the '
                || (SELECT name FROM roles WHERE code = d->>'requested_role') || ' role.',
                NULL, NEW.actor_id, NEW.seq);
        WHEN 'USER_APPROVED' THEN
            PERFORM notify_users(ARRAY[NEW.entity_id], 'account_approved', 'Welcome aboard',
                'Your account was approved as ' || (SELECT name FROM roles WHERE code = d->>'granted_role') || '.',
                NULL, NEW.actor_id, NEW.seq);
        WHEN 'USER_ROLE_CHANGED' THEN
            PERFORM notify_users(ARRAY[NEW.entity_id], 'role_changed', 'Your role changed',
                v_actor || ' changed your role to ' || (SELECT name FROM roles WHERE code = d->>'to') || '.',
                NULL, NEW.actor_id, NEW.seq);
        ELSE
            NULL;
        END CASE;
    END IF;
    RETURN NULL;
END $$;

