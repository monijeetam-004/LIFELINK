"""Phase 25: Complete Testing, Regression & QA — new regression tests for
real, confirmed gaps found while independently auditing the existing suite
(see LIFELINK_PHASE_25_TESTING_AUDIT.md for the full audit).

Every test here targets a genuine hole identified by reading the actual
source and the actual existing tests, not a hypothetical. Nothing here
weakens or replaces an existing test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from datetime import date

import psycopg
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.session import SessionLocal
from app.models.password_reset_otp import PasswordResetOTP
from tests.helpers import auth_client, extract_otp

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# 1. Password-reset OTP verification race (Phase 25 Section 7/19 -- OTP
#    verification was a confirmed, real gap: no existing test drives
#    concurrent attempts against /auth/reset-password).
#
# Root cause found by reading app/services/auth_service.py::reset_password:
# it does `row = db.scalar(select(PasswordResetOTP)...)` with NO
# `.with_for_update()` -- every other mutate-after-read row in this codebase
# (RegistrationCounter, BloodRequest, Emergency, FamilyLink, inventory,
# VerificationRequest, TransportRoute, Donation-for-certificate) uses
# `with_for_update()` or `db.get(..., with_for_update=True)` for exactly
# this reason. Without the lock, N concurrent wrong-OTP submissions each
# read the same stale `attempts` value, each independently compute
# `attempts + 1`, and the last COMMIT wins -- a classic lost-update: the
# persisted `attempts` count ends up far below the real number of guesses
# made, so `settings.password_reset_max_attempts` (5) can be bypassed by
# guessing in parallel instead of serially.
# --------------------------------------------------------------------------- #
def test_password_reset_otp_lockout_survives_concurrent_wrong_guesses(mailbox) -> None:
    client, _ = auth_client("PATIENT", email="otprace@example.com")
    client.post("/auth/forgot-password", json={"email": "otprace@example.com"})
    real_otp = extract_otp(mailbox)

    # 20 simultaneous WRONG guesses -- well over the 5-attempt cap. If the
    # lockout counter is not properly serialized, most of these lost-update
    # races leave `attempts` far below 20, and the account is never locked.
    barrier = threading.Barrier(20)
    statuses: list[int] = []
    lock = threading.Lock()

    def _guess() -> None:
        barrier.wait()
        c, _ = auth_client("PATIENT")  # unrelated client, same backend/db
        r = c.post(
            "/auth/reset-password",
            json={"email": "otprace@example.com", "otp": "000000", "new_password": "irrelevant pw"},
        )
        with lock:
            statuses.append(r.status_code)

    threads = [threading.Thread(target=_guess) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every wrong guess must fail (400 wrong-code, or 429 once locked out) --
    # none may ever return 200.
    assert all(s in (400, 429) for s in statuses), statuses
    # At least one of the 20 must have observed the lockout kick in.
    assert 429 in statuses, (
        "20 concurrent wrong guesses never tripped the 5-attempt lockout -- "
        f"the attempts counter lost updates under concurrency: {statuses}"
    )

    # The real, correct OTP must now be rejected too -- the account is
    # locked, not merely "still has some attempts left due to undercounting."
    final = client.post(
        "/auth/reset-password",
        json={"email": "otprace@example.com", "otp": real_otp, "new_password": "brand new pw!!"},
    )
    assert final.status_code == 429, (
        "the correct OTP was accepted after 20 concurrent wrong guesses -- "
        "the 5-attempt lockout was bypassed by concurrency"
    )

    db = SessionLocal()
    try:
        row = db.query(PasswordResetOTP).order_by(PasswordResetOTP.created_at.desc()).first()
        assert row is not None
        assert row.attempts >= 5, (
            f"persisted attempts={row.attempts} after 20 concurrent wrong guesses -- "
            "lost updates under concurrency (missing row lock)"
        )
    finally:
        db.close()


def test_password_reset_otp_sequential_brute_force_locks_out_after_max_attempts(
    mailbox,
) -> None:
    """Sequential (non-concurrent) brute force: confirms the basic lockout
    mechanism itself works end-to-end (attempts counter -> 429), which no
    existing test in the suite exercised (only single wrong/right OTP
    submissions were tested before)."""
    client, _ = auth_client("PATIENT", email="otpbrute@example.com")
    client.post("/auth/forgot-password", json={"email": "otpbrute@example.com"})
    real_otp = extract_otp(mailbox)

    for _ in range(5):
        r = client.post(
            "/auth/reset-password",
            json={
                "email": "otpbrute@example.com",
                "otp": "111111",
                "new_password": "nope nope nope",
            },
        )
        assert r.status_code == 400

    # The 6th attempt -- even with the CORRECT code -- must be locked out.
    locked = client.post(
        "/auth/reset-password",
        json={"email": "otpbrute@example.com", "otp": real_otp, "new_password": "brand new pw!!"},
    )
    assert locked.status_code == 429


# --------------------------------------------------------------------------- #
# 2. Donation-logging concurrency (Phase 25 Section 7 explicitly lists
#    "donation creation" as a mutation endpoint to verify for duplicates).
#
# Confirmed by reading app/models/donation.py and app/schemas/donation.py:
# there is no idempotency_key, no unique constraint, and no dedup check on
# POST /donors/me/donations -- unlike blood_requests/emergencies, which both
# have a real (requester, idempotency_key) unique constraint. This test
# documents the actual, current behavior with real concurrent requests
# rather than asserting a fix that wasn't made: two near-simultaneous
# identical submissions against the SAME accepted request currently create
# TWO donation rows. This is reported as a real, found-but-not-fixed gap
# (see LIFELINK_PHASE_25_TESTING_REPORT.md's Bugs Found / Remaining
# Limitations) rather than silently left undocumented -- donations are a
# self-reported history log with no inventory/allocation consequence, so
# this is lower severity than the allocation-integrity bugs already fixed
# elsewhere, and adding a full idempotency-key subsystem (schema migration +
# model + route rewrite) is a scoped feature addition beyond what this
# regression/QA pass verified was necessary with evidence of real user harm.
# --------------------------------------------------------------------------- #
def test_donation_logging_duplicate_submission_current_behavior_is_documented() -> None:
    patient, _ = auth_client("PATIENT")
    req_id = patient.post(
        "/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"}
    ).json()["id"]

    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_POS"})
    accept = donor.post(f"/blood-requests/{req_id}/respond", json={"action": "ACCEPTED"})
    assert accept.status_code == 200, accept.text

    payload = {
        "blood_request_id": req_id,
        "units": 1,
        "donated_on": date.today().isoformat(),
    }
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    lock = threading.Lock()

    def _log() -> None:
        barrier.wait()
        r = donor.post("/donors/me/donations", json=payload)
        with lock:
            statuses.append(r.status_code)

    threads = [threading.Thread(target=_log) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Both requests succeed today (no idempotency guard exists on this
    # endpoint) -- neither 500s, which is the one invariant that must never
    # break, but this documents the real, current duplicate-row outcome.
    assert all(s == 201 for s in statuses), statuses

    my_donations = donor.get("/donors/me/donations").json()
    matching = [d for d in my_donations if d["blood_request_id"] == req_id]
    assert len(matching) == 2, (
        "expected the currently-real duplicate-row behavior (2 donation rows "
        f"for one accepted request from 2 concurrent identical submissions); got {len(matching)}. "
        "If this now returns 1, an idempotency guard was added -- update this test "
        "and the Phase 25 report's Bugs Found / Remaining Limitations sections to "
        "say the gap was closed."
    )


# --------------------------------------------------------------------------- #
# 3. CORS (Phase 25 Section 17 lists CORS explicitly; a genuine gap -- the
#    existing suite has zero tests for it despite CORSMiddleware being
#    actively configured in app/main.py).
# --------------------------------------------------------------------------- #
def test_cors_allows_the_configured_frontend_origin(client) -> None:
    resp = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_does_not_allow_an_unconfigured_origin(client) -> None:
    resp = client.options(
        "/auth/login",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette's CORSMiddleware never blocks the preflight itself; it simply
    # omits the allow-origin header for an origin that isn't on the allow
    # list, which is what actually makes the browser enforce same-origin.
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"


# --------------------------------------------------------------------------- #
# 4. Migration-vs-model schema drift (Phase 25 Section 10 -- a genuine gap:
#    tests/conftest.py builds the schema via Base.metadata.create_all,
#    completely bypassing Alembic, so a real migration file could diverge
#    from the current ORM models undetected by the rest of this suite).
#
# This spins up a real, throwaway scratch database, runs `alembic upgrade
# head` against it in a fresh subprocess (so it gets its own freshly-loaded
# Settings pointed at the scratch DB via DATABASE_URL, independent of the
# already-imported, cached `settings` singleton the rest of the test run
# uses), and compares the resulting real schema to Base.metadata.
# --------------------------------------------------------------------------- #
def test_alembic_head_schema_matches_orm_metadata() -> None:
    # psycopg.connect() wants a plain libpq/postgresql:// URL, not
    # SQLAlchemy's "postgresql+psycopg://" dialect URL.
    base_test_url = os.environ["TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://"
    )
    admin_url = base_test_url.rsplit("/", 1)[0] + "/postgres"
    scratch_db = f"lifelink_migration_check_{uuid.uuid4().hex[:8]}"
    scratch_url_sqlalchemy = (
        os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + f"/{scratch_db}"
    )

    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{scratch_db}"')

    try:
        result = subprocess.run(
            # QA round: the interpreter running this test, not whatever
            # `python3` happens to be first on PATH -- run as
            # `.venv/bin/python -m pytest` the bare name found the system
            # Python, which has no alembic, and the test failed for a reason
            # unrelated to the migrations.
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_DIR,
            env={**os.environ, "DATABASE_URL": scratch_url_sqlalchemy},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed against a clean scratch database:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

        scratch_engine = create_engine(scratch_url_sqlalchemy)
        try:
            inspector = inspect(scratch_engine)
            migrated_tables = {
                t: {c["name"] for c in inspector.get_columns(t)}
                for t in inspector.get_table_names()
                if t != "alembic_version"
            }
        finally:
            scratch_engine.dispose()

        orm_tables = {
            t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables
        }

        missing_tables = set(orm_tables) - set(migrated_tables)
        extra_tables = set(migrated_tables) - set(orm_tables)
        assert not missing_tables, (
            f"tables in the ORM models but never created by `alembic upgrade head`: "
            f"{missing_tables} -- a migration is missing"
        )
        assert not extra_tables, (
            f"tables created by migrations but not declared on any ORM model: "
            f"{extra_tables} -- an orphaned/stale migration or a model that was "
            "removed without a downgrade"
        )

        column_mismatches = {}
        for table_name, orm_cols in orm_tables.items():
            migrated_cols = migrated_tables[table_name]
            if orm_cols != migrated_cols:
                column_mismatches[table_name] = {
                    "in_orm_only": orm_cols - migrated_cols,
                    "in_migration_only": migrated_cols - orm_cols,
                }
        assert not column_mismatches, (
            f"column drift between ORM models and `alembic upgrade head`: {column_mismatches}"
        )
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{scratch_db}' AND pid <> pg_backend_pid()"
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{scratch_db}"')
