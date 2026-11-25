"""Rescan every quarantined / never-scanned verification document.

    python -m scripts.rescan_documents

Use after enabling scanning on an existing deployment (documents stored
before malware scanning existed are PENDING_SCAN and are withheld until a
scan passes) or after a scanner outage. Prints counts per resulting status.
"""
from __future__ import annotations

from app.db.session import SessionLocal
from app.services import document_quarantine


def main() -> None:
    db = SessionLocal()
    try:
        counts = document_quarantine.rescan_pending(db)
    finally:
        db.close()
    print("Document rescan:", counts)


if __name__ == "__main__":
    main()
