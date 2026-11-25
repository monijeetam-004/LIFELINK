# LifeLink SMS Provider — Technical Architecture

**Read `LIFELINK_SMS_STATUS.md` first.** That document is the plain-language
statement of what's actually true today: SMS is disabled, nothing depends
on it, and no message has ever been sent. This document is the technical
detail behind that statement, for anyone maintaining or extending the code.

## Architecture

```
notification_service.notify()
       |
       +---- Notification row              -- always created (in-app, guaranteed)
       |
       +---- _maybe_send_email()            -- if email configured/enabled
       |          |
       |          +---- email_service.py    -- pre-existing, unchanged
       |
       +---- _maybe_send_sms()              -- if SMS configured/enabled (OFF by default)
                  |
                  +---- sms_service.py       -- orchestration: idempotency, audit, gating
                             |
                             +---- SmsProvider (app/services/sms/base.py)
                                        |
                                        +---- MSG91Provider (app/services/sms/msg91.py)
```

Business/domain code (`app/api/routes/blood_requests.py`,
`app/api/routes/emergencies.py`, `app/api/routes/family.py`, etc.) only
ever calls `notification_service.notify(...)`. None of it imports or
references SMS, MSG91, or any provider directly — the channel decision
(in-app always, email if configured, SMS if configured) lives entirely
inside `notify()` and the functions it calls. This is what makes SMS safe
to leave off: nothing in the request-handling code path for creating an
emergency, matching a donor, or any other flow changes behavior based on
whether SMS exists.

`SmsProvider` (`app/services/sms/base.py`) is an abstract interface with
one method, `send_sms(to_e164, template_key, variables) -> SmsSendResult`.
`MSG91Provider` is the only implementation today. Adding a second provider,
or swapping MSG91 for something else, means writing one new class and
pointing `app.services.sms.get_sms_provider()` at it — nothing else in the
codebase changes.

## The master switch: `MSG91_ENABLED`

`_maybe_send_sms()` in `app/services/notification_service.py` checks
`settings.msg91_enabled` as its very first line. When false (the default,
and the state in every environment this project ships in — local dev,
tests, and however it's deployed for submission/demo), the function
returns immediately: no database query, no import of the `app.services.sms`
package, no network call, no audit row written. SMS being off costs
nothing and touches nothing.

```python
def _maybe_send_sms(db, user_id, pref, *, type, notification_id, sms_variables):
    if not settings.msg91_enabled:
        return
    ...
```

When `MSG91_ENABLED=true` (not the case in this deployment), the function
goes on to check, in order: does the user's `NotificationPreference` have
`sms_enabled=True` (default `False` — opt-in, not opt-out); is this a
quiet-hours-suppressible event (same rule as email: emergency alerts are
never suppressed); then delegates to `sms_service.send_for_event(...)`.

## Configuration (`Settings.sms_provider_configured`)

```python
@property
def sms_provider_configured(self) -> bool:
    return bool(self.msg91_enabled and self.msg91_auth_key and self.msg91_sender_id)
```

Mirrors the existing `neo4j_configured` / `storage_backend()` pattern
elsewhere in this codebase: a plain "is it set up" check, not a
reachability probe. It deliberately does not require `msg91_flow_id` —
a deployment can be legitimately configured with only per-template Flow ID
overrides (`MSG91_TEMPLATE_FLOW_MAP`) and no single global default; whether
a *specific* template is actually sendable is resolved per-attempt.

`MSG91Provider.__init__()` raises `SmsProviderUnavailableError` if
`sms_provider_configured` is false — constructing the provider fails fast
rather than deferring the failure to the first send. `get_sms_provider()`
(`app/services/sms/__init__.py`) is the one place that's caught, exactly
like `GraphUnavailableError` is caught around Neo4j calls.

## Templates (`app/services/sms/templates.py`)

LifeLink never sends free-text SMS. Every send goes through one of seven
fixed templates, each with a required-variables list validated *before*
any network call:

| Template key | Backing event(s) (real, already in the codebase) |
|---|---|
| `EMERGENCY_CREATED` | `emergency.new` |
| `EMERGENCY_STATUS_CHANGED` | `emergency.acknowledged`, `.responder_accepted`, `.responder_withdrew`, `.assistance_found`, `.resolved`, `.cancelled` |
| `DONOR_MATCH_ALERT` | `blood_request.new` |
| `HOSPITAL_REQUEST_UPDATE` | `blood_request.incoming` |
| `FAMILY_FILED_REQUEST` | `blood_request.filed_for_you` |
| `BLOOD_REQUEST_FULFILLED` | `blood_request.fulfilled` |
| `BLOOD_REQUEST_CANCELLED` | `blood_request.cancelled` |

Deliberately **not** wired to SMS: self-echo confirmations the recipient is
already looking at in-app when they happen (`blood_request.you_accepted`,
`emergency.you_responded`), and every non-blood-donation event type
(`family.*`, `certificate.ready`, `appointment.*`, `verification.*`,
`account.password_changed`). Also deliberately **not** implemented at all:
an SMS-OTP authentication flow (LifeLink's auth uses email verification and
email-based password reset; there's no existing SMS-OTP mechanism to wire
into) and an automatic "critical request escalation" timer (no such
escalation mechanism exists anywhere in this codebase to hang an SMS off
of). Both were example categories in an earlier draft of the SMS spec, and
both were left out rather than backed by something fabricated.

Each template's `reference_text` in `templates.py` is documentation, not
what's actually sent — the real, DLT-approved wording for a production
deployment lives on MSG91's side, addressed by a Flow ID
(`flow_id_for(template_key)`, with an optional `MSG91_TEMPLATE_FLOW_MAP`
per-template override falling back to `MSG91_FLOW_ID`).

## Provider: MSG91 (`app/services/sms/msg91.py`)

Uses MSG91's Flow API v5 (`POST https://control.msg91.com/api/v5/flow/`),
which sends a message built from a pre-approved DLT template plus named
variables — the API MSG91 documents for DLT-compliant Indian transactional
SMS. `httpx.Client` is constructed fresh per call with
`settings.msg91_timeout_seconds` (default 10s); a `transport` constructor
parameter exists purely as a testing seam (`tests/test_sms.py` uses
`httpx.MockTransport` — no test in this repository ever makes a real
network call or could send a real SMS).

Response classification (`_classify_response`):

| Condition | `SmsFailureCategory` |
|---|---|
| Network error / timeout | `TIMEOUT` |
| HTTP 401/403 | `AUTH_FAILURE` |
| HTTP 429 | `RATE_LIMITED` |
| HTTP 5xx | `PROVIDER_5XX` |
| HTTP 4xx, message mentions mobile/recipient/number | `INVALID_RECIPIENT` |
| HTTP 4xx, message mentions template/flow/dlt | `INVALID_TEMPLATE` |
| HTTP 2xx but body `type: "error"` | `INVALID_RECIPIENT` / `INVALID_TEMPLATE` / `UNKNOWN`, by message content |
| 2xx with `type: "success"` | accepted, `provider_message_id` captured when present |

`SMS_RETRYABLE_CATEGORIES` = `{TIMEOUT, PROVIDER_5XX, RATE_LIMITED}`. This
categorization exists so a future retry policy has something correct to
act on — see "Known limitations" below for why no retry is actually
scheduled today.

## Idempotency and the audit trail (`SmsNotification`)

Every SMS attempt (successful or not) is recorded on `SmsNotification`
(`app/models/sms_notification.py`), one row per in-app `Notification` —
enforced by `uq_sms_notification_per_notification`. `sms_service.send_for_event()`
looks up the existing row by `notification_id` first: if it's already
`SENT`, the function returns it unchanged without calling the provider
again. This makes a retried/duplicated call (a retried API request, a
worker restart, calling `notify()` twice for the same logical event)
naturally idempotent rather than needing a separate deduplication layer.

Fields recorded: `event_type`, `template_key`, `recipient_phone_masked`
(never the full number — see below), `channel`, `provider`, `status`
(`PENDING`/`SENT`/`FAILED`), `sent_at`/`failed_at`, `retry_count`,
`provider_message_id`, `failure_category`, `failure_detail` (truncated to
300 chars), and `related_type`/`related_id`. Never recorded: provider
credentials, the SMS body text, or the recipient's un-masked phone number.

## Phone numbers (`app/core/phone.py`)

Real validation via Google's `phonenumbers` library (a port of
libphonenumber), not a hand-rolled regex. `normalize_phone_e164()` parses
and normalizes to E.164 (e.g. `+919876543210`); malformed input raises and
is surfaced as a 422 by the Patient/Donor/Hospital profile-update schemas
(`app/schemas/profile.py`, `app/schemas/hospital.py`), which all call it
via a shared Pydantic field validator. `mask_phone()` keeps the country
code and last two digits for the audit trail
(`+919876543210` → `+91*******10`).

This validation is genuinely useful independent of SMS — it's the same
phone-number field patients, donors, and hospitals already fill in for
contact purposes, now actually validated instead of accepting arbitrary
text. `resolve_user_phone()` (`app/services/domain_service.py`) is the
one place that knows phone numbers live on the role-specific profile
table (`PatientProfile`/`DonorProfile`/`HospitalProfile`), not on `User`
itself — FAMILY and ADMIN users have no phone field in this schema at all
and honestly return `None` rather than guessing.

## Admin visibility (not user-facing)

`GET /admin/system/health` includes an `sms` component
(`configured`/`status`/`provider`/`recent_failures`, via
`sms_service.sms_health()` and `recent_failure_count()`), rendered in the
Admin Overview page next to the existing DB/Redis/Storage/Graph rows. This
is deliberately never a live reachability probe — MSG91 has no ping
endpoint, and sending a real SMS just to populate a dashboard would be its
own kind of dishonesty — so `status="configured"` means "credentials are
set", not "we just verified delivery works." `configured=false` (this
deployment's actual state) renders neutral, the same treatment already
given to the graph engine's "not configured" state, never as an error.

This is intentionally admin-only. No equivalent control or status
indicator is shown to a normal user — see `LIFELINK_SMS_STATUS.md` for why.

## Environment variables

See `.env.example` for the full, commented list
(`MSG91_ENABLED`, `MSG91_AUTH_KEY`, `MSG91_FLOW_ID`, `MSG91_SENDER_ID`,
`MSG91_DLT_TEMPLATE_ID`, `MSG91_TIMEOUT_SECONDS`, `MSG91_TEMPLATE_FLOW_MAP`,
`SMS_DEFAULT_REGION`). All are optional; every one is unset or `false` in
this project's shipped configuration, and none is required for local dev,
tests, the frontend, in-app notifications, or emergency workflows.

## Database

Migration `f3a4b5c6d7e8_sms_notifications.py` adds the `sms_notifications`
table and three new Postgres enum types (`sms_status`,
`sms_failure_category`, `sms_template_key`). It does not touch any
existing table. `NotificationPreference.sms_enabled` (added in an earlier
phase) is unchanged and kept — see "NotificationPreference.sms_enabled"
below.

## `NotificationPreference.sms_enabled`

This column pre-dates the current SMS work (added when notification
preferences were first built) and was already being read/written by the
API and covered by `tests/test_notification_preferences.py`. Rather than
delete a real, tested, migrated database column, it's kept as an internal
capability flag: a per-user opt-in bit that only has any effect at all once
`MSG91_ENABLED=true` (see `_maybe_send_sms`'s gating above). It is not
exposed as a control on the Settings page — see `LIFELINK_SMS_STATUS.md`.
The API (`GET`/`PUT /notifications/preferences`) still returns and accepts
it, since removing it from the API would be a breaking, untested change for
no honesty benefit (the field is a stored boolean, not a delivery claim).

## Known limitations (stated plainly)

- **No automatic retry/backoff is implemented.** `SmsFailureCategory` and
  `SMS_RETRYABLE_CATEGORIES` classify failures correctly, and
  `SmsNotification.retry_count` exists, but nothing currently re-enqueues a
  retryable failure. A real production rollout would add scheduled retries
  (an RQ job on the reserved `sms` queue — see `app/workers/queue.py` — is
  the natural place) rather than the single synchronous attempt made today.
- **Template variable sourcing is not wired at every call site.**
  `notify()` accepts an optional `sms_variables` parameter as the
  extension point, but none of the ~13 call sites across
  `blood_requests.py`/`emergencies.py` currently populate it. Enabling SMS
  today would mean every attempt for a template with required variables
  (all but `BLOOD_REQUEST_FULFILLED`/`BLOOD_REQUEST_CANCELLED`) fails
  validation and is recorded honestly as `INVALID_TEMPLATE` — never a
  crash, never a wrongly-worded send, never a silent failure — but also
  not yet a working send. Wiring real values through is a small, mechanical
  follow-up once a specific deployment is ready to configure MSG91.
- **No live reachability probe.** Admin health reports configuration state
  and recent send history, not a real-time "is MSG91 up" check (see
  "Admin visibility" above for why).
- **DLT registration is entirely a manual, provider-side/regulatory step**
  this codebase cannot automate or verify. `MSG91_DLT_TEMPLATE_ID` is
  accepted as configuration but nothing here confirms a template is
  actually DLT-approved before attempting to send it — MSG91 itself
  rejects an unapproved template send, which is classified as
  `INVALID_TEMPLATE` per the response-classification table above.

## Manual live-test procedure (not run in this project)

This procedure is documented for a future deployment that actually wants
to verify real delivery. It has **not** been performed for this project —
see `LIFELINK_SMS_STATUS.md`.

1. Create a real MSG91 account and generate an auth key.
2. Register a sender ID and complete DLT template registration for at
   least one of the templates in `app/services/sms/templates.py`
   (India-specific regulatory requirement).
3. Set `MSG91_ENABLED=true`, `MSG91_AUTH_KEY`, `MSG91_SENDER_ID`, and
   either `MSG91_FLOW_ID` or a `MSG91_TEMPLATE_FLOW_MAP` entry for the
   template being tested, in a local `.env` (never commit these).
4. Wire real `sms_variables` into the one call site for the template
   being tested (see "Known limitations" above).
5. Start the backend with that configuration.
6. As a test account, `PUT /notifications/preferences` with
   `{"sms_enabled": true}`, and ensure the account's role-specific profile
   has a real, reachable phone number set (E.164 will be enforced
   automatically).
7. Trigger the real event (e.g. create a blood request that matches the
   test donor).
8. Verify the SMS actually arrives on the test phone.
9. Query the `sms_notifications` table (or `GET /admin/system/health`) and
   confirm the `SmsNotification` row shows `status=SENT` with a
   `provider_message_id`.
10. Confirm no credentials appear in application logs.
11. Deliberately misconfigure something (wrong auth key, wrong flow id) and
    confirm the failure is classified correctly and recorded, not silently
    swallowed or reported as success.

## Tests

`backend/tests/test_sms.py` (37 tests) covers phone normalization/masking,
MSG91 response classification for every category in the table above (via
`httpx.MockTransport` — no real network calls), idempotency, the
`MSG91_ENABLED=false` no-op guarantee, opt-in gating, and an end-to-end
emergency-creation test proving the real API still works with SMS
disabled. `tests/test_admin.py::test_system_health_reports_real_component_status`
was extended to assert the `sms` health component reads
`configured=False`/`status="disabled"` in this deployment's actual
configuration.
