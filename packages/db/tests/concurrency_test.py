"""
Concurrency / race-condition test for the multi-signature workflow.

Addresses the "Concurrency deadlocks" risk in Table 3.2 of the interim report.
Uses only the Python standard library + the psql client, so it runs anywhere
PostgreSQL is installed (including the Docker container).

Scenario A - quorum race (repeated TRIALS times):
    A CRITICAL release needs exactly 2 RELEASE_MANAGER signatures (and 2 QA leads). RACERS
    different release managers all try to sign at the same instant.
    Expected: exactly 2 succeed, the rest are refused, the release is
    APPROVED exactly once, no deadlocks, audit chain intact.

Scenario B - throughput / latency:
    WORKERS threads each drive their own releases through submit + sign-offs
    concurrently. Reports p50/p95/max latency of sign_approval() measured
    inside the database, and the deadlock counter.

Usage:
    PGHOST=localhost PGUSER=postgres python tests/concurrency_test.py release_mgmt
The target database must be freshly migrated (see reset_db.sh).
"""
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import threading

DB = sys.argv[1] if len(sys.argv) > 1 else "release_mgmt"
OWNER = os.environ.get("PGUSER", "postgres")
APP = "release_app"
TRIALS = int(os.environ.get("TRIALS", 20))
RACERS = int(os.environ.get("RACERS", 6))
WORKERS = int(os.environ.get("WORKERS", 8))
RELEASES_PER_WORKER = int(os.environ.get("RELEASES_PER_WORKER", 10))


def psql(sql, user=APP, check=True):
    env = dict(os.environ, PGUSER=user, PGAPPNAME="concurrency_test")
    if user == APP:
        env["PGPASSWORD"] = os.environ.get("APP_PGPASSWORD", "release_app")
    p = subprocess.run(["psql", "-d", DB, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return p


def scalar(sql, user=APP):
    return psql(sql, user).stdout.strip().splitlines()[-1]


def setup():
    racers = ", ".join(f"('race_rm{i}', 'Race RM {i}', crypt('x', gen_salt('bf', 4)), 'ACTIVE')" for i in range(RACERS))
    psql(f"""
        INSERT INTO users(username, full_name, password_hash, status) VALUES {racers}
            ON CONFLICT (username) DO NOTHING;
        INSERT INTO user_roles SELECT id, 'RELEASE_MANAGER' FROM users WHERE username LIKE 'race_rm%'
            ON CONFLICT DO NOTHING;
        SELECT register_model('concurrency-test', 'test', now(), '{{}}', '{{}}', '/dev/null');
    """, user=OWNER)
    return scalar("SELECT id FROM model_versions WHERE version_tag = 'concurrency-test'")


def uid(name):
    return scalar(f"SELECT id FROM users WHERE username = '{name}'")


def new_release(model_id, tier, tag, creator):
    prob = {"LOW": 0.05, "MEDIUM": 0.2, "HIGH": 0.4, "CRITICAL": 0.8}[tier]
    rid = scalar(f"""
        INSERT INTO releases(app_name, version, target_date, responsible_qa, ticket_id, fix_type,
                             description, created_by)
        VALUES ('Load App', '1.0.0', current_date, 'QA', '{tag}', 'Other', 'load test', {creator})
        RETURNING id;""")
    psql(f"SELECT record_prediction({rid}, {creator}, {model_id}, {prob}, '{tier}', '{{}}', '{{}}');")
    psql(f"SELECT submit_release({rid}, {creator});")
    return rid


def scenario_a(model_id):
    dev, qa1, qa2 = uid("dev1"), uid("qa1"), uid("qa2")
    racer_ids = [uid(f"race_rm{i}") for i in range(RACERS)]
    failures = []
    for trial in range(TRIALS):
        rid = new_release(model_id, "CRITICAL", f"RACE-{trial}", dev)
        for actor, role in ((qa1, "QA_LEAD"), (qa2, "QA_LEAD")):
            psql(f"SELECT sign_approval({rid}, {actor}, '{role}', 'APPROVE', NULL, TRUE);")

        start = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(milliseconds=600)).isoformat()
        results = [None] * RACERS

        def race(i):
            # Every session sleeps until the same wall-clock instant, then signs.
            p = psql(f"""SELECT pg_sleep(greatest(0, extract(epoch FROM '{start}'::timestamptz - clock_timestamp())));
                         SELECT sign_approval({rid}, {racer_ids[i]}, 'RELEASE_MANAGER', 'APPROVE', NULL, TRUE);""",
                     check=False)
            results[i] = p

        threads = [threading.Thread(target=race, args=(i,)) for i in range(RACERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok = sum(1 for r in results if r.returncode == 0)
        errors = [r.stderr.strip() for r in results if r.returncode != 0]
        # Late signers must be refused cleanly: either the quota is full or the
        # release has already moved to APPROVED. Anything else is a failure.
        bad_errors = [e for e in errors
                      if "already has the 2 approval" not in e and "release is APPROVED" not in e]
        status = scalar(f"SELECT gov_status FROM releases WHERE id = {rid}")
        approvals = int(scalar(f"""SELECT count(*) FROM approvals WHERE release_id = {rid}
                                   AND role_code = 'RELEASE_MANAGER' AND invalidated_at IS NULL"""))
        transitions = int(scalar(f"""SELECT count(*) FROM audit_logs WHERE entity_id = {rid}
                                     AND action = 'STATUS_CHANGED' AND details->>'to' = 'APPROVED'"""))
        if not (ok == 2 and approvals == 2 and status == "APPROVED" and transitions == 1 and not bad_errors):
            failures.append(dict(trial=trial, ok=ok, approvals=approvals, status=status,
                                 transitions=transitions, errors=bad_errors))
    return failures


def scenario_b(model_id):
    devs = [uid("dev1"), uid("dev2")]
    qas = [uid("qa1"), uid("qa2")]
    rms = [uid("rm1"), uid("rm2")]
    latencies, errors = [], []
    lock = threading.Lock()

    def worker(w):
        for n in range(RELEASES_PER_WORKER):
            try:
                rid = new_release(model_id, "LOW", f"LOAD-{w}-{n}", devs[w % 2])
                for actor, role in ((qas[w % 2], "QA_LEAD"), (rms[w % 2], "RELEASE_MANAGER")):
                    out = psql(f"""DO $$ DECLARE t timestamptz := clock_timestamp(); s text; BEGIN
                                     s := sign_approval({rid}, {actor}, '{role}', 'APPROVE');
                                     RAISE NOTICE 'LAT %', extract(epoch FROM clock_timestamp() - t) * 1000;
                                   END $$;""").stderr
                    ms = float(re.search(r"LAT ([0-9.]+)", out).group(1))
                    with lock:
                        latencies.append(ms)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(str(e))

    t0 = dt.datetime.now()
    threads = [threading.Thread(target=worker, args=(w,)) for w in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = (dt.datetime.now() - t0).total_seconds()
    approved = int(scalar("SELECT count(*) FROM releases WHERE ticket_id LIKE 'LOAD-%' AND gov_status = 'APPROVED'"))
    return latencies, errors, approved, elapsed


def main():
    deadlocks_before = int(scalar(f"SELECT deadlocks FROM pg_stat_database WHERE datname = '{DB}'", OWNER))
    model_id = setup()

    failures = scenario_a(model_id)
    print(f"[A] quorum race: {TRIALS} trials x {RACERS} simultaneous release managers")
    print(f"    trials with wrong outcome: {len(failures)}")
    for f in failures[:5]:
        print("    ", json.dumps(f))

    lat, errs, approved, elapsed = scenario_b(model_id)
    expected = WORKERS * RELEASES_PER_WORKER
    print(f"[B] load: {WORKERS} concurrent workers, {expected} releases, {len(lat)} signatures in {elapsed:.1f}s")
    print(f"    releases approved: {approved}/{expected}, errors: {len(errs)}")
    if lat:
        q = statistics.quantiles(lat, n=100)
        print(f"    sign_approval latency (ms, in-database): p50={q[49]:.2f} p95={q[94]:.2f} max={max(lat):.2f}")
    for e in errs[:5]:
        print("    ", e)

    deadlocks = int(scalar(f"SELECT deadlocks FROM pg_stat_database WHERE datname = '{DB}'", OWNER)) - deadlocks_before
    chain = scalar("SELECT is_valid || ' ' || rows_checked FROM verify_audit_chain()")
    print(f"[C] deadlocks detected: {deadlocks}; audit chain valid/rows: {chain}")

    passed = not failures and not errs and approved == expected and deadlocks == 0 and chain.startswith("t")
    print("CONCURRENCY TEST", "PASSED" if passed else "FAILED")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
