-- =====================================================================
-- 002_audit.sql
-- Tamper-evident, append-only audit log.
--
-- Every row stores the SHA-256 hash of the previous row (prev_hash) and
-- its own hash (row_hash) computed over its contents + prev_hash.
-- Editing or deleting any historic row therefore breaks the chain, which
-- verify_audit_chain() detects. UPDATE / DELETE / TRUNCATE are blocked by
-- triggers, so even the table owner cannot silently rewrite history
-- without first disabling the triggers (itself a privileged, visible act).
-- =====================================================================

CREATE TABLE audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    seq         BIGINT UNIQUE NOT NULL,       -- gap-free chain position
    occurred_at TIMESTAMPTZ NOT NULL,
    actor_id    INT REFERENCES users(id),
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   INT,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    prev_hash   TEXT NOT NULL,
    row_hash    TEXT NOT NULL
);
CREATE INDEX idx_audit_entity ON audit_logs(entity, entity_id);

-- Canonical text form of a row used for hashing (jsonb::text is key-ordered).
CREATE FUNCTION audit_row_digest(
    p_seq BIGINT, p_at TIMESTAMPTZ, p_actor INT, p_action TEXT,
    p_entity TEXT, p_entity_id INT, p_details JSONB, p_prev TEXT
) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(digest(concat_ws('|',
        p_seq::text,
        to_char(p_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        coalesce(p_actor::text, '-'),
        p_action, p_entity,
        coalesce(p_entity_id::text, '-'),
        p_details::text,
        p_prev), 'sha256'), 'hex');
$$;

CREATE FUNCTION audit_chain_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_prev_seq  BIGINT;
    v_prev_hash TEXT;
BEGIN
    -- Serialise writers so two concurrent transactions cannot both chain
    -- onto the same predecessor. The lock is released at COMMIT/ROLLBACK.
    PERFORM pg_advisory_xact_lock(hashtext('audit_logs_chain'));

    SELECT seq, row_hash INTO v_prev_seq, v_prev_hash
    FROM audit_logs ORDER BY seq DESC LIMIT 1;

    NEW.seq         := coalesce(v_prev_seq, 0) + 1;
    NEW.prev_hash   := coalesce(v_prev_hash, repeat('0', 64));   -- genesis
    NEW.occurred_at := clock_timestamp();
    NEW.row_hash    := audit_row_digest(NEW.seq, NEW.occurred_at, NEW.actor_id,
                          NEW.action, NEW.entity, NEW.entity_id, NEW.details, NEW.prev_hash);
    RETURN NEW;
END $$;

CREATE TRIGGER trg_audit_chain
    BEFORE INSERT ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_chain_before_insert();

CREATE FUNCTION audit_block_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only (% blocked)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_block_mutation();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_block_mutation();

-- Convenience writer used by every governance function.
CREATE FUNCTION write_audit(p_actor INT, p_action TEXT, p_entity TEXT,
                            p_entity_id INT, p_details JSONB DEFAULT '{}'::jsonb)
RETURNS VOID LANGUAGE sql AS $$
    INSERT INTO audit_logs(seq, occurred_at, actor_id, action, entity, entity_id,
                           details, prev_hash, row_hash)
    VALUES (0, now(), p_actor, p_action, p_entity, p_entity_id,
            coalesce(p_details, '{}'::jsonb), '', '');   -- trigger fills real values
$$;

-- Recompute every hash and report the first broken link, if any.
CREATE FUNCTION verify_audit_chain()
RETURNS TABLE(is_valid BOOLEAN, rows_checked BIGINT, first_invalid_seq BIGINT, reason TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
    r          audit_logs%ROWTYPE;
    v_expected TEXT := repeat('0', 64);
    v_seq      BIGINT := 0;
    v_n        BIGINT := 0;
BEGIN
    FOR r IN SELECT * FROM audit_logs ORDER BY seq LOOP
        v_n := v_n + 1;
        IF r.seq <> v_seq + 1 THEN
            RETURN QUERY SELECT FALSE, v_n, r.seq, 'gap in sequence (row deleted?)'; RETURN;
        END IF;
        IF r.prev_hash <> v_expected THEN
            RETURN QUERY SELECT FALSE, v_n, r.seq, 'prev_hash does not match previous row'; RETURN;
        END IF;
        IF r.row_hash <> audit_row_digest(r.seq, r.occurred_at, r.actor_id, r.action,
                                          r.entity, r.entity_id, r.details, r.prev_hash) THEN
            RETURN QUERY SELECT FALSE, v_n, r.seq, 'row contents altered'; RETURN;
        END IF;
        v_expected := r.row_hash;
        v_seq := r.seq;
    END LOOP;
    RETURN QUERY SELECT TRUE, v_n, NULL::BIGINT, 'chain intact';
END $$;
