# Gmail setup

LifeLink sends mail over authenticated SMTP with TLS. For Gmail:

1. Turn on 2-Step Verification on the sending Google account.
2. Create an **App Password** (Google Account → Security → App passwords).
3. Put it in your secret store / `.env` — never in source, never in
   `.env.example`, never in logs:

```bash
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_STARTTLS=true
MAIL_USERNAME=your.address@gmail.com
MAIL_PASSWORD=<16-character app password>
MAIL_FROM=your.address@gmail.com
```

Safety rails already in the code: credentials are refused over a plain
connection (config validation), TLS uses a verifying context, the password
is a `SecretStr` and never logged (`mail_summary()` prints provider, host,
port, TLS mode and whether auth is on), and Docker Compose only falls back
to Mailpit when `MAIL_HOST` is unset (`tests/test_gap_infrastructure.py::
test_gmail_is_never_silently_replaced_by_mailpit`).

Smoke test (manual, with real credentials):

```bash
cd backend && python -m scripts.send_test_email you@example.com
```

## What you must provide (exactly)

| Credential | Env var | Format | Where |
|---|---|---|---|
| Sending Gmail address | `MAIL_USERNAME` and `MAIL_FROM` | `name@gmail.com` (or a Workspace address) | `backend/.env` |
| Google **App Password** (not the account password) | `MAIL_PASSWORD` | 16 letters. Google shows it in four groups; the spaces may be kept or removed | `backend/.env` |
| Transport | `MAIL_HOST=smtp.gmail.com`, `MAIL_PORT=587`, `MAIL_STARTTLS=true`, `MAIL_USE_SSL=false` | as shown | `backend/.env` |

Notes:

* `backend/.env` is git-ignored. Never put these values in `.env.example`, the compose file, source code or a commit.
* With Docker, set the same keys in `backend/.env`. Compose falls back to Mailpit only when `MAIL_HOST` is unset, so it never overrides Gmail.

**Check without exposing the password:**

```bash
cd backend
python -c "from app.core.config import settings; print(settings.mail_summary())"
#   -> provider=gmail host=smtp.gmail.com port=587 tls=starttls auth=on   (no password printed)
python -m scripts.send_test_email you@example.com   # ONE message; exit 0 = accepted by smtp.gmail.com
```

Then confirm the message arrived in the recipient's inbox. "Accepted" proves only the SMTP transaction.

Run this from a network that allows outbound TCP to `smtp.gmail.com:587`.

## Status

**Final external validation, 2026-10-13: BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE.** Credentials were also not provided.

* No `MAIL_USERNAME` or `MAIL_PASSWORD` (or any GMAIL_* variable) exists in this environment. `scripts/send_test_email.py` refused with a configuration error (`evidence/external/gmail/send_test_email_attempt.txt`).
* Direct TCP to smtp.gmail.com fails (no DNS). Through the egress proxy, CONNECT to :587 and :465 is accepted, but no SMTP greeting ever arrives (`evidence/external/gmail/smtp_reachability.txt`).

No Gmail delivery is claimed. The SMTP code path itself is verified end to end against real Mailpit (`evidence/external/mailpit/`).

Earlier status (before 2026-10-13): BLOCKED / NOT TESTED for the same reasons (`evidence/egress_probe.txt`).
