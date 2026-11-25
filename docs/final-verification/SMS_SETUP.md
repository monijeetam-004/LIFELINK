# SMS setup

Architecture: `notification_service._maybe_send_sms` → `sms_service.send_for_event`
→ provider (`app/services/sms/`). Per-user consent (`sms_enabled`, default
OFF), quiet hours for non-emergency events, emergency opt-out and an hourly
emergency-alert cap, DLT templates with required variables, one audited
`SmsNotification` row per notification (masked number, provider, status,
failure category, retry count).

Providers (`SMS_PROVIDER`):

| value | behaviour |
|---|---|
| `msg91` (default) | real MSG91 Flow API; needs `MSG91_ENABLED=true`, `MSG91_AUTH_KEY`, `MSG91_SENDER_ID`, a Flow ID (global or per template) |
| `console` | development only: the message is written to the log and recorded as **LOGGED_ONLY** — never SENT. Refused in production. |
| `none` | SMS off |

Retries: TIMEOUT / PROVIDER_5XX / RATE_LIMITED failures are re-attempted
automatically through the RQ scheduler with exponential backoff
(`SMS_RETRY_BASE_SECONDS * 2^n`, up to `SMS_MAX_RETRIES`); the row stays
PENDING while a retry is scheduled, FAILED when retries are exhausted.
Permanent failures (bad number, auth, template) are not retried.

Tests: `tests/test_sms.py` (MSG91 request/response handling through an
`httpx.MockTransport` -- the HTTP exchange is simulated, no real network), `tests/test_gap_features_api.py` (console provider never claims
delivery and is refused in production; retry scheduling, exhaustion and
permanent-failure handling).

## What you must provide (exactly)

| Credential | Env var | Format |
|---|---|---|
| Provider selection | `SMS_PROVIDER=msg91`, `MSG91_ENABLED=true` | as shown |
| MSG91 auth key | `MSG91_AUTH_KEY` | the key from MSG91 → API, an opaque string |
| DLT-registered sender ID | `MSG91_SENDER_ID` | 6 letters, e.g. `LFLINK` |
| Flow (template) ID | `MSG91_FLOW_ID`, or `MSG91_TEMPLATE_FLOW_MAP` with an `EMERGENCY_CREATED` entry | the Flow ID shown in MSG91's Flow builder |
| DLT template ID | `MSG91_DLT_TEMPLATE_ID` | the 19-digit DLT template ID (India) |

All of these go in `backend/.env`, which is git-ignored. Never put them in `.env.example`, source code or a commit.

**One controlled test** (sends exactly one SMS; prints the number masked, never the key):

```bash
cd backend
python -m scripts.send_test_sms +91XXXXXXXXXX
#   exit 0 = ACCEPTED by MSG91 (then confirm on the handset or in MSG91's delivery report)
#   exit 1 = REJECTED (failure category printed); exit 2 = not configured / console provider
```

## Status

**Final external validation, 2026-10-13: BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED.**

* No MSG91_* variables exist here, so `msg91_enabled=False` and `sms_provider_configured=False`.
* control.msg91.com and api.msg91.com are unreachable.
* `send_test_sms` refused in both configurations (exit 2).
* Evidence: `evidence/external/sms/msg91_attempt.txt`.

**No real SMS was sent, and none is claimed.**
