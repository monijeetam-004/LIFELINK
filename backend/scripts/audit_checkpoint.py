"""Hardening -- write or verify HMAC-signed audit-chain checkpoints.

    python -m scripts.audit_checkpoint write    # seal stragglers, append a checkpoint
    python -m scripts.audit_checkpoint verify   # checkpoints + full chain; exit 1 on failure
    python -m scripts.audit_checkpoint verify --incremental

Run `write` on a schedule (cron / Kubernetes CronJob, e.g. hourly) and
`verify` at least daily. Needs AUDIT_CHECKPOINT_KEY and
AUDIT_CHECKPOINT_PATH (see docs/LIFELINK_AUDIT_CHAIN.md).
"""
from __future__ import annotations

import sys

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.services import audit_chain_service, audit_checkpoint_service


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"write", "verify"}:
        print(__doc__, file=sys.stderr)
        return 2
    if not settings.audit_checkpoints_enabled:
        print("signed checkpoints are not configured "
              "(AUDIT_CHECKPOINT_KEY / AUDIT_CHECKPOINT_PATH)", file=sys.stderr)
        return 2
    audit_chain_service.seal_pending_now(engine)
    db = SessionLocal()
    try:
        if argv[0] == "write":
            cp = audit_checkpoint_service.write_checkpoint(db)
            print(f"checkpoint written: seq={cp.seq}" if cp else "nothing sealed yet")
            return 0
        cp_state = audit_checkpoint_service.verify_against_db(db)
        after_seq, after_hash = 0, audit_chain_service.ac.GENESIS_HASH
        if "--incremental" in argv and cp_state.ok and cp_state.latest_seq is not None:
            after_seq, after_hash = cp_state.latest_seq, cp_state.latest_hash
        chain = audit_chain_service.verify_chain(db, after_seq=after_seq, after_hash=after_hash)
        print(f"checkpoints: ok={cp_state.ok} checked={cp_state.checked} "
              f"latest_seq={cp_state.latest_seq} reason={cp_state.reason}")
        brk = chain.first_break
        print(f"chain: ok={chain.ok} checked={chain.checked} head_seq={chain.head_seq} "
              f"unsealed={chain.unsealed} break={brk.reason if brk else None}")
        return 0 if (cp_state.ok and chain.ok) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
