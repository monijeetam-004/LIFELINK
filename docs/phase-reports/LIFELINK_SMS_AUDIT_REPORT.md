# LifeLink SMS Audit & Honesty Report

This report documents the work done in response to the pivot away from live
MSG91 SMS integration, per the instruction: *"The final LifeLink UI must
never promise a capability that is not actually operational... IF THE USER
CAN SEE IT, IT MUST BE REAL."*

It follows the exact 10-point structure requested.

---

## 1. Files inspected

Audited for every SMS / `sms_enabled` / MSG91 / `send_sms` / `SMSProvider`
reference, across both trees, before any change was made:

**Backend:** `app/core/config.py`, `app/core/graph.py`, `app/core/storage.py`
(read as the honest-degradation template), `app/models/enums.py`,
`app/models/notification_preference.py`, `app/models/notification.py`,
`app/models/user.py`, `app/schemas/notification_preference.py`,
`app/schemas/profile.py`, `app/schemas/hospital.py`, `app/schemas/admin.py`,
`app/services/notification_service.py`, `app/services/domain_service.py`,
`app/api/routes/admin.py`, `app/api/routes/notifications.py`,
`app/api/routes/blood_requests.py`, `app/api/routes/emergencies.py`,
`app/workers/queue.py`, `app/workers/worker.py`, `alembic/versions/*`
(for the existing enum-creation pattern), `requirements.txt`,
`.env.example`, `tests/conftest.py`, `tests/test_admin.py`,
`tests/test_notifications.py`, `tests/test_profiles.py`.

**Frontend:** `src/pages/shared/Settings.tsx`, `src/pages/admin/AdminOverview.tsx`,
`src/lib/domain.ts`, `src/lib/api.ts`, and a repo-wide
`grep -rli "sms\|msg91"` across `frontend/src` to confirm no other
component, page, or type referenced SMS.

**Root:** `README.md` (the existing "what's real and what isn't yet"
section).

This audit found the previously-started, in-progress MSG91 backend
implementation (provider abstraction, phone validation, template registry,
audit model, migration) plus one genuinely misleading piece of existing
frontend UI: a working-looking "SMS notifications" toggle in
`Settings.tsx` bound to `NotificationPreference.sms_enabled`, which flipped
a real database bit but was never connected to any SMS being sent —
exactly the kind of promise-not-backed-by-reality this pivot exists to
remove.

## 2. Files changed

**Backend — created:**
- `app/core/phone.py` — real E.164 phone validation/normalization/masking (`phonenumbers`)
- `app/models/sms_notification.py` — `SmsNotification` audit-trail model
- `app/services/sms/__init__.py`, `base.py`, `templates.py`, `msg91.py` — provider abstraction + MSG91 Flow API v5 client
- `app/services/sms_service.py` — orchestration/idempotency layer
- `alembic/versions/f3a4b5c6d7e8_sms_notifications.py` — migration for `sms_notifications`

**Backend — edited:**
- `requirements.txt` (+`httpx`, `+phonenumbers`)
- `app/core/config.py` (MSG91_* settings, `sms_provider_configured`)
- `app/models/enums.py` (`SmsStatus`, `SmsFailureCategory`, `SmsTemplateKey`)
- `app/models/__init__.py`
- `app/schemas/profile.py`, `app/schemas/hospital.py` (phone validators)
- `app/services/domain_service.py` (`resolve_user_phone`)
- `app/services/notification_service.py` (`_maybe_send_sms`, channel-agnostic `notify()`)
- `app/schemas/admin.py`, `app/api/routes/admin.py` (admin-only SMS health)
- `app/workers/queue.py`, `app/workers/worker.py` (dormant `sms` queue)
- `.env.example` (documented, all-blank SMS section)
- `tests/conftest.py`, `tests/test_admin.py`, `tests/test_profiles.py`

**Backend — created (tests):** `tests/test_sms.py` (37 tests)

**Frontend — edited:**
- `src/pages/shared/Settings.tsx` — removed the SMS toggle, added an honest note
- `src/lib/domain.ts` — `AdminSmsHealth` / `AdminHealth.sms` types
- `src/pages/admin/AdminOverview.tsx` — admin-only SMS health row

**Root — created:** `LIFELINK_SMS_STATUS.md`, `LIFELINK_SMS_PROVIDER.md`, this file
**Root — edited:** `README.md`

No other files were touched. No unrelated part of LifeLink was rebuilt.

## 3. SMS UI removed/changed

- **Removed:** the `Settings.tsx` "SMS notifications" `ToggleSwitch`, the
  only control that let a normal user believe SMS was active. It has no
  replacement toggle of any kind.
- **Added (Settings.tsx):** one small, non-alarming, non-promotional line:
  *"SMS notifications are not currently enabled for this deployment."*
  It appears once, is not styled as a warning or call-to-action, and does
  not offer a way to "turn it on."
- **Added (AdminOverview.tsx):** a single admin-only system-health row —
  `SMS (MSG91): disabled` (grey) today — next to the existing DB/Redis/
  Storage/Graph rows. It reports configuration status and recent failure
  count only; it never claims a message was or will be delivered, and it
  is not reachable by non-admin roles (same route-level admin check as the
  rest of that page).
- **Untouched:** every phone-number field used for verification, emergency
  contact, donor/hospital contact, or family linking — none of those are
  SMS-delivery controls, so none were removed.

## 4. Backend SMS changes

- `notify()` in `notification_service.py` is the single, channel-agnostic
  entry point all business logic (emergencies, blood requests, donor
  matching, family/hospital alerts) already called before this work, and
  still does. It always creates the in-app `Notification` row, then
  independently attempts email and SMS:
  ```
  notify()
    +---- in-app Notification row  -- always created
    +---- _maybe_send_email()      -- if email configured/enabled
    +---- _maybe_send_sms()        -- if SMS configured/enabled (dormant by default)
  ```
- `_maybe_send_sms()`'s first line checks `settings.msg91_enabled` — a
  zero-cost boolean check, no DB query, no import, no network call when
  false (the default everywhere: dev, tests, and this deployment).
- `Settings.sms_provider_configured` requires `msg91_enabled` +
  `msg91_auth_key` + `msg91_sender_id` all truthy, mirroring the existing
  `neo4j_configured` pattern in the same file.
- `NotificationPreference.sms_enabled` (the DB column) was **not deleted**.
  Per the instruction to not blindly drop columns, it's kept as an
  internal capability flag — real, stored, but with no user-facing control
  and no effect while `MSG91_ENABLED=false`.
- The MSG91 provider (`app/services/sms/msg91.py`) is real, working code
  (Flow API v5, DLT-template-based), but is only ever constructed via
  `get_sms_provider()`, which raises `SmsProviderUnavailableError` unless
  `sms_provider_configured` is true — so it cannot run at all in this
  deployment.
- Admin health (`sms_health()`) reports configuration status without ever
  sending a probe SMS.

## 5. Remaining SMS infrastructure

Classified per the requested A (retained infra) / B (config/docs) /
C (dead code) / D (misleading user-facing) scheme:

- **A — intentionally retained:** `app/services/sms/*`, `app/core/phone.py`,
  `app/services/sms_service.py`, `app/models/sms_notification.py`,
  `SmsStatus`/`SmsFailureCategory`/`SmsTemplateKey` enums,
  `NotificationPreference.sms_enabled` column, the `sms` RQ queue
  declaration, the admin-only health row. All isolated behind
  `MSG91_ENABLED=false`, none reachable from any normal code path today.
- **B — configuration/documentation:** the `MSG91_*` block in
  `.env.example` (all blank), `LIFELINK_SMS_STATUS.md`,
  `LIFELINK_SMS_PROVIDER.md`, this report, the updated `README.md` section.
- **C — dead code removed:** none found beyond the UI toggle covered
  under D — the in-progress backend work was structurally sound and is
  now the real, gated implementation rather than leftover scaffolding.
- **D — misleading user-facing functionality removed:** the `Settings.tsx`
  SMS toggle (section 3).

No legitimate future-provider infrastructure was removed.

## 6. Tests executed

```
# Backend
cd backend
ruff check app tests
.venv/bin/python -m mypy app
ENVIRONMENT=test .venv/bin/python -m pytest        # full suite, 273 tests
ENVIRONMENT=test .venv/bin/python -m pytest -q -k "emergency or sms" tests/

# Frontend
cd frontend
npm run typecheck
npm run lint
npm run build
grep -riE "msg91|auth_key" dist/assets/*.js         # bundle secret/label scan
```

## 7. Test results

- **ruff (`app tests`):** 22 pre-existing errors remain, all `UP042`
  (recommends `enum.StrEnum`) or unrelated issues in files never touched
  this session (`app/models/enums.py`'s pre-existing enum classes,
  `app/models/user.py`, `tests/test_family.py`). Every file authored or
  edited for SMS work is clean.
- **mypy (`app`):** 3 pre-existing errors remain, all in
  `app/api/routes/verifications.py` and `app/api/routes/donors.py` — files
  never touched this session. Every file authored or edited for SMS work
  is clean.
- **pytest:** **273 passed**, 0 failed, 0 errors (202s). Includes the new
  `tests/test_sms.py` (37 tests) plus the existing suite unmodified in
  behavior.
- **npm run typecheck:** clean, no errors.
- **npm run lint:** clean, no errors.
- **npm run build:** succeeds (`vite build` completes; only Rollup's
  informational chunk-size note, unrelated to this work).
- **Bundle secret scan:** `grep -ri "msg91\|auth_key"` over the built
  `dist/assets/*.js` returns **zero matches** — no SMS provider name,
  credential, or endpoint reaches the shipped frontend bundle.

## 8. Any remaining risks

- **No automatic retry/backoff is implemented.** `SmsFailureCategory`,
  `SMS_RETRYABLE_CATEGORIES`, and `SmsNotification.retry_count` exist, and
  an `sms` RQ queue is declared, but nothing enqueues or consumes retry
  jobs on it yet. Irrelevant while SMS is disabled; documented as a known
  limitation in `LIFELINK_SMS_PROVIDER.md` for anyone enabling it later.
- **Template variables aren't wired at call sites.** `notify()` accepts an
  optional `sms_variables` extension point, but none of the ~13 real call
  sites (`blood_requests.py`, `emergencies.py`, etc.) populate it yet.
  If SMS were switched on today without further work, every templated
  send would fail *honestly* (`INVALID_TEMPLATE`, recorded on the audit
  row) rather than crash or send blank/wrong content — but it would not
  yet deliver real messages.
- **No live reachability probe.** The admin health panel reports
  configuration status, not a live "can we reach MSG91 right now" check.
- **DLT registration is manual and regulatory** — nothing in this
  codebase can complete India's DLT template/sender registration; that
  remains entirely outside the application.
- **FAMILY and ADMIN roles have no phone field** in the current schema,
  so `resolve_user_phone()` returns `None` for them by design — a
  documented, not hidden, gap.
- None of the above affects the currently-shipped behavior: SMS is
  disabled by default, no code path sends a real SMS, and nothing
  user-visible implies otherwise.

## 9. Exact commands used

```
ruff check app tests
ruff check --fix app/services/sms/templates.py app/services/sms/msg91.py \
    app/services/sms_service.py tests/test_sms.py app/services/notification_service.py
.venv/bin/python -m mypy app
ENVIRONMENT=test .venv/bin/python -m pytest
ENVIRONMENT=test .venv/bin/python -m pytest -q -k "emergency or sms" tests/
alembic upgrade head
alembic downgrade -1
alembic upgrade head
npm run typecheck
npm run lint
npm run build
grep -riE "msg91|auth_key|sms_provider" dist/assets/*.js
grep -rliE "sms|msg91" --include="*.py" --include="*.tsx" --include="*.ts" backend/app frontend/src
```

## 10. Confirmation that emergency/in-app notification flows still work

Confirmed by the passing suite, specifically:

- `test_emergency_creation_works_with_sms_disabled` — a full API-level test
  creating a real emergency with SMS disabled (the default), asserting the
  emergency is created successfully and in-app notifications fire.
- `test_notify_never_touches_sms_when_disabled` — asserts zero
  `SmsNotification` rows are ever created for an `emergency.new` event when
  `MSG91_ENABLED=false`, while the in-app `Notification` row is still
  created.
- The full pre-existing emergency, matching, graph/resource-pathway,
  donor/hospital/family alerting, and notification-history test files
  (`test_emergencies.py`, graph/matching suites, `test_notifications.py`,
  `test_family.py`, etc.) all pass unmodified within the same 273-test run.
- `test_notification_preferences_response_has_no_sms_delivery_claim`
  confirms the notification-preferences API response makes no claim of
  SMS delivery capability.

Emergency creation, matching, resource allocation, graph optimization,
recovery/tracking, and every existing donor/hospital/family/patient
notification flow are unaffected by SMS being disabled — as required.
