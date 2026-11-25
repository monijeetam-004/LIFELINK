# LifeLink — Email (Mailpit and Gmail)

The API and the RQ worker read the **same** `MAIL_*` variables and both log, at start-up:

```
mail transport: <provider> <host>:<port> <plain|starttls|ssl> <auth|no-auth>
```

(never the password). `GET /health/ready` reports `optional.email.provider`.

## Mode A — Mailpit (development; nothing leaves the machine)

| Where | Settings |
|---|---|
| docker compose | `.env` as in `.env.example`: `COMPOSE_PROFILES=mailpit`, `MAIL_HOST` unset → the API/worker use the `mailpit` container. UI: http://localhost:8025 |
| API run directly | `MAIL_HOST` unset (defaults to `localhost`), Mailpit on `localhost:1025` |

`MAIL_PORT=1025`, `MAIL_STARTTLS=false`, no credentials.

## Mode B — real Gmail SMTP

```
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_STARTTLS=true          # or MAIL_PORT=465 + MAIL_USE_SSL=true
MAIL_USERNAME=<your Gmail address>
MAIL_PASSWORD=<16-character Google App Password>
MAIL_FROM=<the same Gmail address>
```

Put these only in your local, git-ignored `.env` or your deployment's secret store. Remove `COMPOSE_PROFILES=mailpit` if you don't also want Mailpit. Compose no longer forces `MAIL_HOST=mailpit` (it used to, silently overriding Gmail); evidence: `docs/QA/evidence/compose_mail_modes.txt`.

Check delivery: `python -m scripts.send_test_email you@example.com` (exit 0 = accepted by the SMTP server; then check the inbox).

## What the application enforces

- Refuses to start with Gmail selected but no credentials, credentials without TLS, a wrong port/TLS pair, or `MAIL_STARTTLS` and `MAIL_USE_SSL` together.
- TLS verifies the server certificate and hostname. Before this QA round, STARTTLS ran without verification, so the App Password could have been read by a network attacker.
- `MAIL_PASSWORD` is a secret value: it never appears in `repr`, logs or start-up errors. Before this QA round, a refused configuration printed every input value, including the password and `SECRET_KEY`.
- Worker failures go to RQ's failed-job registry after `EMAIL_JOB_MAX_RETRIES` retries; `0` now means one attempt. Before, it crashed registration with HTTP 500.

## Verified here

- The full path: registration → queue → RQ worker → SMTP → email received → link → account verified. Tested with a real local SMTP server (`tests/test_qa_email.py`) and in the browser with MailDev standing in for Mailpit (screenshots W05–W06).
- STARTTLS with authentication against a Gmail-shaped local server; certificate verification; a wrong App Password; SMTP down; worker down.

## Not verified here

Live delivery through Gmail is **BLOCKED**: there are no credentials in this environment, and `smtp.gmail.com:587` is unreachable (connection times out); evidence in `docs/QA/evidence/gmail_attempt.txt`. To verify it yourself: set Mode B in `.env`, then run `python -m scripts.send_test_email <your address>` and register a test account.
