# LifeLink Phase 6 — Complete Admin Control Center

## 0. Starting point

As with every prior phase, the existing Admin portal was audited end to end
before writing anything — every route in `app/api/routes/admin.py` and
`app/api/routes/verifications.py`, every admin schema, and all five
`frontend/src/pages/admin/*.tsx` screens. It turned out to be substantially
more complete than the spec might suggest: user directory/search, role
change, suspend/reactivate, a real donor/hospital verification workflow
(submit → document storage → admin review with document preview → approve
with side effects / reject with a reason → audit trail → notification),
system health, and a full transport-route/graph administration surface
(create/disrupt/recover routes, resync the graph, a pathway audit log) were
all already real, already audited, and already covered by 403-for-every-
non-admin-role tests. Nothing here needed to be rebuilt. This phase closes
the specific, genuine gaps found against the Phase 6 spec.

## 1. What was already real (preserved, not rebuilt)

- **User management & role governance**: search/filter/paginate the user
  directory, change a role, suspend (with a required reason) / reactivate —
  every mutation audited, self-role-change and self-suspend both blocked
  server-side.
- **Verification queue**: submit → admin list/filter by kind & status →
  detail view → per-document preview (streamed from object storage, never a
  public URL) → approve (the *only* code path that ever sets
  `DonorProfile.identity_verified` / `HospitalProfile.verified`) or reject
  with a required reason → audit trail → a real notification to the
  submitter. This already covers the spec's entire "Verification Queue" and
  "Facility verification" line items for the workflow itself.
- **Transport routes & graph administration**: create/disrupt/recover real
  `TransportRoute` rows, with a genuine before/after pathway-impact
  comparison (or an honest haversine fallback when Neo4j is down), plus
  `POST /admin/graph/resync` (fails loudly, 503, rather than claiming
  success when Neo4j is unreachable) and a pathway audit log.
- **System health**: real DB/Redis/storage/graph/SMS component checks, with
  "not configured" correctly treated as a normal feature-off state rather
  than a fault.
- **Security architecture**: role comes from a server-side session lookup
  against `User.role` in the database (`app/api/deps.py:get_current_user`)
  — never from a client-supplied claim or request body — so no admin
  endpoint can be reached by "manually constructing the HTTP request" with
  a forged role. This was audited, not just assumed, and was already
  exercised by 403 tests across every non-admin role on every existing
  admin endpoint (`test_admin.py`, `test_verifications.py`,
  `test_graph_api.py`).

## 2. Real gaps closed this phase

1. **No certificate registry.** `GET /certificates` was donor-only and
   scoped to "my own certificates"; there was no way for an admin to search
   certificates platform-wide. Added `GET /admin/certificates` (search by
   serial, verify code, donor name/email, or hospital name) and wired it
   into `AdminAnalytics.tsx`, replacing the disabled search box that
   previously said "no admin-wide certificate registry" — the search now
   works for real. The public certificate-verify-by-code tool (already
   real) is preserved unchanged.
2. **No facilities view.** There was no way to review hospitals/blood banks
   as a group — only per-facility detail via the donor-facing directory
   picker. Added `GET /admin/facilities` (search by name/city, filter by
   type and verification status, paginated, each row carrying a real
   on-hand/reserved inventory total) and `GET /admin/facilities/{id}` (a
   detail view with the facility's real per-blood-type inventory — the same
   `HospitalInventory` rows the facility's own dashboard reads). New
   `AdminFacilities.tsx` screen with a directory table and a detail drawer.
3. **No donor governance view distinct from the general user directory.**
   `GET /admin/users?role=DONOR` only exposes generic account fields
   (name/email/role/active/created_at) — none of the donor-specific
   verification/eligibility signals a governance reviewer needs. Added
   `GET /admin/donors` (search by name/email, filter by blood type,
   identity-verification state, and availability). `AdminDonorOut`
   deliberately **excludes** `DonorProfile.medical_conditions` and
   `.weight_kg` (health data) and raw latitude/longitude (precise location)
   — enforced at the schema layer, not just hidden in the UI, per the
   spec's explicit "do not expose unnecessary private medical data." A test
   (`test_donor_governance_excludes_medical_data`) asserts those keys are
   never present in the response at all. New `AdminDonors.tsx` screen.
4. **No real analytics.** `AdminAnalytics.tsx` previously showed a static,
   non-functional list of funnel step labels with a "no analytics endpoint"
   panel underneath. Added `GET /admin/analytics/overview`: real `GROUP BY`
   aggregates over `BloodRequest.status`, `BloodRequest.blood_type`,
   `Emergency.status`, `DonorResponse.status`,
   `HospitalRequestResponse.status`, and `VerificationRequest` (kind ×
   status), plus a real per-day count of requests filed over a caller-
   chosen window (default 14 days). Every number is computed fresh from the
   current database on each request; a status this platform has never
   recorded a row for legitimately renders as 0, never padded to look
   busier. Wired into `AdminAnalytics.tsx` as bar/sparkline breakdowns.
5. **Audit feed had no dedicated search UI and only two filters.**
   `GET /admin/audit-log` only filtered by exact `action` and
   `actor_user_id`; there was no way to filter by resource, actor role, a
   date range, or severity, and the only UI for it was eight rows embedded
   in the Overview screen. Extended the endpoint with `target_type`
   (resource), `actor_role`, `since`/`until`, and `severity`, and added a
   `severity` field to every returned entry. **Severity is derived from the
   action string at read time** (`_classify_severity` — endings in
   `_failed` → CRITICAL; actions containing `suspended`, `rejected`,
   `disrupted`, or `role_changed` → WARNING; everything else → INFO) — it
   is never a separately stored fact that could drift out of sync with the
   action name, and the SQL-side filter (`_severity_sql_match`) expresses
   the exact same three buckets so filtering and display can never
   disagree. Built a new, dedicated `AdminAuditFeed.tsx` screen with all six
   filters and cursor pagination; the Overview screen's existing "recent
   activity" panel is untouched.

## 3. Investigated and confirmed already correct (no change needed)

- **"Role changes must not allow privilege escalation through frontend
  manipulation."** I initially added an explicit "can't demote/suspend the
  last remaining admin" guard, reasoning it through as a natural extension
  of role governance safety. On tracing the actual code path, that guard
  turned out to be **unreachable**: `change_user_role` and `suspend_user`
  already refuse a self-action before any such check would run, and every
  request that reaches either endpoint is, by construction, made by an
  *already-active* admin who is not the target — so "another active admin
  exists" is always true at that point, by the acting admin's own
  existence. Shipping a safety check that can never actually fire would
  have been exactly the kind of fake-safety-net this project's honesty
  principle exists to prevent, so I removed it rather than leave dead code
  behind. The platform's real protection against ever reaching zero admins
  is the pre-existing "cannot act on yourself" checks, which are already
  correct and already tested (`test_admin_cannot_change_own_role`,
  `test_admin_cannot_suspend_self`).

## 4. Deliberately not built (and why)

- **CSV export of the audit log / a broadcast-alert endpoint.** Both were
  already flagged as honest "Backend required" gaps on the Overview screen
  before this phase and remain out of scope — the spec's "searchable audit
  feed" asks for filters, which are now real; it does not ask for export or
  broadcast messaging.
- **Editable facility records for admins** (renaming a hospital, moving its
  coordinates). The spec says admin can *review* facilities, verification,
  and status — it does not ask for admin-side editing of a hospital's own
  profile fields, which stays that hospital's own responsibility via
  `PUT /hospitals/me`.

## 5. Tests

`backend/tests/test_admin_phase6.py` is new — 32 tests: 403-for-every-non-
admin-role coverage on all five new/extended read surfaces (facilities
list, facility detail, donors list, certificates registry, analytics
overview); facility list/detail reflecting real inventory totals and
per-blood-type breakdowns, and its search/type/verified filters; the donor
governance exclusion test described above plus its blood-type/availability/
identity filters; certificate registry search across serial, donor name,
and hospital name; analytics overview counts changing in step with a real
blood request being filed, and the `days` window being respected; a direct
unit test of `_classify_severity`'s three buckets; and the audit log's new
`severity`/`target_type`/`actor_role`/`since` filters and hydrated
`actor_email`/`actor_role` fields.

## 6. Verification

```
# Backend
ruff check app tests             # clean on every file this phase touched
                                  # (pre-existing UP042/E501/I001 debt in
                                  # untouched files, unchanged from before)
mypy app                         # clean on every file this phase touched;
                                  # same 3 pre-existing errors in
                                  # verifications.py/donors.py as before,
                                  # never touched here
ENVIRONMENT=test pytest          # 312 passed (280 pre-existing + 32 new),
                                  # 0 failed
# No Alembic migration this phase -- every new endpoint reads existing
# tables/columns; nothing required a schema change.

# Frontend
npm run typecheck                # clean
npm run lint                     # clean
npm run build                    # succeeds (2164 modules transformed)
```

## 7. Files changed

**Backend:** `app/schemas/admin.py` (new facility/donor/certificate/
analytics schemas, extended audit-log entry schema), `app/api/routes/
admin.py` (six new endpoints: `GET /admin/facilities`, `GET /admin/
facilities/{id}`, `GET /admin/donors`, `GET /admin/certificates`, `GET
/admin/analytics/overview`; extended `GET /admin/audit-log` with target_
type/actor_role/since/until/severity filters and severity classification),
`tests/test_admin_phase6.py` (new).

**Frontend:** `src/lib/domain.ts` (new admin API types/functions for
facilities, donors, certificates, analytics; extended audit-log types),
`src/pages/admin/adminNav.ts` (new — the identical `ADMIN_NAV`/
`ACCOUNT_NAV` arrays that had been hand-duplicated across all five existing
admin screens are now a single shared module), `src/pages/admin/
AdminFacilities.tsx` (new), `src/pages/admin/AdminDonors.tsx` (new),
`src/pages/admin/AdminAuditFeed.tsx` (new), `src/pages/admin/
AdminAnalytics.tsx` (real analytics + certificate registry replacing the
two "Backend required" panels), `src/pages/admin/AdminOverview.tsx`,
`TransportRoutes.tsx`, `UserManagement.tsx`, `VerificationsQueue.tsx`
(nav dedup only — no behavior change), `src/App.tsx` (routes + command-
palette entries for the three new screens).
