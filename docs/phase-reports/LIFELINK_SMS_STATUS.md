# LifeLink SMS Status

**SMS notifications are not currently enabled in this deployment.**

This document exists so that nobody — a user reading the settings page, a
teammate reading the code, an instructor reviewing the project — has to
guess at what SMS actually does in LifeLink right now. Short version: it
does nothing. No LifeLink user has ever received, or can currently receive,
a text message from this application.

## The plain facts

- **SMS is disabled by default**, and nothing in this deployment turns it
  on. The master switch is the `MSG91_ENABLED` environment variable, which
  defaults to `false` and is unset (equivalently false) everywhere this
  project runs — local dev, the test suite, and however it's deployed for
  submission/demo.
- **SMS is not required for anything to work.** Emergency SOS creation and
  broadcast, donor/hospital alerting, blood-request matching, the graph/
  resource-pathway engine, and every other core LifeLink flow run entirely
  on in-app notifications (and email, where configured) and have no
  dependency on SMS anywhere in their code path.
- **In-app notifications are fully active.** The bell icon, the
  notification list, unread counts, and every real-time in-app alert for
  emergencies, blood requests, donor matches, family links, and hospital
  updates work exactly as before and are unaffected by SMS being off.
- **Email notifications work when SMTP is configured**, independently of
  SMS — they're a separate channel with their own on/off logic
  (`NotificationPreference.email_enabled`, digest frequency, quiet hours).
- **A dormant, feature-flagged SMS provider abstraction exists in the
  backend** (`app/services/sms/`, `app/core/phone.py`,
  `app/services/sms_service.py`) but is not reachable from any normal code
  path while `MSG91_ENABLED=false`. It was built to demonstrate a clean,
  channel-agnostic notification architecture and to leave a real place to
  plug in a provider later — not to claim SMS delivery is available today.
  See `LIFELINK_SMS_PROVIDER.md` for the technical detail.
- **No user-facing SMS control is shown.** The Settings page does not
  offer an "SMS notifications" toggle. It shows a plain, low-emphasis note
  instead: *"SMS notifications are not currently enabled for this
  deployment."* An admin-only system-health panel reports SMS provider
  configuration status (configured/not configured, recent send failures)
  for operational visibility — it never claims a message was or will be
  delivered.
- **Turning SMS on for real production use would require legitimate
  provider configuration** — a real MSG91 account, a real auth key, a
  registered sender ID — **and, for India, completing DLT (Distributed
  Ledger Technology) template and sender registration**, which is a
  regulatory requirement for transactional SMS to Indian numbers, not
  something this codebase can configure on its own. None of that has been
  done for this project, and this document does not claim otherwise.

## Why

LifeLink is a college project. Presenting SMS as a working feature it
isn't would be dishonest to anyone reading the app, and this project's
whole engineering standard (documented consistently across every phase
report) has been to never fabricate a capability the code doesn't
genuinely have. Rather than delete the SMS work outright, it's kept as
inert, clearly-labelled infrastructure — the same treatment already given
to the Neo4j graph engine (`app/core/graph.py`) and to S3 object storage
(`app/core/storage.py`), both of which degrade honestly to a documented
fallback or "not configured" state instead of failing or faking success.

## What would change if SMS were switched on

Nothing about this decision closes the door on SMS later. If a future
deployment sets `MSG91_ENABLED=true` with real, working MSG91 credentials
(and completes DLT registration for India), the existing
`notification_service.notify()` entry point already has the gating logic
to also send SMS for the small set of emergency/blood-donation events it's
wired to, per-user opt-in, respecting quiet hours the same way email does.
See `LIFELINK_SMS_PROVIDER.md` for exactly what's implemented, what's a
known limitation, and what a real production rollout would still need.
