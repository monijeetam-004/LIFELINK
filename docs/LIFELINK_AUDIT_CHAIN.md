# LifeLink — Audit Hash Chain (tamper-evident, not immutable)

Introduced in Phase E. Code: `backend/app/core/audit_chain.py` (pure), `backend/app/services/audit_chain_service.py` (sealing + verification), the hooks at the bottom of `backend/app/services/audit_service.py`, and two admin endpoints in `backend/app/api/routes/admin.py`. Tests: `backend/tests/test_phase_e_audit_chain.py`.

## What it is

Every row in `audit_logs` is, shortly after its transaction commits, *sealed*: it is given a sequence number `chain_seq` (1, 2, 3, … with no gaps), the previous sealed row's hash as `prev_hash`, and its own `entry_hash`:

```
entry_hash = SHA-256( canonical_json({
    v: 1, seq, prev_hash,
    id, actor_user_id, action, target_type, target_id, ip, user_agent,
    meta, created_at
}) )
```

The canonical form is compact JSON with sorted keys and ASCII escaping; UUIDs are lowercase hyphenated strings; `created_at` is UTC ISO-8601 with microseconds; `meta` is exactly what Postgres returns from JSONB. `meta` is normalised through a JSON round-trip *before* it is stored, so what is stored is what is hashed. The first row's `prev_hash` is 64 zeros.

`POST /admin/audit-log/verify` recomputes every sealed row from `seq 1` and reports the first break: a content change (hash mismatch), a deletion or insertion (sequence gap or `prev_hash` mismatch), or a re-linked chain. `GET /admin/audit-log/head` returns the current head (`chain_seq`, `entry_hash`) without verifying.

## What it is not

The words matter, so they are stated plainly here and in the UI:

- **Not immutable.** The database does not stop anyone with write access from running `UPDATE` or `DELETE` on `audit_logs`. The chain makes such changes *detectable*; it does not prevent them.
- **Not a blockchain and not a signature.** There is no secret key. Someone with full database write access can edit a row *and recompute every later hash*, leaving an internally consistent chain. `test_full_recompute_is_NOT_detectable_without_anchor_but_is_with_one` proves this limitation rather than hiding it.
- **Truncation of the newest rows** leaves a shorter chain that is still internally consistent. Also proven by a test (`test_tail_truncation_needs_an_anchor_to_detect`).

Both of those are caught by **anchoring**: record the head (`seq` + `hash`) somewhere outside LifeLink's database — a ticket, an email to compliance, a separate log store — and pass it to `POST /admin/audit-log/verify?anchor_seq=…&anchor_hash=…`. If the anchored entry is missing or its hash changed, verification fails with an explicit reason. **Production hardening added automated, HMAC-signed anchoring** (next section).

- **History before Phase E** was sealed by the migration in `(created_at, id)` order. The chain can only attest to those rows' content *as of the migration*; any earlier edit is undetectable.

## Production hardening (this round)

### Threat model

| Attack on sealed entries | Detected by | Prevented by |
|---|---|---|
| Edit a row's content | chain (hash mismatch) | append-only guard (non-owner roles) |
| Delete a row in the middle | chain (seq gap / prev_hash) | guard |
| Insert a forged row | chain (seq / prev_hash) | guard |
| Reorder rows (swap `chain_seq`) | chain (`test_reordering_rows_is_detected_by_the_chain`) | guard |
| Truncate the newest rows | **signed checkpoint** (entry named by a checkpoint missing) or manual anchor | guard (DELETE/TRUNCATE refused) |
| Edit + recompute every later hash | **signed checkpoint** (checkpointed entry's hash changed) or manual anchor | guard |
| Forge / edit a checkpoint | HMAC (key not in the DB) | — |
| Remove / reorder a checkpoint line in the middle | checkpoint `prev_mac` chain | — |
| Remove the newest checkpoint lines | **not detectable** from the log alone | append-only/WORM storage for the log |
| Owner disables the guard, edits, recomputes, and also holds the checkpoint key | **not detectable** | separation of duties (key not available to DB operators) |

### 1. Append-only guard in the database (`app/core/audit_guard.py`, migration `c9d0e1f2a3b4`)

PostgreSQL triggers refuse `DELETE` and `TRUNCATE` (including `TRUNCATE users CASCADE`) of audit entries, any `UPDATE` of a sealed entry, and any change to an unsealed entry's content columns — only the sealer's one-time fill of the chain columns is allowed. Tests: `test_guard_refuses_update_delete_truncate_of_audit_entries`, `test_guard_freezes_unsealed_content_but_lets_the_sealer_seal`. The existing tamper tests now lift the guard first (`ALTER TABLE audit_logs DISABLE TRIGGER USER`), exactly as an attacker holding the **table owner** role would — which is the guard's limit: the owner (or a superuser) can disable it. **Deployment:** run migrations as an owner role and the API/worker as a separate role with only `SELECT, INSERT, UPDATE` on `audit_logs` (not owner), so a compromised application cannot switch the guard off. (Not executed here: this environment runs everything as one role; the role split is a deployment step, documented, not verified.)

Consequence: a user referenced by a sealed entry cannot be hard-deleted (the FK's `ON DELETE SET NULL` would rewrite a sealed entry — and would previously have silently broken the chain). The app never hard-deletes users; `scripts/e2e_seed.py` now resets its fixture users in place instead of deleting them (`test_hard_deleting_a_user_referenced_by_sealed_audit_is_refused`).

### 2. HMAC-signed checkpoints outside the database (`app/services/audit_checkpoint_service.py`)

`checkpoint = {v, key_id, seq, entry_hash, created_at, prev_mac}`, `mac = HMAC-SHA256(key[key_id], canonical_json(checkpoint))`, one JSON line appended (`O_APPEND` + `fsync`) to `AUDIT_CHECKPOINT_PATH`. The key (`AUDIT_CHECKPOINT_KEY`, ≥ 32 chars, must differ from `SECRET_KEY`) is never stored in the database, so a database-only attacker cannot produce a checkpoint matching a recomputed chain. Rotation: set a new key/id and keep the old one in `AUDIT_CHECKPOINT_RETIRED_KEYS=id=key;…`. A log that does not verify is never extended.

- Write: `python -m scripts.audit_checkpoint write` (schedule hourly) or `POST /admin/audit-log/checkpoint` (admin, origin-checked, audited as `admin.audit_checkpoint_written`).
- Verify: `python -m scripts.audit_checkpoint verify` (exit 1 on failure) or `POST /admin/audit-log/verify` — when checkpoints are configured they are always checked, and the response carries `checkpoints: {enabled, ok, checked, latest_seq, reason}`; the admin panel shows it.
- Tests (`tests/test_hardening_audit_chain.py`): full recompute detected; truncation below a checkpoint detected; edited / attacker-keyed / removed / reordered / unknown-key / malformed checkpoints detected; retired key still verifies; endpoints; config validation.
- Verified against the dev database (7,821 entries) with the script: write → verify (full) → verify --incremental, all exit 0.

**Storage requirement (not enforceable by the app):** `AUDIT_CHECKPOINT_PATH` must be on storage the database credentials cannot write, ideally append-only/object-locked, or the lines copied to a second system. The docker-compose stack leaves checkpoints disabled (no key is shipped).

### 3. Incremental verification

`POST /admin/audit-log/verify?mode=incremental` (and `scripts.audit_checkpoint verify --incremental`) re-hashes only entries after the latest *verified* checkpoint, after confirming that checkpoint's entry still has the signed hash. Limitation, proven by `test_incremental_verification_and_its_limitation`: an edit to an older entry that was **not** followed by a recompute is only found by a full verification — schedule full verification too (e.g. daily) and incremental more often. Measured cost: see `docs/LIFELINK_HARDENING_REPORT.md`.

## Why sealing happens after commit (design decision)

Two obvious designs were rejected:

1. *Naive*: inside `record()`, `SELECT` the latest hash, then `INSERT` the new row chained to it. Two concurrent transactions read the same "latest" hash and fork the chain. Explicitly forbidden by the Phase E brief.
2. *Locked in-transaction*: take a global lock inside `record()` before reading the head. Almost every write route in this app audits while it already holds row locks (`SELECT … FOR UPDATE` on a blood request, shipment, donation). Transaction A holding the audit lock and waiting for a row that B holds, while B waits for the audit lock, is a deadlock — and the lock would serialise every write in the app for the whole request.

What was built instead: business transactions only `INSERT` plain rows (chain columns `NULL`). After the transaction has committed **and returned its connection to the pool** (`after_transaction_end` on the root transaction), a separate short transaction seals:

```
BEGIN;
SELECT pg_advisory_xact_lock(<fixed key>);      -- one sealer at a time
read head (max chain_seq); read unsealed rows ORDER BY created_at, id
UPDATE each: chain_seq, prev_hash, entry_hash, hash_version, sealed_at
COMMIT;
```

The sealer waits only on the advisory lock and on audit rows no business transaction ever locks, so it cannot deadlock with business locks, and because the head is read and advanced only under the lock, two sealers can never fork the chain.

**Bug found while building this** (by the concurrency test, not in review): the first version sealed from `after_commit`. At that point SQLAlchemy still holds the session's pooled connection, so the sealer needed a *second* connection. Sixteen concurrent writers, each holding one connection and waiting for another, exhausted the 15-connection pool and deadlocked until the 30 s pool timeout. Moving sealing to `after_transaction_end` (after the connection is released) fixed it; `test_concurrent_writers_never_fork_the_chain` now runs 16 writers × 5 commits cleanly, repeated 5× without a failure. The admin verify endpoint likewise commits its own transaction before sealing.

## Failure behaviour

- Sealing never raises into a request: the business transaction has already committed. A failure is logged (`lifelink.audit_chain`) and the rows stay unsealed.
- Any later sealing run (the next commit that audits anything, or an admin verify) seals stragglers, in order. `test_unsealed_row_is_reported_then_sealed_by_next_run` proves this.
- Verification reports `unsealed` separately and never counts unsealed rows as verified. The UI says "N recent entries are not yet sealed and were not checked."

## Order

Chain order is *seal* order: rows already committed are sealed in `(created_at, id)` order after the current head. A row committed late with an earlier `created_at` is sealed after rows that were sealed before it committed. `created_at` is now set per entry in Python (`utcnow()`), not the column's `now()` default (which is the *transaction* start and made every entry in one transaction tie).

## Cost (measured, dev database, 7k+ rows)

| Operation | Result |
|---|---|
| Head lookup (`ORDER BY chain_seq DESC LIMIT 1`) | Index Scan Backward on `audit_logs_chain_seq_key`, 0.11 ms |
| Unsealed queue / count | partial index `ix_audit_logs_unsealed`, 0.1 ms |
| `record()` + commit, no sealing | median ≈ 2.5–4 ms |
| `record()` + commit + seal | median ≈ 8–11 ms (+≈6 ms per auditing write request: one extra short transaction) |
| Empty sealing run | median 1.9 ms |
| Full `verify_chain` | ≈ 68 µs per entry (7,341 entries in 0.5 s) |

An attempt to cut the sealing cost with `SET LOCAL synchronous_commit = off` showed no measurable improvement and was reverted, per this project's rule for optimisations that don't measurably help.

## Operating it

- `python backend/scripts/check_invariants.py` — runs every cross-domain invariant check and `verify_chain` against `DATABASE_URL`; exits non-zero on any violation.
- Admin UI → Audit Feed → **Audit chain integrity** → *Verify chain*, optionally with a recorded head.
- Recommended: enable signed checkpoints (above) and schedule `scripts.audit_checkpoint write` hourly and `verify` daily; or, without a key, record the head from `GET /admin/audit-log/head` on a schedule into a system LifeLink's database credentials cannot write to.
