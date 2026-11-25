"""Phase E -- run the cross-domain invariant checks
(app.services.invariant_check) against the database in DATABASE_URL and
report violations. Exit code 1 if any invariant is violated.

    python scripts/check_invariants.py
"""

from __future__ import annotations

import sys

from app.db.session import SessionLocal
from app.services.audit_chain_service import verify_chain
from app.services.invariant_check import CHECKS, run_all


def main() -> int:
    db = SessionLocal()
    try:
        results = run_all(db)
        failed = 0
        for check in CHECKS:
            rows = results[check.number]
            mark = "OK " if not rows else "FAIL"
            print(
                f"[{mark}] #{check.number} {check.name}"
                + (f" -- {len(rows)} violation(s)" if rows else "")
            )
            failed += bool(rows)
        chain = verify_chain(db)
        print(
            f"[{'OK ' if chain.ok else 'FAIL'}] audit chain: checked={chain.checked} "
            f"unsealed={chain.unsealed} head={chain.head_seq}"
            + (
                f" break@{chain.first_break.seq}: {chain.first_break.reason}"
                if chain.first_break
                else ""
            )
        )
        failed += not chain.ok
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
