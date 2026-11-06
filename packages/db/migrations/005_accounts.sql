-- =====================================================================
-- 005_accounts.sql
-- Self-registration and account administration.
--
-- Anyone can register and REQUEST a role, but a request grants nothing:
-- the account stays PENDING (no role, cannot sign or edit anything) until a
-- Release Manager approves it and assigns the role, which may differ from
-- the one requested. This prevents self-escalation - nobody can make
-- themselves a Release Manager by filling in a form.
--
-- Account rules:
--   * only RELEASE_MANAGERs administer accounts
--   * nobody can review, re-role or disable their OWN account, so there is
--     always at least one active Release Manager (the one acting)
--   * every account action is written to the hash-chained audit log
-- =====================================================================

CREATE FUNCTION register_user(p_username TEXT, p_full_name TEXT, p_email TEXT,
                              p_password TEXT, p_requested_role TEXT)
RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_username TEXT := lower(trim(p_username));
    v_email    TEXT := nullif(lower(trim(coalesce(p_email, ''))), '');
    v_id       INT;
BEGIN
    IF v_username !~ '^[a-z0-9._-]{3,32}$' THEN
        RAISE EXCEPTION 'username must be 3-32 characters: letters, digits, dot, dash or underscore'
            USING ERRCODE = 'check_violation';
    END IF;
    IF length(coalesce(p_password, '')) < 8 OR p_password !~ '[A-Za-z]' OR p_password !~ '[0-9]' THEN
        RAISE EXCEPTION 'password must be at least 8 characters and contain a letter and a digit'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM roles WHERE code = p_requested_role) THEN
        RAISE EXCEPTION 'requested role must be DEVELOPER, QA_LEAD or RELEASE_MANAGER'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM users WHERE username = v_username) THEN
        RAISE EXCEPTION 'username "%" is already taken', v_username USING ERRCODE = 'unique_violation';
    END IF;
    IF v_email IS NOT NULL AND EXISTS (SELECT 1 FROM users WHERE email = v_email) THEN
        RAISE EXCEPTION 'an account with this email already exists' USING ERRCODE = 'unique_violation';
    END IF;

    INSERT INTO users(username, full_name, email, password_hash, status, requested_role)
    VALUES (v_username, trim(p_full_name), v_email, crypt(p_password, gen_salt('bf', 10)),
            'PENDING', p_requested_role)
    RETURNING id INTO v_id;

    PERFORM write_audit(v_id, 'USER_REGISTERED', 'user', v_id,
        jsonb_build_object('username', v_username, 'requested_role', p_requested_role));
    RETURN v_id;
END $$;

-- Lock the target account and check the actor may administer it.
CREATE FUNCTION _lock_account_for_admin(p_user INT, p_actor INT) RETURNS users
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    u users%ROWTYPE;
BEGIN
    PERFORM require_role(p_actor, 'RELEASE_MANAGER');
    IF p_user = p_actor THEN
        RAISE EXCEPTION 'you cannot administer your own account' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT * INTO u FROM users WHERE id = p_user FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'user % not found', p_user USING ERRCODE = 'no_data_found'; END IF;
    RETURN u;
END $$;

-- Approve (assigning a role) or reject a pending registration.
CREATE FUNCTION review_registration(p_user INT, p_actor INT, p_approve BOOLEAN,
                                    p_role TEXT DEFAULT NULL, p_note TEXT DEFAULT NULL)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    u      users%ROWTYPE := _lock_account_for_admin(p_user, p_actor);
    v_role TEXT := coalesce(p_role, u.requested_role);
BEGIN
    IF u.status <> 'PENDING' THEN
        RAISE EXCEPTION 'account % is % - only PENDING registrations can be reviewed', u.username, u.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_approve THEN
        IF NOT EXISTS (SELECT 1 FROM roles WHERE code = v_role) THEN
            RAISE EXCEPTION 'unknown role %', v_role USING ERRCODE = 'check_violation';
        END IF;
        INSERT INTO user_roles(user_id, role_code) VALUES (p_user, v_role);
        UPDATE users SET status = 'ACTIVE', reviewed_by = p_actor, reviewed_at = now(), review_note = p_note
         WHERE id = p_user;
        PERFORM write_audit(p_actor, 'USER_APPROVED', 'user', p_user,
            jsonb_build_object('username', u.username, 'requested_role', u.requested_role,
                               'granted_role', v_role, 'note', p_note));
        RETURN 'ACTIVE';
    END IF;
    UPDATE users SET status = 'REJECTED', reviewed_by = p_actor, reviewed_at = now(), review_note = p_note
     WHERE id = p_user;
    PERFORM write_audit(p_actor, 'USER_REJECTED', 'user', p_user,
        jsonb_build_object('username', u.username, 'requested_role', u.requested_role, 'note', p_note));
    RETURN 'REJECTED';
END $$;

-- Move an approved user to another role.
CREATE FUNCTION change_user_role(p_user INT, p_actor INT, p_role TEXT, p_note TEXT DEFAULT NULL)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    u      users%ROWTYPE := _lock_account_for_admin(p_user, p_actor);
    v_old  TEXT;
BEGIN
    IF u.status <> 'ACTIVE' THEN
        RAISE EXCEPTION 'only ACTIVE accounts can change role (account is %)', u.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM roles WHERE code = p_role) THEN
        RAISE EXCEPTION 'unknown role %', p_role USING ERRCODE = 'check_violation';
    END IF;
    SELECT role_code INTO v_old FROM user_roles WHERE user_id = p_user;
    IF v_old = p_role THEN
        RETURN p_role;
    END IF;
    UPDATE user_roles SET role_code = p_role WHERE user_id = p_user;
    PERFORM write_audit(p_actor, 'USER_ROLE_CHANGED', 'user', p_user,
        jsonb_build_object('username', u.username, 'from', v_old, 'to', p_role, 'note', p_note));
    RETURN p_role;
END $$;

-- Disable (e.g. a leaver) or re-enable an account.
CREATE FUNCTION set_user_status(p_user INT, p_actor INT, p_status TEXT, p_note TEXT DEFAULT NULL)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    u users%ROWTYPE := _lock_account_for_admin(p_user, p_actor);
BEGIN
    IF p_status NOT IN ('ACTIVE', 'DISABLED') THEN
        RAISE EXCEPTION 'status must be ACTIVE or DISABLED' USING ERRCODE = 'check_violation';
    END IF;
    IF u.status NOT IN ('ACTIVE', 'DISABLED') THEN
        RAISE EXCEPTION 'account is % - review the registration instead', u.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF u.status = p_status THEN
        RETURN p_status;
    END IF;
    UPDATE users SET status = p_status WHERE id = p_user;
    PERFORM write_audit(p_actor, CASE p_status WHEN 'DISABLED' THEN 'USER_DISABLED' ELSE 'USER_ENABLED' END,
        'user', p_user, jsonb_build_object('username', u.username, 'note', p_note));
    RETURN p_status;
END $$;

-- Read model for the account administration page.
CREATE VIEW v_users AS
SELECT u.id, u.username, u.full_name, u.email, u.status, u.requested_role,
       ur.role_code AS role, u.created_at, u.reviewed_at, u.review_note,
       rv.username AS reviewed_by_username
FROM users u
LEFT JOIN user_roles ur ON ur.user_id = u.id
LEFT JOIN users rv ON rv.id = u.reviewed_by;

REVOKE EXECUTE ON FUNCTION register_user(TEXT, TEXT, TEXT, TEXT, TEXT), _lock_account_for_admin(INT, INT),
    review_registration(INT, INT, BOOLEAN, TEXT, TEXT), change_user_role(INT, INT, TEXT, TEXT),
    set_user_status(INT, INT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION register_user(TEXT, TEXT, TEXT, TEXT, TEXT),
    review_registration(INT, INT, BOOLEAN, TEXT, TEXT), change_user_role(INT, INT, TEXT, TEXT),
    set_user_status(INT, INT, TEXT, TEXT) TO release_app;
GRANT SELECT ON v_users TO release_app;
