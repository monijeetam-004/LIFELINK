# Security audit — gap-elimination round

Scope: every endpoint, service and worker added or changed in this round,
plus a re-run of the existing security sweeps. Method: code review of each
change, attack-style tests, the automated CSRF/origin sweep, dependency
audits.

## Defects found and fixed (with the test that now guards each)

| # | Finding | Fix | Guarded by |
|---|---|---|---|
| S1 | Releasing a reservation "for" one emergency could free units another emergency was holding (release only *preferred* its own context). | Context-strict release: own-context or context-free holds only; otherwise 409. | `test_gap_pathway_engine.py::test_release_never_frees_another_emergencys_reservation` |
| S2 | `release_reservation` decremented the counter and wrote the ledger before checking the units existed; a caught error left a dirty session. | Select and validate first, mutate after. | same + `test_phase34_*` |
| S3 | FEFO reservation could pick units that expire before the delivery arrives. | `reserve_stock(usable_until=arrival)`; checked before any mutation. | `test_reservation_takes_only_units_in_date_at_arrival_and_is_all_or_nothing` |
| S4 | Unverified facilities could accept emergencies and commit stock; responder lists offered them. | Verification gate (403, audited refusal); unverified excluded from responder ranking and as pathway sources unless an audited ADMIN override exists; revocation endpoint fails over pathways and withdraws responses. | `test_gap_features_api.py::test_unverified_hospital_*`, `test_admin_revocation_*`, `test_gap_pathway_engine.py::test_*verification*` |
| S5 | Emergency alerts went to every hospital whose city string appeared in the free-text address, verified or not. | Verified + within radius (or same city) + holds compatible usable stock, plus the target facility. | `test_hospital_alerts_use_proximity_capability_verification_and_target` |
| S6 | `POST /pathways/simulate` was added without the CSRF origin dependency. | `verify_origin` added. | `test_phase_h_security.py::test_every_state_changing_route_has_csrf_origin_check` (caught it) |
| S7 | A disabled audit guard trigger was not detected. | `/admin/audit-log/verify` reports trigger state; disabled/missing → `ok=false`. | `test_gap_audit_security.py::test_disabled_guard_trigger_*` |
| S8 | Admin reads of the audit log and of identity documents were not audited. | `admin.audit_log_viewed`, `admin.verification_document_viewed`. | `test_admin_reads_of_the_audit_log_are_themselves_audited` |
| S9 | Donor distance limit could only be bypassed implicitly (no limit existed). | `MAX_DONOR_DISTANCE_KM`; override ADMIN-only, reason required, audited, revocable; non-admin → 403. | `test_donor_distance_limit_below_at_and_above_and_admin_override` |
| S11 | Auditing audit-log reads (S8) made the offset-paginated audit log shift by one row per page read, repeating entries (found by the full regression: `test_audit_log_lists_real_entries_with_pagination`). | Keyset cursor `(created_at, id)`; malformed cursors → 400. | `test_admin.py::test_audit_log_pages_never_repeat_although_reading_appends_entries` |
| S12 | **Client IP spoofing.** `client_ip()` trusted the raw `X-Forwarded-For` header from any client. Rotating that header gave an attacker a fresh per-IP budget on every request, which bypassed the login and registration rate limits; it also let the client choose the IP recorded in audit entries and sessions. The production entrypoints also passed `--forwarded-allow-ips="*"`. Found while investigating E2E rate-limit behaviour. | `client_ip()` now uses only the peer address that the ASGI server resolved. Proxy headers are honoured only by uvicorn `--proxy-headers`, from the proxies listed in `FORWARDED_ALLOW_IPS` (uvicorn's default is 127.0.0.1). `"*"` was removed from the Dockerfile CMD and from `run_production.sh`. | `test_gap_rate_limit.py::test_spoofed_x_forwarded_for_cannot_bypass_the_login_ip_limit`, `::test_audit_records_do_not_trust_a_client_supplied_forwarded_header` |
| S10 | Pathway logs could not record system-initiated replans (NOT NULL actor) — an automated failover would have crashed. | Column nullable (= system). | `test_full_route_is_stored_in_order` (crashed before the fix) |

**Deployment note for S12.** Behind a load balancer, set `FORWARDED_ALLOW_IPS` to the balancer's address(es). Otherwise every request appears to come from the balancer, and per-IP limits then apply to all clients together.

## New attack surfaces and their controls

* **Telemetry ingestion**: timezone required; future (>5 min skew) and
  pre-shipment readings refused; a reading without a value must carry an
  error code; implausible values (< −90 °C or > 60 °C) count as sensor
  failure; idempotent per (sensor, timestamp); only parties to the shipment;
  SIMULATED source refused in production.
* **Geocoding**: external geocoder OFF by default (patient address text is
  not sent to third parties unless a deployment opts in); 3 s timeout;
  coordinates range-checked; failures never invent a location.
* **Development-only paths refused in production**: console SMS provider,
  telemetry simulator.
* **Least privilege**: `backend/scripts/least_privilege_role.sql` creates
  an application role that cannot disable the audit trigger or delete audit
  rows — verified on a fresh database (`evidence/least_privilege_check.txt`): as
  `lifelink_app`, DISABLE TRIGGER / DELETE / TRUNCATE / DROP / OWNER TO on
  `audit_logs` all fail, while the application's audit writer and chain
  verification work. The script now grants CONNECT on the database it is
  run against (it previously hard-coded `lifelink`).

## Sweeps re-run

* CSRF/origin sweep over every state-changing route (160 operations) — PASS.
* `npm audit --omit=dev`: 0 vulnerabilities; audit gate PASS (1 dev-only
  allowlisted advisory, unchanged).
* `pip-audit -r requirements.lock`: no known vulnerabilities.
* Secrets: no credentials in source, `.env.example`, compose files or logs;
  the sandbox CA and generated passwords never entered the repository.
