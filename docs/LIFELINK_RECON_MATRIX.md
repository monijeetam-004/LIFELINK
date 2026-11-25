# LifeLink — Reconnaissance Matrix (living status)

The full Phase A matrix, with its original per-row evidence, is `LIFELINK_PHASE_35_RECON_MATRIX.md`. This file is the current status of every row that a phase has since closed, re-verified against the code when closed.

| Area | Closed in | Current state |
|---|---|---|
| Blood component model, unit identity, donation→unit traceability, defense-in-depth compatibility | B/C (Phase 35) | `BloodComponent`, `unit_code`, `donation_id`, component-aware compatibility. |
| Transport / shipment + chain of custody | D | Shipment lifecycle, ownership transfer, invariants 23–27. |
| Request fulfilment states | E | Quantity-backed `PARTIALLY_FULFILLED`, races A–D. |
| Donation verification | E | `DONOR_REPORTED/VERIFIED/REJECTED`, facility-scoped review, certificate gate. |
| Shelf life provenance | E | Machine-readable, fingerprint-pinned, per-unit `expiry_rule_ref`. |
| Audit log | E | Tamper-evident hash chain + anchoring. |
| Verification document versioning | E | Versions, checksums, decisions bound to checksums. |
| Failed-shipment reconciliation | E | Attested, one-time, origin/admin reconciliation. |
| **Real-time architecture** (11–12 polling sites) | **F** | SSE + transactional outbox + ordered Redis publication + authorized replay; polling only as a fallback while the stream is down. See `LIFELINK_REALTIME_EVENTS.md`. |
| **NotificationsBell per-item Read** | **G** | Per-row in-flight lock (bell, donor Ready Console, admin overview). |
| **Admin facility inventory honesty** | **G** | Detail sweeps expiry; list subtracts expired-unswept; component column shown (was two identical "O-" rows). |
| **Component-level inventory visibility** (deferred in B/C "until the frontend calls for it") | **G** | `GET /hospitals/me/inventory/components` + console section + component selector — needed once shipments could carry components. |
| DonorDashboardMobile | already closed before Phase A | Redirect to the real, responsive `/donor` (re-verified in G). |
| Stale-state audit | **G** | Real-time invalidation (F); hospital "Analytics" quick link pointed at the conceptual-illustration page → now real analytics; inventory action overlay replaced by the accessible Modal. |
| **React Router advisory** (Phase A: "non-applicable") | **H** | A newer advisory (GHSA-wrjc-x8rr-h8h6, client-side open redirect) did apply to 6.30.6 → upgraded to 7.18.4; axios 1.20.0; runtime `npm audit` = 0, enforced by `audit-gate.mjs`. |
| **Security pass (§18)** | **H** | Systemic route-table guards (auth/CSRF/admin/mass-assignment, live sweeps); wildcard-CORS startup refusal (real finding); session fixation; secrets-in-logs; urllib3 floor. |
| **Infrastructure reproducibility** (MinIO/Mailpit gating, bucket init) | **I** | Healthchecks + `service_healthy`/`service_completed_successfully` gating; the 10 Phase 34 failures root-caused and passing on an S3 API; real-MinIO CI job. |
| **CI pipeline** (Vitest missing) | **I** | Vitest step; invariant + audit-chain check after the E2E run; `e2e-object-storage` job. |
| **Worker/job reliability** (correlation id) | **I** | Request id in job meta → worker log context; worker now installs app logging and no longer logs recipients. |
| **Seed data** (no requests/emergencies/notifications) | **I** | API-driven, idempotent demo activity. |
| **Testing pass** (concurrency for new workflows, E2E journeys, axe + keyboard, responsive incl. donor mobile) | **J** | 4 new cross-workflow races + 3 deterministic locking tests (found and fixed a stale-row-under-lock lost update, invariant 46); 9 new E2E journeys/checks; notifications bell and account menu made real disclosures (Escape, `aria-expanded`); donor dashboard contrast/CSP/dead-control fixes. |
| **Performance** (EXPLAIN ANALYZE on hot queries) | **K** | Volume DB (127k units, 250k notifications, 251k events); 2 N+1s fixed (2,043→6 and 405→5 statements); 2 evidence-backed indexes; real-time publisher backlog + retention bugs fixed (invariant 47). |
| **Final documentation + readiness report** | **L** | `LIFELINK_FINAL_PRODUCTION_READINESS_REPORT.md`. |

Not in the A–L plan, carried as limitations: external audit anchoring/HMAC, per-unit shipment reconciliation, plasma post-thaw tracking.
