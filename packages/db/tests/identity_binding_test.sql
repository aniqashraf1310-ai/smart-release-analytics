-- =====================================================================
-- Identity-binding test. Run against a FRESHLY migrated database:
--   psql -d release_mgmt -f tests/identity_binding_test.sql
-- Question: can a client connected as release_app sign a release using
-- ANOTHER user's ID? Each check prints OBSERVED <outcome>.
-- =====================================================================
SET client_min_messages = notice;
SELECT register_model('id-test-model', 'test', now(), '{}', '{}', '/dev/null');
SET ROLE release_app;
CREATE TEMP TABLE ids AS SELECT username, id FROM users WHERE username IN ('dev1','dev2','qa1','rm1');

DO $$
DECLARE
    u_dev1 INT := (SELECT id FROM ids WHERE username='dev1');
    u_dev2 INT := (SELECT id FROM ids WHERE username='dev2');
    u_qa1  INT := (SELECT id FROM ids WHERE username='qa1');
    v_model INT := (SELECT id FROM model_versions WHERE version_tag='id-test-model');
    r INT; st TEXT; who INT; aud INT;
BEGIN
    INSERT INTO releases(app_name, version, target_date, responsible_qa, ticket_id, fix_type, description, created_by)
    VALUES ('Identity Test', '1.0.0', current_date, 'QA', 'T-ID', 'Other', 'identity test', u_dev1) RETURNING id INTO r;
    PERFORM record_prediction(r, u_dev1, v_model, 0.05, 'LOW'::risk_tier, '{}', '{}');
    PERFORM submit_release(r, u_dev1, NULL);

    -- The authenticated session belongs to dev2 (a Developer), but the call names qa1.
    PERFORM set_config('app.actor_id', u_dev2::text, true);
    BEGIN
        st := sign_approval(r, u_qa1, 'QA_LEAD', 'APPROVE');
        SELECT user_id INTO who FROM approvals WHERE release_id = r ORDER BY id DESC LIMIT 1;
        SELECT actor_id INTO aud FROM audit_logs WHERE entity_id = r AND action LIKE 'APPROVAL_%' ORDER BY id DESC LIMIT 1;
        RAISE NOTICE 'OBSERVED signature accepted: status=%, approval recorded for user % (qa1=%), audit actor=% (session dev2=%)',
            st, who, u_qa1, aud, u_dev2;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'OBSERVED signature refused: %', SQLERRM;
    END;

    -- Damage limits that still hold: a borrowed ID must hold the role and cannot be the submitter.
    BEGIN
        PERFORM sign_approval(r, u_dev2, 'RELEASE_MANAGER', 'APPROVE');
        RAISE NOTICE 'OBSERVED dev2 ID accepted as RELEASE_MANAGER';
    EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'OBSERVED dev2 ID as RELEASE_MANAGER refused: %', SQLERRM; END;
END $$;
