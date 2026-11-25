# LifeLink Phase 5 — Complete Hospital Portal

## 0. Starting point

Before writing any code, the existing hospital portal was audited end to
end (backend routes/models/schemas and every `frontend/src/pages/hospital/*`
screen). It turned out to be substantially more complete than a from-scratch
build would suggest: a real `HospitalDashboard`, `InventoryConsole`,
`RequestManagementDetail`, `DonorDirectoryAndLogistics`, `ResourcePathways`
and mobile-first `AnalyticsMobile` already existed, all wired to real
backend data with no fabricated numbers (each file's own header comment
documents exactly what was stripped out from the original Stitch mock and
why). So this phase is an *extension* of real, working code, not a
replacement — the plan below is scoped to the genuine gaps found against
the Phase 5 spec, not a rebuild.

## 1. What was already real (preserved, not rebuilt)

- Blood inventory per the 8 ABO/Rh types, incoming-request board, incoming
  emergencies, KPI tiles, and the live donor map — all on `GET
  /hospitals/me/*` endpoints, all genuinely wired.
- The anonymized donor directory (`GET /blood-requests/{id}/match-candidates`)
  — donor identity (name/phone/email) is never sent to a hospital caller,
  enforced server-side in `matching.py`, not just hidden in the UI.
- The real Leaflet/OpenStreetMap live map (`react-leaflet` + `leaflet`,
  `DonorMap.tsx`) — untouched, per the instruction not to replace it.
- The real graph-engine resource-pathway search (`GET
  /hospitals/me/resource-pathways`), with an honestly-labelled haversine
  fallback whenever Neo4j is unreachable.
- `AnalyticsMobile.tsx`, a genuinely mobile-first analytics screen computing
  every number client-side from real endpoints (no hardcoded chart data).

## 2. Real gaps closed this phase

1. **Inventory was "set an absolute number," not real actions.** The spec
   calls for add / reserve / allocate / release / expire / reconcile, all
   audited. Added:
   - `HospitalInventory.reserved_units` (new column) and a `HospitalInventoryLog`
     audit table (one row per action, signed delta + resulting totals).
   - `app/services/inventory_service.py` — one function per action, each
     enforcing real constraints (never negative, never over-reserved) and
     writing an audit row.
   - DB-level `CHECK` constraints (`units >= 0`, `reserved_units >= 0`,
     `reserved_units <= units`) as defense in depth beyond the app-layer
     checks — "never allow negative inventory" holds even against a bug.
   - Six new endpoints: `POST /hospitals/me/inventory/{add,reserve,allocate,
     release,expire,reconcile}`, plus `GET /hospitals/me/inventory/log`.
   - `InventoryConsole.tsx` now exposes all six actions per blood type (not
     just the old "set units" box, which is kept as the reconcile action)
     and shows the real activity log.
2. **Hospitals couldn't allocate or fulfil a request from their own stock.**
   Only ACKNOWLEDGED/PREPARING/DISPATCHED/DECLINED existed. Added
   `ALLOCATED`/`FULFILLED` to `HospitalResponseStatus`, and two new
   endpoints: `POST /hospitals/me/requests/{id}/allocate` (a real inventory
   deduction tied to the request) and `.../fulfill` (transitions the
   *same* `BloodRequestStatus.FULFILLED` the donor-matching path uses — one
   shared state machine, not a second one). The generic `/respond` endpoint
   now rejects these two statuses (422) since they have real side effects
   the free-form endpoint must not trigger implicitly. Wired into
   `RequestManagementDetail.tsx`.
3. **No Alerts, Recent Activity, or Transport/Resources on the dashboard.**
   Added `GET /hospitals/me/activity` (a real merge of the new inventory
   log and existing `BloodRequestEvent` rows — it invents no data, only
   merges two real sources) and `GET /hospitals/me/routes` (the existing
   admin-managed `TransportRoute` table, filtered read-only to links
   touching the caller's own facility). Both are now real panels on
   `HospitalDashboard.tsx`; a full transport-links table was added to
   `ResourcePathways.tsx` for the Logistics view.
4. **Mobile was not usable at all on the main dashboard.** `HospitalDashboard.tsx`
   had a bespoke, fixed `w-64`/`ml-64` sidebar with zero responsive
   behavior — unusable below desktop width, including the EMERGENCY SOS
   button. It's rebuilt on the app's own shared, already-tested responsive
   shell (`components/shell/AppShell.tsx`, built in an earlier phase but
   not yet adopted anywhere — this is that adoption). SOS and inventory
   management now reach a phone-width viewport; the sub-screens
   (`InventoryConsole`, `RequestManagementDetail`, `DonorDirectoryAndLogistics`)
   already used a responsive top bar and grid and needed no layout change.

## 3. Deliberately not built (and why)

- **Blood "components" (plasma/platelets/whole blood).** The data model
  has never had a components concept — `DonorProfile`'s own comment states
  this explicitly ("the system doesn't model donation components"). The
  spec says "support components where model supports them"; the model
  doesn't, so none were invented. `HospitalInventory` stays one row per
  ABO/Rh type.
- **Cross-hospital partial-fulfilment tracking.** `fulfill_from_hospital`
  declares a request met from this hospital's own supply; it does not sum
  units allocated by multiple hospitals against `units_needed` (no field
  in the schema tracks that). Documented in the endpoint's own docstring
  rather than silently assumed.
- **Admin-style transport-route management for hospitals.** Creating or
  disrupting a `TransportRoute` stays an admin/demo-control action per the
  existing architecture; hospitals get read-only visibility into routes
  touching their own facility, not a way to edit the network.

## 4. Tests

`backend/tests/test_hospital.py` gained 7 new tests: the full add → reserve
→ release → allocate → expire → reconcile lifecycle with exact unit/reserved
math; every negative/over-reservation rejection path (409s, plus 422 for a
non-positive delta); the allocate → fulfil happy path on a real request
(inventory deduction, event timeline, notification, double-fulfil/allocate
rejected); wrong-blood-type and insufficient-stock rejection; the
`/respond` endpoint refusing ALLOCATED/FULFILLED; the transport-routes
endpoint scoped correctly to the caller's own facility; and the merged
activity feed containing both real sources, newest first.

## 5. Verification

```
# Backend
ruff check app tests            # clean except pre-existing, untouched-file debt
mypy app                        # clean except 3 pre-existing errors in
                                 # verifications.py/donors.py, never touched here
ENVIRONMENT=test pytest         # 280 passed (273 pre-existing + 7 new)
alembic upgrade head / downgrade -1 / upgrade head   # round-trips cleanly

# Frontend
npm run typecheck               # clean
npm run lint                    # clean
npm run build                   # succeeds
```

## 6. Files changed

**Backend:** `app/models/enums.py`, `app/models/hospital.py`,
`app/models/__init__.py`, `app/schemas/hospital.py`,
`app/services/inventory_service.py` (new),
`app/api/routes/hospitals.py`,
`alembic/versions/a1b2c3d4e5f6_hospital_inventory_actions.py` (new),
`tests/test_hospital.py`.

**Frontend:** `src/lib/domain.ts`, `src/pages/HospitalDashboard.tsx`,
`src/pages/hospital/InventoryConsole.tsx`,
`src/pages/hospital/RequestManagementDetail.tsx`,
`src/pages/hospital/AnalyticsMobile.tsx`,
`src/pages/hospital/ResourcePathways.tsx`,
`src/components/HospitalInventoryModal.tsx`.
