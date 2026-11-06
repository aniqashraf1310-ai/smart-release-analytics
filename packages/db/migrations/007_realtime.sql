-- =====================================================================
-- 007_realtime.sql
-- Instant notification delivery + wider sign-off recipients.
--
-- 1. Every new notification row raises pg_notify('sras_notifications',
--    '<user_id>:<notification_id>'). PostgreSQL delivers it only when the
--    transaction COMMITS, so a rolled-back action never reaches anyone. The
--    API listens on this channel and pushes the event to that user's open
--    browser tabs over Server-Sent Events, so it appears immediately.
-- 2. Approvals and rejections now notify all QA Leads and Release Managers
--    as well as the release's stakeholders, and an approval says which
--    sign-offs are still outstanding.
-- Safe to apply to an existing database (python packages/db/setup_db.py --upgrade).
-- =====================================================================

CREATE OR REPLACE FUNCTION notifications_after_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('sras_notifications', NEW.user_id || ':' || NEW.id);
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS trg_notifications_push ON notifications;
CREATE TRIGGER trg_notifications_push AFTER INSERT ON notifications
    FOR EACH ROW EXECUTE FUNCTION notifications_after_insert();

REVOKE EXECUTE ON FUNCTION notifications_after_insert() FROM PUBLIC;

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
BEGIN
    SELECT coalesce(full_name, username) INTO v_actor FROM users WHERE id = NEW.actor_id;
    v_actor := coalesce(v_actor, 'The system');

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
        WHEN 'RELEASE_REVISED' THEN
            IF d->>'status_before' IN ('IN_REVIEW', 'APPROVED') THEN
                PERFORM notify_users(v_stake, 'revised', 'Release changed after sign-off',
                    v_actor || ' edited ' || v_name || '. Earlier signatures were invalidated; it must be re-scored and re-approved.',
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

