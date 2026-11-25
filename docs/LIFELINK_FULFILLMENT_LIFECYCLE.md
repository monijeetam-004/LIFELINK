# LifeLink — Blood Request Fulfilment Lifecycle (quantity-aware)

Introduced in Phase E. Code: `backend/app/core/request_fulfillment.py` (pure quantity rules), `backend/app/core/request_lifecycle.py` (transition table), `backend/app/api/routes/hospitals.py` (`allocate_to_request`, `fulfill_from_hospital`), `backend/app/api/routes/blood_requests.py` (`fulfill_request`). Tests: `backend/tests/test_phase_e_fulfillment.py`, `backend/tests/test_phase_e_invariants.py`.

## Single source of truth

`BloodRequest.fulfilled_units` (integer, `0 ≤ fulfilled_units ≤ units_needed`, both bounds enforced by Postgres `CHECK` constraints) is **the** answer to "how much of this request has been met". It is a stored column updated under the request's own row lock, never derived at read time and never accepted from a client (`test_client_cannot_set_fulfilled_units_directly`).

`HospitalRequestResponse.units_allocated` is each hospital's own contribution. Invariant: `Σ units_allocated ≤ fulfilled_units`, with equality whenever every unit came through a hospital (the donor-matched requester path has no hospital to attribute units to).

## States

```
PENDING ──► MATCHING ──► (back to PENDING if every donor withdraws)
   │            │
   ├────────────┼──► PARTIALLY_FULFILLED ──► FULFILLED
   │            │            │
   └────────────┴────────────┴──► CANCELLED | EXPIRED
PENDING/MATCHING ──► FULFILLED (allocation covering everything at once, or a manual fulfil)
```

`PARTIALLY_FULFILLED` is not a label: it is entered only when `0 < fulfilled_units < units_needed`, and only by an allocation that actually moved inventory. It never goes back to `PENDING`/`MATCHING` — units already dispatched are real, so a donor withdrawing cannot "reopen" the request. It is still an **open** status (backend `OPEN_STATUSES`, frontend `OPEN_REQUEST_STATUSES`): it still needs blood.

`REJECTED` was considered and **not** added: the only rejection that exists is a single hospital declining (`HospitalResponseStatus.DECLINED`), which does not close the request.

## Transitions and who causes them

| Action | Effect on quantity | Status |
|---|---|---|
| `POST /hospitals/me/requests/{id}/allocate` (`units = n`) | Validated **before** inventory is touched: `fulfilled + n ≤ needed`, else 409 with zero side effects. Then inventory is deducted, `fulfilled_units += n`, the hospital's `units_allocated += n`. | Bucket recomputed: `PARTIALLY_FULFILLED` or `FULFILLED` via the real state machine (event + audit). If the bucket didn't change (a second partial), a `hospital_allocated` event + `hospital.request_allocated` audit — never a same-state "transition". |
| `POST /hospitals/me/requests/{id}/fulfill` | Declares the **remaining** gap met through a channel the system doesn't itemise; `fulfilled_units := units_needed`; the gap is attributed to this hospital's `units_allocated`. | `FULFILLED` |
| `POST /blood-requests/{id}/fulfill` (requester, donor-matched) | `fulfilled_units := units_needed`; no hospital attribution. | `FULFILLED` |
| Cancel / expire | Unchanged — units already dispatched stay recorded, never silently restocked. | `CANCELLED` / `EXPIRED` |

Behaviour change worth knowing: allocating the **full** quantity in one call now fulfils the request by itself; an explicit `/fulfill` afterwards correctly returns 409. The pre-Phase-E flow required a second manual call, which is why `tests/test_hospital.py::test_hospital_allocate_and_fulfill_request` was updated (the test encoded the old flow; the new assertions check the new, quantity-driven behaviour and the 409).

## Concurrency (real PostgreSQL, real threads)

All of these serialise on the same `SELECT … FOR UPDATE` row lock every request mutation already took — no new locking mechanism.

| Race | Scenario | Proven outcome |
|---|---|---|
| A | Two hospitals each try to allocate the final remaining units | exactly one 200, one 409; loser's inventory untouched; `fulfilled_units == units_needed` |
| B | Two partial allocations that together fit | both succeed, no lost update; plus 5 hospitals × 1 unit against a 4-unit request → exactly 4 succeed |
| C | Allocation vs requester cancel | either history is legal; inventory matches whichever won |
| D | Allocation vs another hospital's manual fulfil | always `FULFILLED`, `fulfilled_units == units_needed`, breakdown sums |

Each race test was run 5× consecutively without a failure.

## Frontend

The UI never computes or sends a fulfilled quantity. It shows `fulfilled_units / units_needed` (shared `FulfillmentProgress` component) and derives "remaining" for display only. Every screen that previously hard-coded `["PENDING","MATCHING"]` as "open" (7 places — patient, family, donor, hospital dashboards, request history, request drawer, request detail) now uses the shared `OPEN_REQUEST_STATUSES`; before this, a partially-fulfilled request would have shown as closed. The patient's "Fulfilled units" total now sums real `fulfilled_units` instead of `units_needed` of fully-fulfilled requests only.

## Migration backfill

`FULFILLED` rows: `fulfilled_units = units_needed`. Open rows: the real sum of their historical `ALLOCATE` inventory-log deltas (capped), with a status correction **and** a `phase_e_fulfilment_backfill` event per affected row. In the dev database no open request had historical allocations, so no status changed.
