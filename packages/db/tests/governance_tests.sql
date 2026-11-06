-- =====================================================================
-- Governance test suite. Run against a FRESHLY migrated database:
--   psql -d release_mgmt -v ON_ERROR_STOP=1 -f tests/governance_tests.sql
-- Most tests run as the least-privileged API role (release_app) to prove
-- the rules hold at the database level, not just in the API.
-- Every test prints "PASS <name>"; any failure aborts the script.
-- =====================================================================
\set ON_ERROR_STOP 1
SET client_min_messages = warning;

CREATE SCHEMA test;
GRANT USAGE ON SCHEMA test TO release_app;

-- Assert that a statement fails with a message matching a pattern.
CREATE FUNCTION test.expect_error(p_name TEXT, p_sql TEXT, p_pattern TEXT) RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM !~* p_pattern THEN
            RAISE EXCEPTION 'FAIL % : wrong error "%" (expected /%/)', p_name, SQLERRM, p_pattern;
        END IF;
        RAISE NOTICE 'PASS %', p_name;
        RETURN;
    END;
    RAISE EXCEPTION 'FAIL % : statement succeeded but should have failed', p_name;
END $$;

CREATE FUNCTION test.ok(p_name TEXT, p_cond BOOLEAN) RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    IF p_cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', p_name; END IF;
    RAISE NOTICE 'PASS %', p_name;
END $$;

CREATE FUNCTION test.uid(p_username TEXT) RETURNS INT
LANGUAGE sql STABLE AS $$ SELECT id FROM public.users WHERE username = p_username $$;

CREATE FUNCTION test.status(p_release INT) RETURNS TEXT
LANGUAGE sql STABLE AS $$ SELECT gov_status::text FROM public.releases WHERE id = p_release $$;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA test TO release_app;


-- A registered model to attach predictions to.
SELECT register_model('test-model', 'test', now(), '{}', '{}', '/dev/null');

SET client_min_messages = notice;
SET ROLE release_app;

-- Helper: create a DRAFT release scored at a given tier; returns its id.
CREATE TEMP TABLE t_ids(name TEXT PRIMARY KEY, id INT);

DO $$
DECLARE
    v_model INT := (SELECT id FROM model_versions WHERE version_tag = 'test-model');
    v INT;
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['LOW','MEDIUM','HIGH','CRITICAL','EDIT','REJECT','EXPIRE','GLASS','SOD'] LOOP
        INSERT INTO releases(app_name, version, target_date, responsible_qa, ticket_id, fix_type,
                             description, created_by)
        VALUES ('Test App', '9.0.0', current_date, 'QA', 'T-' || t, 'Other', 'test ' || t,
                test.uid('dev1'))
        RETURNING id INTO v;
        PERFORM record_prediction(v, test.uid('dev1'), v_model,
            CASE t WHEN 'MEDIUM' THEN 0.2 WHEN 'HIGH' THEN 0.4 WHEN 'CRITICAL' THEN 0.8 ELSE 0.05 END,
            (CASE t WHEN 'MEDIUM' THEN 'MEDIUM' WHEN 'HIGH' THEN 'HIGH' WHEN 'CRITICAL' THEN 'CRITICAL'
                    ELSE 'LOW' END)::risk_tier,
            '{}', '{}');
        INSERT INTO t_ids VALUES (t, v);
    END LOOP;
END $$;

-- ---------------------------------------------------------------- privileges
SELECT test.expect_error('app role cannot set gov_status directly',
    $$UPDATE releases SET gov_status = 'APPROVED' WHERE ticket_id = 'T-LOW'$$,
    'permission denied');
SELECT test.expect_error('app role cannot forge approvals by UPDATE',
    $$UPDATE approvals SET decision = 'APPROVE'$$, 'permission denied');
SELECT test.expect_error('app role cannot insert approvals directly',
    $$INSERT INTO approvals(release_id, release_revision, prediction_id, role_code, user_id, decision, payload_hash, expires_at)
      VALUES (1, 1, 1, 'QA_LEAD', 1, 'APPROVE', 'x', now())$$, 'permission denied');
SELECT test.expect_error('app role cannot delete audit logs',
    $$DELETE FROM audit_logs$$, 'permission denied');
SELECT test.expect_error('app role cannot read password hashes',
    $$SELECT password_hash FROM users$$, 'permission denied');
SELECT test.expect_error('only DEVELOPER/QA_LEAD may create releases',
    $$INSERT INTO releases(app_name, version, target_date, responsible_qa, ticket_id, fix_type, description, created_by)
      VALUES ('x', '1.0', current_date, 'q', 'T-X', 'Other', 'd', test.uid('rm1'))$$,
    'only DEVELOPER or QA_LEAD');

-- ---------------------------------------------------------------- LOW flow
SELECT test.expect_error('cannot sign a DRAFT',
    format('SELECT sign_approval(%s, test.uid(''qa1''), ''QA_LEAD'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='LOW')),
    'only accepted while IN_REVIEW');
SELECT test.expect_error('QA lead cannot submit (needs DEVELOPER role)',
    format('SELECT submit_release(%s, test.uid(''qa1''))', (SELECT id FROM t_ids WHERE name='LOW')),
    'does not hold role DEVELOPER');
SELECT test.ok('developer submits LOW release',
    submit_release((SELECT id FROM t_ids WHERE name='LOW'), test.uid('dev1')) = 'IN_REVIEW');
SELECT test.expect_error('user cannot sign with a role they do not hold',
    format('SELECT sign_approval(%s, test.uid(''qa1''), ''RELEASE_MANAGER'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='LOW')),
    'does not hold role RELEASE_MANAGER');
SELECT test.ok('QA sign-off keeps it IN_REVIEW',
    sign_approval((SELECT id FROM t_ids WHERE name='LOW'), test.uid('qa1'), 'QA_LEAD', 'APPROVE') = 'IN_REVIEW');
SELECT test.expect_error('role quota cannot be exceeded',
    format('SELECT sign_approval(%s, test.uid(''qa2''), ''QA_LEAD'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='LOW')),
    'already has the 1 approval');
SELECT test.ok('final RM signature approves atomically',
    sign_approval((SELECT id FROM t_ids WHERE name='LOW'), test.uid('rm1'), 'RELEASE_MANAGER', 'APPROVE') = 'APPROVED');
SELECT test.ok('policy_satisfied() agrees', policy_satisfied((SELECT id FROM t_ids WHERE name='LOW')));

-- ---------------------------------------------------------------- separation of duties
SELECT submit_release((SELECT id FROM t_ids WHERE name='SOD'), test.uid('dev1'));
SELECT test.expect_error('submitter cannot approve own release',
    format('SELECT sign_approval(%s, test.uid(''dev1''), ''QA_LEAD'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='SOD')),
    'does not hold role|submitter cannot approve');
-- Role-switch attack: qa2 signs as QA, is then promoted to Release Manager,
-- and tries to sign the same revision again in the new role.
SELECT sign_approval((SELECT id FROM t_ids WHERE name='SOD'), test.uid('qa2'), 'QA_LEAD', 'APPROVE');
SELECT change_user_role(test.uid('qa2'), test.uid('rm1'), 'RELEASE_MANAGER');
SELECT test.expect_error('promoted user cannot sign the same revision twice',
    format('SELECT sign_approval(%s, test.uid(''qa2''), ''RELEASE_MANAGER'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='SOD')),
    'already signed this revision');
SELECT change_user_role(test.uid('qa2'), test.uid('rm1'), 'QA_LEAD');
SELECT test.expect_error('DEVELOPER signature only via submission',
    format('SELECT sign_approval(%s, test.uid(''dev2''), ''DEVELOPER'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='SOD')),
    'given by submitting');

-- ---------------------------------------------------------------- MEDIUM: risk acknowledgement
SELECT submit_release((SELECT id FROM t_ids WHERE name='MEDIUM'), test.uid('dev1'));
SELECT sign_approval((SELECT id FROM t_ids WHERE name='MEDIUM'), test.uid('qa1'), 'QA_LEAD', 'APPROVE');
SELECT test.expect_error('MEDIUM: RM must acknowledge risk drivers',
    format('SELECT sign_approval(%s, test.uid(''rm1''), ''RELEASE_MANAGER'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='MEDIUM')),
    'must acknowledge the risk drivers');
SELECT test.ok('MEDIUM: approved once RM acknowledges',
    sign_approval((SELECT id FROM t_ids WHERE name='MEDIUM'), test.uid('rm1'), 'RELEASE_MANAGER', 'APPROVE', 'ok', TRUE) = 'APPROVED');

-- ---------------------------------------------------------------- HIGH: two release managers
SELECT submit_release((SELECT id FROM t_ids WHERE name='HIGH'), test.uid('dev1'));
SELECT test.expect_error('HIGH: QA lead must acknowledge risk drivers',
    format('SELECT sign_approval(%s, test.uid(''qa1''), ''QA_LEAD'', ''APPROVE'')', (SELECT id FROM t_ids WHERE name='HIGH')),
    'must acknowledge');
SELECT sign_approval((SELECT id FROM t_ids WHERE name='HIGH'), test.uid('qa1'), 'QA_LEAD', 'APPROVE', NULL, TRUE);
SELECT test.ok('HIGH: QA + one RM is not enough',
    sign_approval((SELECT id FROM t_ids WHERE name='HIGH'), test.uid('rm1'), 'RELEASE_MANAGER', 'APPROVE', NULL, TRUE) = 'IN_REVIEW');
SELECT test.expect_error('HIGH: same RM cannot count twice',
    format('SELECT sign_approval(%s, test.uid(''rm1''), ''RELEASE_MANAGER'', ''APPROVE'', NULL, TRUE)', (SELECT id FROM t_ids WHERE name='HIGH')),
    'already signed');
SELECT test.ok('HIGH: second distinct RM completes it',
    sign_approval((SELECT id FROM t_ids WHERE name='HIGH'), test.uid('rm2'), 'RELEASE_MANAGER', 'APPROVE', NULL, TRUE) = 'APPROVED');

-- ---------------------------------------------------------------- CRITICAL: 2 QA leads + 2 RMs
SELECT submit_release((SELECT id FROM t_ids WHERE name='CRITICAL'), test.uid('dev1'));
SELECT sign_approval((SELECT id FROM t_ids WHERE name='CRITICAL'), test.uid('rm1'), 'RELEASE_MANAGER', 'APPROVE', NULL, TRUE);
SELECT sign_approval((SELECT id FROM t_ids WHERE name='CRITICAL'), test.uid('rm2'), 'RELEASE_MANAGER', 'APPROVE', NULL, TRUE);
SELECT test.ok('CRITICAL: two RMs + one QA lead is not enough',
    sign_approval((SELECT id FROM t_ids WHERE name='CRITICAL'), test.uid('qa1'), 'QA_LEAD', 'APPROVE', NULL, TRUE) = 'IN_REVIEW');
SELECT test.ok('CRITICAL: second distinct QA lead approves',
    sign_approval((SELECT id FROM t_ids WHERE name='CRITICAL'), test.uid('qa2'), 'QA_LEAD', 'APPROVE', NULL, TRUE) = 'APPROVED');

-- ---------------------------------------------------------------- content binding
SELECT submit_release((SELECT id FROM t_ids WHERE name='EDIT'), test.uid('dev1'));
SELECT sign_approval((SELECT id FROM t_ids WHERE name='EDIT'), test.uid('qa1'), 'QA_LEAD', 'APPROVE');
SELECT sign_approval((SELECT id FROM t_ids WHERE name='EDIT'), test.uid('rm1'), 'RELEASE_MANAGER', 'APPROVE');
SELECT set_config('app.actor_id', test.uid('dev1')::text, false);
UPDATE releases SET patch_size_loc = 5000 WHERE ticket_id = 'T-EDIT';
SELECT test.ok('material edit after approval returns release to DRAFT',
    test.status((SELECT id FROM t_ids WHERE name='EDIT')) = 'DRAFT');
SELECT test.ok('material edit bumps the revision',
    (SELECT revision FROM releases WHERE ticket_id = 'T-EDIT') = 2);
SELECT test.ok('material edit invalidates all earlier signatures',
    NOT EXISTS (SELECT 1 FROM approvals WHERE release_id = (SELECT id FROM t_ids WHERE name='EDIT')
                AND invalidated_at IS NULL));
SELECT test.expect_error('re-submission requires a fresh risk score',
    format('SELECT submit_release(%s, test.uid(''dev1''))', (SELECT id FROM t_ids WHERE name='EDIT')),
    'must be risk-scored');
UPDATE releases SET progression = 'waiting for vendor' WHERE ticket_id = 'T-LOW';
SELECT test.ok('non-material tracking update keeps approval',
    test.status((SELECT id FROM t_ids WHERE name='LOW')) = 'APPROVED');

-- ---------------------------------------------------------------- rejection
SELECT submit_release((SELECT id FROM t_ids WHERE name='REJECT'), test.uid('dev1'));
SELECT test.expect_error('rejection requires a comment',
    format('SELECT sign_approval(%s, test.uid(''qa1''), ''QA_LEAD'', ''REJECT'')', (SELECT id FROM t_ids WHERE name='REJECT')),
    'approvals_check|check constraint');
SELECT test.ok('reviewer rejection moves to REJECTED',
    sign_approval((SELECT id FROM t_ids WHERE name='REJECT'), test.uid('qa1'), 'QA_LEAD', 'REJECT', 'UAT evidence missing') = 'REJECTED');
UPDATE releases SET description = 'test REJECT - fixed' WHERE ticket_id = 'T-REJECT';
SELECT test.ok('editing a rejected release reopens it as DRAFT',
    test.status((SELECT id FROM t_ids WHERE name='REJECT')) = 'DRAFT');

-- ---------------------------------------------------------------- deployment + outcomes
SELECT test.expect_error('only RELEASE_MANAGER can deploy',
    format('SELECT start_deployment(%s, test.uid(''qa1''))', (SELECT id FROM t_ids WHERE name='LOW')),
    'does not hold role RELEASE_MANAGER');
SELECT test.ok('RM starts deployment',
    start_deployment((SELECT id FROM t_ids WHERE name='LOW'), test.uid('rm1')) = 'DEPLOYING');
SELECT test.ok('outcome recorded',
    record_outcome((SELECT id FROM t_ids WHERE name='LOW'), test.uid('rm1'), 'DEPLOYED', 'smooth') = 'DEPLOYED');
SELECT test.expect_error('deployed release is locked',
    $$UPDATE releases SET patch_size_loc = 1 WHERE ticket_id = 'T-LOW'$$, 'can no longer be edited');
SELECT test.ok('rollback after go-live is recorded',
    record_outcome((SELECT id FROM t_ids WHERE name='LOW'), test.uid('rm1'), 'ROLLED_BACK', 'crash spike') = 'ROLLED_BACK');
SELECT test.ok('outcome row updated for retraining',
    (SELECT outcome::text FROM deployment_outcomes WHERE release_id = (SELECT id FROM t_ids WHERE name='LOW')) = 'ROLLED_BACK');

-- ---------------------------------------------------------------- break-glass
SELECT submit_release((SELECT id FROM t_ids WHERE name='GLASS'), test.uid('dev1'));
SELECT test.expect_error('break-glass restricted to RELEASE_MANAGER',
    format('SELECT emergency_approve(%s, test.uid(''qa1''), ''production outage, customers cannot log in'')', (SELECT id FROM t_ids WHERE name='GLASS')),
    'does not hold role RELEASE_MANAGER');
SELECT test.expect_error('break-glass needs a real justification',
    format('SELECT emergency_approve(%s, test.uid(''rm1''), ''urgent'')', (SELECT id FROM t_ids WHERE name='GLASS')),
    'at least 20 characters');
SELECT test.ok('break-glass approves with missing signatures',
    emergency_approve((SELECT id FROM t_ids WHERE name='GLASS'), test.uid('rm1'),
                      'production outage, customers cannot log in') = 'APPROVED');
SELECT test.ok('break-glass opens a mandatory incident review',
    EXISTS (SELECT 1 FROM post_incident_reviews WHERE release_id = (SELECT id FROM t_ids WHERE name='GLASS')
            AND status = 'PENDING'));
SELECT test.expect_error('break-glass approver cannot review themselves',
    format('SELECT complete_incident_review(%s, test.uid(''rm1''), ''root cause: expired cert, fixed properly'')',
           (SELECT id FROM post_incident_reviews WHERE release_id = (SELECT id FROM t_ids WHERE name='GLASS'))),
    'cannot review themselves');
SELECT complete_incident_review((SELECT id FROM post_incident_reviews WHERE release_id = (SELECT id FROM t_ids WHERE name='GLASS')),
                                test.uid('rm2'), 'root cause: expired cert, renewal now automated');
SELECT test.ok('incident review completed',
    (SELECT status FROM post_incident_reviews WHERE release_id = (SELECT id FROM t_ids WHERE name='GLASS')) = 'COMPLETED');

-- ---------------------------------------------------------------- expiry (needs owner to backdate)
SELECT submit_release((SELECT id FROM t_ids WHERE name='EXPIRE'), test.uid('dev1'));
SELECT sign_approval((SELECT id FROM t_ids WHERE name='EXPIRE'), test.uid('qa1'), 'QA_LEAD', 'APPROVE');
SELECT sign_approval((SELECT id FROM t_ids WHERE name='EXPIRE'), test.uid('rm1'), 'RELEASE_MANAGER', 'APPROVE');
RESET ROLE;
ALTER TABLE approvals DISABLE TRIGGER trg_approvals_guard;
UPDATE approvals SET expires_at = now() - interval '1 minute'
 WHERE release_id = (SELECT id FROM t_ids WHERE name='EXPIRE') AND role_code = 'QA_LEAD';
ALTER TABLE approvals ENABLE TRIGGER trg_approvals_guard;
SET ROLE release_app;
SELECT test.ok('expired signature sends release back to review at deploy time',
    start_deployment((SELECT id FROM t_ids WHERE name='EXPIRE'), test.uid('rm1')) = 'IN_REVIEW');

-- ---------------------------------------------------------------- accounts: registration and administration
SET ROLE release_app;
SELECT test.expect_error('weak password refused',
    $$SELECT register_user('newdev', 'New Dev', 'new@example.com', 'short', 'DEVELOPER')$$, 'at least 8 characters');
SELECT test.expect_error('unknown role refused',
    $$SELECT register_user('newdev', 'New Dev', 'new@example.com', 'Passw0rd!', 'ADMIN')$$, 'requested role must be');
SELECT test.expect_error('duplicate username refused',
    $$SELECT register_user('DEV1', 'Imposter', 'x@example.com', 'Passw0rd!', 'DEVELOPER')$$, 'already taken');
SELECT register_user('newrm', 'Ambitious Person', 'newrm@example.com', 'Passw0rd1', 'RELEASE_MANAGER');
SELECT test.ok('registration starts PENDING with no role',
    (SELECT status FROM users WHERE username = 'newrm') = 'PENDING'
    AND NOT EXISTS (SELECT 1 FROM user_roles WHERE user_id = test.uid('newrm')));
SELECT test.ok('pending user authenticates but receives no roles',
    (SELECT status = 'PENDING' AND roles = '{}' FROM authenticate('newrm', 'Passw0rd1')));
SELECT test.ok('wrong password returns nothing', NOT EXISTS (SELECT 1 FROM authenticate('newrm', 'wrong')));
SELECT test.ok('requesting RELEASE_MANAGER does not grant it', NOT has_role(test.uid('newrm'), 'RELEASE_MANAGER'));
SELECT test.expect_error('pending user cannot create releases',
    $$INSERT INTO releases(app_name, version, target_date, responsible_qa, ticket_id, fix_type, description, created_by)
      VALUES ('x', '1.0', current_date, 'q', 'T-PEND', 'Other', 'd', test.uid('newrm'))$$, 'only DEVELOPER or QA_LEAD');
SELECT test.expect_error('only a RELEASE_MANAGER can approve registrations',
    format('SELECT review_registration(%s, test.uid(''qa1''), TRUE)', test.uid('newrm')), 'does not hold role RELEASE_MANAGER');
SELECT test.ok('RM approves but grants a lower role than requested',
    review_registration(test.uid('newrm'), test.uid('rm1'), TRUE, 'DEVELOPER', 'start as developer') = 'ACTIVE');
SELECT test.ok('granted role is the one the RM chose',
    has_role(test.uid('newrm'), 'DEVELOPER') AND NOT has_role(test.uid('newrm'), 'RELEASE_MANAGER'));
SELECT test.expect_error('a registration cannot be reviewed twice',
    format('SELECT review_registration(%s, test.uid(''rm1''), FALSE)', test.uid('newrm')), 'only PENDING');
SELECT test.expect_error('nobody can change their own role',
    format('SELECT change_user_role(%s, %s, ''DEVELOPER'')', test.uid('rm1'), test.uid('rm1')), 'your own account');
SELECT test.expect_error('nobody can disable themselves',
    format('SELECT set_user_status(%s, %s, ''DISABLED'')', test.uid('rm1'), test.uid('rm1')), 'your own account');
SELECT set_user_status(test.uid('newrm'), test.uid('rm2'), 'DISABLED', 'left the company');
SELECT test.ok('disabled user loses all authority', NOT has_role(test.uid('newrm'), 'DEVELOPER'));
SELECT register_user('spam', 'Spam Bot', NULL, 'Passw0rd1', 'QA_LEAD');
SELECT test.ok('RM can reject a registration',
    review_registration(test.uid('spam'), test.uid('rm1'), FALSE, NULL, 'unknown person') = 'REJECTED');
SELECT test.ok('account actions are audited',
    (SELECT count(*) FROM audit_logs WHERE entity = 'user'
      AND action IN ('USER_REGISTERED', 'USER_APPROVED', 'USER_REJECTED', 'USER_DISABLED', 'USER_ROLE_CHANGED')) >= 6);
RESET ROLE;
SELECT test.expect_error('a user cannot hold two roles',
    format('INSERT INTO user_roles VALUES (%s, ''QA_LEAD'')', test.uid('dev1')), 'duplicate key|user_roles_pkey');

-- ---------------------------------------------------------------- notifications
SET ROLE release_app;
SELECT test.ok('submission notifies every QA lead and release manager',
    (SELECT count(DISTINCT user_id) FROM notifications
      WHERE kind = 'review_requested' AND release_id = (SELECT id FROM t_ids WHERE name='LOW')
        AND user_id IN (test.uid('qa1'), test.uid('qa2'), test.uid('rm1'), test.uid('rm2'))) = 4);
SELECT test.ok('the submitter is not notified about their own submission',
    NOT EXISTS (SELECT 1 FROM notifications WHERE kind = 'review_requested'
                AND release_id = (SELECT id FROM t_ids WHERE name='LOW') AND user_id = test.uid('dev1')));
SELECT test.ok('developer notified of each sign-off',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'signed' AND user_id = test.uid('dev1')
            AND release_id = (SELECT id FROM t_ids WHERE name='LOW') AND message LIKE '%Azila%QA Lead%'));
SELECT test.ok('developer and release managers notified when fully approved',
    (SELECT count(DISTINCT user_id) FROM notifications WHERE kind = 'approved'
      AND release_id = (SELECT id FROM t_ids WHERE name='LOW')
      AND user_id IN (test.uid('dev1'), test.uid('rm2'))) = 2);
SELECT test.ok('developer notified of rejection with the reason',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'rejected' AND user_id = test.uid('dev1')
            AND release_id = (SELECT id FROM t_ids WHERE name='REJECT') AND message LIKE '%UAT evidence missing%'));
SELECT test.ok('signers notified when content changes after sign-off',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'revised' AND user_id = test.uid('qa1')
            AND release_id = (SELECT id FROM t_ids WHERE name='EDIT')));
SELECT test.ok('release managers notified of break-glass',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'break_glass' AND user_id = test.uid('rm2')));
SELECT test.ok('release managers notified of account requests',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'account_request' AND user_id = test.uid('rm1')
            AND message LIKE 'newrm%Release Manager%'));
SELECT test.ok('approved user receives a welcome notification',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'account_approved' AND user_id = test.uid('newrm')
            AND message LIKE '%Developer%'));
SELECT test.ok('release managers hear about a QA lead approval, with what is still missing',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'signed' AND user_id = test.uid('rm2')
            AND release_id = (SELECT id FROM t_ids WHERE name='LOW')
            AND message LIKE 'Azila%approved%Still waiting for: Release Manager (1 more).'));
SELECT test.ok('QA leads hear about a release manager approval',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'signed' AND user_id = test.uid('qa2')
            AND release_id = (SELECT id FROM t_ids WHERE name='HIGH') AND message LIKE 'Farid%Release Manager%'));
SELECT test.ok('release managers hear about a QA lead rejection',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'rejected' AND user_id = test.uid('rm1')
            AND release_id = (SELECT id FROM t_ids WHERE name='REJECT') AND message LIKE '%rejected%as QA Lead: "UAT evidence missing%'));
-- edits of the overview info
SELECT set_config('app.actor_id', test.uid('qa1')::text, false);
UPDATE releases SET qa_status = 'Ready for UAT', responsible_qa = 'Akma', progression = 'retested on iOS 18'
 WHERE ticket_id = 'T-SOD';
SELECT test.ok('QA lead edit notifies the developer with the changed values',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'edited' AND user_id = test.uid('dev1')
            AND release_id = (SELECT id FROM t_ids WHERE name='SOD')
            AND message LIKE 'Azila (QA Lead) updated %QA status: Open → Ready for UAT; Responsible QA: QA → Akma; Progression updated.'));
SELECT test.ok('QA lead edit of a submitted release notifies the release managers',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'edited' AND user_id = test.uid('rm1')
            AND release_id = (SELECT id FROM t_ids WHERE name='SOD')));
SELECT test.ok('the editor is not notified about their own edit',
    NOT EXISTS (SELECT 1 FROM notifications WHERE kind = 'edited' AND user_id = test.uid('qa1')
                AND release_id = (SELECT id FROM t_ids WHERE name='SOD')));
SELECT set_config('app.actor_id', test.uid('rm2')::text, false);
UPDATE releases SET notes = 'deploy window agreed with ops' WHERE ticket_id = 'T-SOD';
SELECT test.ok('release manager edit notifies the developer and QA leads',
    (SELECT count(DISTINCT user_id) FROM notifications WHERE kind = 'edited'
      AND release_id = (SELECT id FROM t_ids WHERE name='SOD') AND message LIKE 'Mei Ling%Notes updated.'
      AND user_id IN (test.uid('dev1'), test.uid('qa1'), test.uid('qa2'))) = 3);
SELECT test.ok('edit history records old and new values',
    (SELECT details->'changes'->'qa_status'->>'from' = 'Open' AND details->'changes'->'qa_status'->>'to' = 'Ready for UAT'
       FROM audit_logs WHERE action = 'RELEASE_TRACKING_UPDATED'
        AND entity_id = (SELECT id FROM t_ids WHERE name='SOD') ORDER BY seq LIMIT 1));
SELECT test.ok('content edit after sign-off says signatures were invalidated',
    EXISTS (SELECT 1 FROM notifications WHERE kind = 'revised' AND release_id = (SELECT id FROM t_ids WHERE name='EDIT')
            AND message LIKE '%Patch size: 0 → 5000%signatures were invalidated%'));
SELECT set_config('app.actor_id', test.uid('dev1')::text, false);
SELECT test.ok('nobody is notified about their own action',
    NOT EXISTS (SELECT 1 FROM notifications WHERE user_id = actor_id));
SELECT test.ok('a user cannot mark someone else''s notification as read',
    NOT mark_notification_read((SELECT max(id) FROM notifications WHERE user_id = test.uid('dev1')), test.uid('qa1')));
SELECT test.ok('a user can mark their own notification as read',
    mark_notification_read((SELECT max(id) FROM notifications WHERE user_id = test.uid('dev1')), test.uid('dev1')));
SELECT test.ok('mark all as read reports what it cleared', mark_all_notifications_read(test.uid('dev1')) > 0);
SELECT test.ok('mark all as read clears the unread count',
    NOT EXISTS (SELECT 1 FROM notifications WHERE user_id = test.uid('dev1') AND read_at IS NULL));
SELECT test.expect_error('app role cannot rewrite notifications',
    $$UPDATE notifications SET message = 'x'$$, 'permission denied');
RESET ROLE;

-- ---------------------------------------------------------------- the core guarantee, even for the DB owner
RESET ROLE;
SELECT test.expect_error('owner cannot UPDATE a release straight to APPROVED',
    format('UPDATE releases SET gov_status = ''APPROVED'' WHERE id = %s', (SELECT id FROM t_ids WHERE name='EXPIRE')),
    'required signatures missing');
SELECT test.expect_error('owner cannot skip the state machine',
    format('UPDATE releases SET gov_status = ''DEPLOYED'' WHERE id = %s', (SELECT id FROM t_ids WHERE name='SOD')),
    'illegal transition');
SELECT test.expect_error('owner cannot edit approval signatures',
    'UPDATE approvals SET decision = ''APPROVE'' WHERE decision = ''REJECT''', 'immutable');
SELECT test.expect_error('owner cannot UPDATE audit rows', 'UPDATE audit_logs SET action = ''x''', 'append-only');
SELECT test.expect_error('owner cannot DELETE audit rows', 'DELETE FROM audit_logs', 'append-only');

-- ---------------------------------------------------------------- audit chain
SELECT test.ok('audit chain intact after all operations', (SELECT is_valid FROM verify_audit_chain()));
SELECT test.ok('every governance action was audited',
    (SELECT count(DISTINCT action) FROM audit_logs) >= 12);

-- Tamper with history the only way possible (disable the guard), then detect it.
ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_no_update;
UPDATE audit_logs SET details = '{"tampered": true}' WHERE seq = 10;
ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_no_update;
SELECT test.ok('tampering detected at the altered row',
    (SELECT NOT is_valid AND first_invalid_seq = 10 FROM verify_audit_chain()));

\echo 'ALL GOVERNANCE TESTS PASSED'
