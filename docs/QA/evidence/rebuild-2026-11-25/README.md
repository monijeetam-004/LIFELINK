# Rebuild check (2026-11-25)

The round-2 privacy changes were re-applied, from the session record, onto the 2026-11-03 project snapshot, because the workspace that held them had been reset. These checks were then re-run on the rebuilt tree, in a fresh workspace with PostgreSQL 16 and Redis 7 only (no ClamAV, MinIO or Mailpit).

| Check | Result | Round-2 reference |
|---|---|---|
| Backend full suite (`pytest`) | **1,472 passed, 10 skipped, 0 failed** (1,482 collected) | 1,482 collected; the 9 extra skips are the ClamAV/MinIO/Mailpit integration tests, skipped because those services were not running here |
| Round-2 and shortlist tests (`test_shortlist_privacy_round2.py`, `test_unified_priority.py`, `test_donor_shortlist.py`) | **46 passed** | 46 passed |
| `test_shortlist_privacy_round2.py` file hash (sha256 prefix) | `07a121019200f390` | identical |
| mypy error count (not a gate) | 113 | 113 |
| Vitest | **184 / 184** | 184 / 184 |
| Ruff, `tsc -b --noEmit`, ESLint, `npm run build` | all pass | all pass |

Repository preparation on the same day: a personal e-mail address used as a sample Gmail sender in `backend/tests/test_phase11_registration_limits_and_gmail.py` was replaced with `lifelink.sender@gmail.com` (26 tests in that file pass); root-level phase reports moved to `docs/phase-reports/`; the old root README moved to `docs/DEVELOPMENT_LOG.md`; the co-authored paper is held back (see `docs/paper/README.md`); `.secrets.baseline` regenerated after every finding was checked to be a development placeholder.
