"""Send one test email through the CONFIGURED mail transport (Mailpit or
Gmail) -- the same code path the RQ worker uses (`email_service._smtp_send`).

    python -m scripts.send_test_email you@example.com

Prints the transport summary (provider, host, port, TLS mode, whether
credentials are set -- never the password) and either "accepted by <host>"
or the failure class and message. Exit code 0 only when the SMTP server
accepted the message. "Accepted" proves the SMTP transaction, not inbox
delivery -- check the recipient mailbox (or Mailpit's UI) for that.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from app.core.config import settings
from app.services import email_service


def main(argv: list[str]) -> int:
    if len(argv) != 1 or "@" not in argv[0]:
        print(__doc__, file=sys.stderr)
        return 2
    print(f"mail transport: {settings.mail_summary()}")
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        email_service._smtp_send(
            argv[0],
            f"LifeLink test email {stamp}",
            f"<p>This is a LifeLink SMTP test sent at {stamp} via "
            f"{settings.mail_provider}.</p>",
        )
    except Exception as exc:  # noqa: BLE001 - report, never echo credentials
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"accepted by {settings.mail_host}:{settings.mail_port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
