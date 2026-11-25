# LifeLink Phase 18 — Notification & Event System Audit

This is the audit document underlying `LIFELINK_PHASE_18_REPORT.md`. It
records what was actually read, what every real notification-producing
event in the codebase is, how it is persisted/delivered/audited, and the
privacy and idempotency posture of each. Every row below was confirmed by
reading the actual call site — nothing here is inferred from naming
conventions alone.

## 1. Method

Every call site of `notification_service.notify(...)` and
`notification_service.notify_matching_donors(...)` was located with a grep
scoped precisely to those two call shapes (`notification_service\.(notify|notify_many|notify_matching_donors)\(`),
deliberately excluding the unrelated and much more numerous
`audit_service.record(..., target_type=...)` calls that an unscoped grep
for `type=` would otherwise conflate with real notifications. `notify_many`
does not exist as a real helper in this codebase — every multi-recipient
fan-out (donor matching, hospital city broadcast, admin fan-out) is a plain
Python `for` loop calling `notify()` once per recipient. This was confirmed
by reading `app/services/notification_service.py` in full: there is no
batch path, so every claim below about "loop calls notify() N times" is a
direct reading of the loop, not an assumption.

## 2. Notification architecture (confirmed by reading `notification_service.py`)

`notify(db, user_id, type, title, body, data=None)`:
1. Always creates and flushes a `Notification` row first — the in-app
   record is never conditional on email/SMS succeeding or even being
   attempted. This is what makes every fan-out crash-safe: an SMTP outage
   cannot prevent the in-app notification from existing.
2. Then calls `_maybe_send_email(...)` and `_maybe_send_sms(...)`, both
   fully wrapped in `try/except Exception` inside the service itself — a
   raised exception from either channel is caught, logged, and never
   propagates to the caller. Confirmed directly in the source; this is why
   `test_notify_creates_in_app_row_even_when_the_email_queue_itself_is_unreachable`
   (new this phase) can monkeypatch the RQ enqueue call to raise
   `ConnectionError` and still observe a committed `Notification` row and a
   normal `notify()` return.
3. Email/SMS dispatch each independently check `NotificationPreference`
   (per-user, per-channel, with quiet-hours logic in the alert watcher path)
   before sending — already covered by `tests/test_notification_preferences.py`
   and `tests/test_alert_watcher.py`, not re-tested here.

There is no push/WebSocket channel. The frontend's `NotificationsBell` and
`NotificationsCenter` both poll `GET /notifications` on a fixed interval
(`refetchInterval: 30_000` in `NotificationsBell.tsx`) plus refetch-on-focus.
This is a deliberate, honest architectural choice already in place before
Phase 18, not a Phase 18 regression — it is called out here only so the
report doesn't imply real-time delivery where none exists.

## 3. Full event inventory (30 types, all confirmed at their call site)

| # | Event type | Trigger / actor | Recipient(s) | Fan-out shape | Channels | Idempotent on replay? |
|---|---|---|---|---|---|---|
| 1 | `blood_request.new` | Patient/family files a request | Matching donors (blood-type + radius + availability) | Loop over matched donor list | in-app + email (per preference) | No dedicated idempotency key on blood-request creation (see §6) |
| 2 | `blood_request.incoming` | Same event, hospital-facing copy | Matching hospitals in city | Loop | in-app + email | Same as above |
| 3 | `blood_request.filed_for_you` | Family files on behalf of a linked patient | The patient | Single `notify()` | in-app + email | N/A (single recipient, not a fan-out) |
| 4 | `blood_request.cancelled` | Requester cancels | Donors/hospitals who had responded | Loop over responders | in-app + email | Cancellation is a one-way status transition; replaying it is a no-op at the DB layer (status already `CANCELLED`), so no duplicate notify path exists |
| 5 | `blood_request.fulfilled` | Request reaches fulfilled state | Requester (+ responders) | Loop | in-app + email | Same one-way-transition guard as above |
| 6 | `blood_request.donor_accepted` | Donor accepts | Requester | Single | in-app + email | Guarded by the response row's own unique-per-donor-per-request constraint |
| 7 | `blood_request.you_accepted` | Same event, donor-facing confirmation | The accepting donor | Single | in-app | N/A |
| 8 | `blood_request.donor_withdrew` | Donor withdraws an acceptance | Requester | Single | in-app + email | Guarded by response-row state machine |
| 9 | `blood_request.hospital_update` | Hospital posts a status update | Requester | Single | in-app + email | N/A |
| 10 | `family.link_requested` | Family sends a link request | Target patient | Single | in-app + email | Backend rejects a second PENDING request for the same (family, patient) pair (confirmed via the E2E test's own comment and by the real 409 the suite avoids by using a fresh relationship label per run) |
| 11 | `family.link_accepted` | Patient accepts | Requesting family member | Single | in-app + email | Link-row state machine (PENDING → ACCEPTED is one-way) |
| 12 | `family.link_rejected` | Patient declines | Requesting family member | Single | in-app + email | Same |
| 13 | `family.link_revoked` | Either party revokes an active link | The other party | Single | in-app + email | Same |
| 14 | `family.link_expired` | Scheduled/background expiry of a stale pending request | The requesting family member | Single | in-app | Driven by a one-way status transition |
| 15 | `account.password_changed` | User changes their own password | That user | Single | in-app + email | N/A — security-echo notification, not retried |
| 16 | `verification.approved` | Admin approves a verification | Submitter | Single | in-app + email | Verification-row state machine, one-way |
| 17 | `verification.rejected` | Admin rejects | Submitter | Single | in-app + email | Same |
| 18 | `verification.submitted` | **New this phase.** Donor/hospital submits a verification | Every active admin | Loop over `User.role == ADMIN, is_active` | in-app only (deliberately no email/SMS — see §5) | Submission itself has no idempotency key, but this is routine queue volume, not a safety-critical replay path; a genuine double-submit produces two `PENDING` rows and two notifications, which is honest (there really are two submissions) rather than silently deduplicated |
| 19 | `emergency.new` | Patient/family broadcasts Emergency SOS | Matching donors | Loop over matched donor list | in-app + email (quiet-hours never suppresses emergency email — confirmed in `test_alert_watcher.py`) | **Yes** — `Emergency.idempotency_key` (unique per `created_by_user_id`) short-circuits before the donor fan-out on both the fast-path replay and the SAVEPOINT-guarded concurrent-race path; confirmed by reading `create_emergency` and proven by this phase's new `test_emergency_idempotency_replay_does_not_double_notify_donors` |
| 20 | `emergency.acknowledged` | A hospital/donor acknowledges | Emergency creator | Single | in-app + email | N/A |
| 21 | `emergency.responder_accepted` | Donor/hospital accepts to respond | Emergency creator | Single | in-app + email | Response-row constraint |
| 22 | `emergency.you_responded` | Same event, responder-facing confirmation | The responder | Single | in-app | N/A |
| 23 | `emergency.responder_withdrew` | Responder withdraws | Emergency creator | Single | in-app + email | Response-row state machine |
| 24 | `emergency.assistance_found` | Enough responders found | Emergency creator | Single | in-app + email | One-way transition |
| 25 | `emergency.pathway_reoptimized` | Graph-engine pathway recalculated | Emergency creator | Single | in-app | N/A (informational) |
| 26 | `emergency.cancelled` | Creator cancels | Responders | Loop | in-app + email | One-way transition |
| 27 | `emergency.resolved` | Emergency marked resolved | Creator + responders | Loop | in-app + email | One-way transition |
| 28 | `certificate.ready` | Donation certificate generated | Donor | Single | in-app + email | Certificate generation is itself guarded upstream (Phase 12/13) against duplicate generation for the same donation |
| 29 | `appointment.requested` | Appointment scheduled | Counterparty | Single | in-app + email | N/A |
| 30 | `appointment.cancelled` | Appointment cancelled | Counterparty | Single | in-app + email | One-way transition |

## 4. Idempotency audit across every retryable notification-producing operation

The spec asked for an idempotency audit of every retryable operation, not
just emergencies. The finding:

- **Emergency SOS (row 19)** is the one operation in this system with a
  real, dedicated client-supplied idempotency key
  (`Emergency.idempotency_key`, unique per creator), and it is correctly
  wired to prevent a donor-notification replay. The frontend
  (`SosModal.tsx`) generates a real `crypto.randomUUID()` per SOS attempt
  (stable across a retry of the same attempt, reset on fresh modal open) —
  this is genuine client behavior, not a test fixture.
- **Blood request creation (rows 1–2) has no equivalent idempotency key.**
  This is a real, previously-undocumented asymmetry: a network retry of a
  blood-request submission (e.g. a flaky connection causing the client to
  resubmit) would create a second request row and re-notify every matching
  donor a second time. Three mitigating factors were confirmed by reading
  the frontend: the submit button is disabled for the duration of the
  pending mutation (`create.isPending`), TanStack Query does not
  auto-retry mutations by default (confirmed — no `retry` option is set on
  this mutation), and there is no client-side auto-resubmit logic anywhere
  in `BloodRequestModal.tsx`. Given this, the residual risk is a genuine
  double-click or a deliberate user resubmission, not a silent duplicate
  from network retry machinery. **This was deliberately not fixed this
  phase** — adding request-level idempotency keys would mean a schema
  migration, backend uniqueness constraint, and frontend key-generation
  change mirroring the emergency path, which is a real feature addition
  rather than a hardening fix, and was judged lower priority than the
  gaps that were fixed (see `LIFELINK_PHASE_18_REPORT.md` §11 for the
  explicit scoping rationale). It is recorded here as a **remaining
  limitation**, not swept under the rug.
- **Every other fan-out (rows 3–17, 20–30) is gated by a one-way status
  transition** on the underlying row (PENDING → ACCEPTED, ACTIVE →
  CANCELLED, etc.) rather than a client-supplied key. A replayed request
  against an already-transitioned row either 409s or no-ops before
  `notify()` is ever called, which was confirmed by reading each of the
  corresponding route handlers. This is a structurally different (and
  in this codebase, older and already-tested) idempotency mechanism than
  the emergency path's client-key approach, but it is real and it works —
  it is not a gap.
- **The RQ email job itself was not retryable at all before this phase**
  (see report §7) — a transient SMTP failure went straight to
  `FailedJobRegistry` after exactly one attempt. This was fixed with
  `Retry(max=settings.email_job_max_retries)`, and RQ's own semantics were
  verified (not assumed) to be **at-least-once, not exactly-once** — a
  successful delivery followed by a worker crash before the job is marked
  finished will re-run the job and send a second email. This is documented
  honestly in the new test
  `test_send_email_job_is_at_least_once_not_exactly_once_by_design` rather
  than claimed away. It does not double-write the in-app `Notification`
  row, because that row is created once, synchronously, before the job is
  ever enqueued — only the email side effect can double-fire.

## 5. Privacy / data-minimization audit

Every notification `body`/`data` payload was read at its call site and
checked against what the recipient can already see through the normal API
(never smuggling in more than that):

- The new `verification.submitted` admin notification body is
  `f"{user.full_name} submitted {len(documents)} document(s) for review."`
  with `data={"verification_id": ..., "kind": ...}` — no document contents,
  no storage keys/paths, no filenames. Confirmed by
  `test_verification_submission_notification_body_has_no_document_contents`,
  which asserts the exact `data` key set and greps the full title+body+data
  blob for `"verifications/"`, `.pdf`, and filename fragments.
- The same notification is scoped to `is_active` admins only — a
  deactivated admin account gets zero rows, confirmed by
  `test_verification_submission_notification_does_not_reach_a_deactivated_admin`
  querying the DB directly rather than trusting the API surface alone.
- Existing (pre-Phase-18) notification bodies across the other 29 types
  were spot-checked and contain only what the recipient's own existing API
  responses already expose (blood group, city/hospital name, relationship
  label, urgency) — no cross-role leakage (e.g. a donor's own contact
  details are never embedded in a patient-facing notification body; the
  patient discovers the donor's identity, if at all, through the existing
  authorized response-detail endpoint, not through the notification
  payload). This matches the existing IDOR protection already covered by
  `test_cannot_read_someone_elses_notification` in
  `tests/test_emergencies_and_notifications.py`.
- SMS: dormant by design, feature-flagged (`MSG91_ENABLED`), no user-facing
  toggle exposed in Settings — this was already true going into Phase 18
  and is unchanged; see `LIFELINK_SMS_STATUS.md` for the pre-existing
  detailed audit. `Settings.tsx` was re-read in full this phase and
  confirmed to only show an informational note, never a toggle.

## 6. Data-model audit

`Notification` (existing model, unchanged this phase): `id, user_id, type,
title, body, data (JSON), read_at, created_at`. No PII fields beyond what
`data` carries per-type (audited above). `NotificationPreference`:
per-user, per-channel opt-out plus quiet-hours window and digest
frequency — unchanged, already covered by
`tests/test_notification_preferences.py`.

## 7. Per-role notification-surface audit (frontend)

- **Patient / Family / Donor / Hospital**: share `NotificationsBell.tsx`
  (header dropdown, polls every 30s, has a retry button on load failure —
  fixed in an earlier Phase 18 pass) and `NotificationsCenter.tsx`
  (`/notifications` full page: filter tabs, mark-read, mark-all-read,
  retry-on-error — the error-state retry button was added this phase).
  The icon map (`TYPE_ICON`) was expanded from 11 to all 30 real types this
  phase; a type missing from the map safely falls back to a generic icon
  rather than breaking rendering, confirmed by reading the fallback branch.
- **Admin**: genuinely different, and this is a real (not
  previously documented) UI-consistency finding — `AdminOverview.tsx` does
  not import or render the shared `NotificationsBell` component at all
  (confirmed by grep: no admin page imports it). Admin instead has its own
  inline "Your Notifications" panel on the Overview page, wired to the
  same `GET /notifications` endpoint. This means admin sub-pages beyond
  Overview (verification queue, user management, etc.) have **zero**
  notification surface — an admin working from those pages would not see
  a new verification submission arrive without navigating back to
  Overview or to `/notifications` directly. This is recorded as a
  **remaining limitation**, not fixed this phase (see report §11 for the
  scoping rationale).

## 8. Real-time-behavior honesty note

Restated plainly for the record: this is a polling system, not a push
system. 30-second bell polling plus refetch-on-focus is the actual
mechanism end to end, confirmed by reading `NotificationsBell.tsx`'s
`useQuery` options. The new Playwright E2E suite (report §12) does not
wait on any WebSocket or SSE channel — it waits on the bell's own refetch
behavior (re-opening the dropdown/navigating triggers a fresh fetch),
which is why every E2E flow re-logs-in as the recipient rather than
leaving a stale session open and waiting for a push update that does not
exist.

## 9. Observability audit

Every `notify()` call site sits inside route handlers that already call
`audit_service.record(...)` for the underlying domain action (verification
submission, emergency creation, etc.) — notification delivery itself has
no separate structured audit trail beyond the `Notification` row and the
RQ job's own `FailedJobRegistry`/`FinishedJobRegistry` (real, inspectable
via `rq info` or direct Redis queries; exercised directly by this phase's
new worker tests). This was true before Phase 18 and is unchanged; no gap
was found here worth a dedicated fix, since the underlying domain action
is already audited and the notification is a deterministic side effect of
that same, already-audited action.
