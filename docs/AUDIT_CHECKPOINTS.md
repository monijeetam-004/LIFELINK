# Audit checkpoints: setup, rotation and limits

This document was written on 2026-10-31 in response to audit finding E4.

## What the audit trail does and does not prove

| Layer | Detects | Does not detect |
|---|---|---|
| **Hash chain** (`app/core/audit_chain.py`). Each entry hashes its own content, its index and the previous entry's hash. | Edits to a sealed entry. Deletions, insertions and reordering inside the chain. | A database owner who recomputes every later hash, because the chain has no secret. A cut-off tail (the newest entries removed). |
| **Append-only trigger** (migration `c9d0e1f2a3b4`). | Ordinary `UPDATE`, `DELETE` and `TRUNCATE` are rejected. The one-time sealing update of an unsealed row is allowed. | An owner who disables the trigger. Verification reports a disabled or missing trigger as a failure. |
| **Signed checkpoints** (`app/services/audit_checkpoint_service.py`). An HMAC-SHA256 over (sequence number, entry hash, previous MAC), written outside the database. | Full recomputation, and truncation below the latest checkpoint, by anyone who lacks the key. | Someone who holds both the database owner role and the key. Removal of the newest lines of the checkpoint log itself, unless that log is on append-only (WORM) storage. Rows changed and recomputed before the first checkpoint. |
| **External anchor** (`GET /admin/audit-log/head`, recorded somewhere else, then passed to verify). | The same as checkpoints, relative to wherever the head was recorded. | Only as strong as that external record. |

Every verification (`POST /admin/audit-log/verify`) now returns `tamper_evidence`. It takes one of three values:

* **`INTERNAL_CONSISTENCY_ONLY`.** Neither checkpoints nor an anchor were checked. The response also carries a warning saying so.
* **`CHECKPOINTED`.** Signed checkpoints were checked.
* **`EXTERNALLY_ANCHORED`.** An externally recorded head was checked.

`/health/ready` also reports `audit_checkpoints` (never the key).

**This is tamper evidence, not immutability.** LifeLink makes no immutability claim.

## The container stack requires checkpoints

`backend/docker-compose.yml` makes checkpoints mandatory:

* **Key.** It reads `AUDIT_CHECKPOINT_KEY` from the environment or `.env`. `docker compose up` stops with an error if the key is missing.
* **Fail-closed start.** It sets `AUDIT_CHECKPOINT_REQUIRED=true`. The API then refuses to start without a usable key and path, and reports *not ready* (503) if the checkpoint log stops verifying.
* **Storage.** The log is written to the `auditcheckpoints` volume, mounted only by `api` and `deadline-monitor`. The `db` container never mounts it.
* **Least privilege.** The RQ worker writes no checkpoints and does not receive the key: the compose file sets `AUDIT_CHECKPOINT_KEY` to empty for it, overriding `.env`. Before this was done, the worker inherited the key without a path and refused to start; the 2026-10-31 container clean start found that. A test now guards every service that reads `.env`.
* **Schedule.** The deadline monitor writes a checkpoint every `AUDIT_CHECKPOINT_INTERVAL_MINUTES` (default 15). A failure is logged and does not stop the monitor.

A native development run (`.env.example`) leaves checkpoints off. In that configuration verification states `INTERNAL_CONSISTENCY_ONLY`.

## Setup

1. Generate a key, at least 32 characters long and different from `SECRET_KEY`:

   ```
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Put the key in the deployment's secret store. Never commit it, and never put it in the database.
3. Set:

   ```
   AUDIT_CHECKPOINT_KEY=<secret>
   AUDIT_CHECKPOINT_KEY_ID=k1
   AUDIT_CHECKPOINT_PATH=<file on storage the DB credentials cannot write>
   ```

4. Set `AUDIT_CHECKPOINT_REQUIRED=true` where tamper evidence is required.
5. Check the setup. `python -m scripts.audit_checkpoint verify` should exit 0, and `POST /admin/audit-log/verify` should report `CHECKPOINTED` after the first checkpoint has been written.

## Rotating the key

1. Generate a new key and pick a new id, for example `k2`.
2. Set `AUDIT_CHECKPOINT_RETIRED_KEYS="k1=<old key>"`, then set `AUDIT_CHECKPOINT_KEY=<new>` and `AUDIT_CHECKPOINT_KEY_ID=k2`.
3. Restart `api` and `deadline-monitor`. New checkpoints are signed with `k2`, and old ones still verify with the retired key. This is tested in `tests/test_hardening_audit_chain.py::test_rotated_key_keeps_old_checkpoints_verifiable`.
4. Keep the retired key for as long as its checkpoints need to be verifiable.

## Remaining limitation, stated plainly

The checkpoint log is local to the deployment: a Docker volume, or a file path. An operator who controls the host can read the key from the API's environment and also rewrite the volume. Protection against that operator needs either of these:

* checkpoints copied to a system the operator does not control, such as object-locked storage or another organisation's log;
* an external anchor recorded somewhere independent.

Neither has been set up for the evaluated deployments.
