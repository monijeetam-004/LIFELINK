# Screenshot index (QA round 2026-10-20)

Every screenshot below was taken from the running application. There are no mock-ups, and no screenshot of a stubbed page is used as evidence of a real integration.

## A. Workflow screenshots

These were produced by `frontend/e2e/qa_evidence.spec.ts` (9 tests) and `frontend/e2e/qa_failover_ui.spec.ts` (1 test).

**Stack.** The services were real: PostgreSQL, Redis, an RQ worker with `RQ_SYNC=false`, Mailpit, MinIO and clamd.

**When a screenshot is taken.** A screenshot is written only **after** the assertions before it have passed, so a failing step produces no image.

**Run logs.** `workflows/playwright_qa_evidence_run.log` (9/9 passed) and `workflows/playwright_qa_failover_run.log` (1/1 passed). Both specs passed again in the final CI-identical run.

**Personal data.** All accounts are synthetic (`@example.com`, `@lifelink-demo.example.com`). No verification token, session cookie or password appears in any image.

| ID | Audit area | Feature | Test | Expected | Actual | File | Result |
|---|---|---|---|---|---|---|---|
| W01 | authentication | Registration | Open the registration form | Role cards, name/email/password fields | Form rendered with all roles and fields | `evidence/workflows/01-auth/auth_01_registration.png` | PASS |
| W02 | authentication | Registration | Submit a weak password | Server-side validation error shown; no account created | Error shown: "Value error, This password is too common and appears in known breach lists. Please choose " | `evidence/workflows/01-auth/auth_02_validation_error.png` | PASS |
| W03 | authentication | Registration | Submit valid details | Account created; user sent to 'check your inbox' | Landed on /verify-email | `evidence/workflows/01-auth/auth_03_registered_check_inbox.png` | PASS |
| W04 | authentication | Email verification enforcement | Unverified user opens /donor | Redirected to /verify-email; API returns 403 | Redirected; GET /notifications -> 403 | `evidence/workflows/01-auth/auth_04_unverified_blocked.png` | PASS |
| W05 | integrations (real Mailpit) | Email (worker + SMTP) | Registration email delivered to the mail server | Subject 'Verify your LifeLink email' with a verify link | Received by the real Mailpit v1.21.8 (sent by the RQ worker) | `evidence/workflows/15-email/email_01_verification_email_received.png` | PASS |
| W06 | authentication | Email verification | Follow the link from the email | 'Email verified' confirmation | Verified page shown | `evidence/workflows/01-auth/auth_05_email_verified.png` | PASS |
| W07 | authentication | Login | Verified donor signs in | Donor dashboard | Landed on /donor | `evidence/workflows/01-auth/auth_06_signed_in_dashboard.png` | PASS |
| W08 | donor | Donor dashboard | Donor signs in | Dashboard with eligibility and requests | Rendered | `evidence/workflows/02-donor/donor_01_dashboard.png` | PASS |
| W09 | donor | Donation creation | Open the log-donation form | Date, facility, units, note | Form filled | `evidence/workflows/02-donor/donor_02_donation_form.png` | PASS |
| W10 | donor | Donation verification | Donation saved | Listed as Self-reported; no certificate offered | Self-reported badge, no certificate button | `evidence/workflows/02-donor/donor_03_donation_submitted_self_reported.png` | PASS |
| W11 | donor / hospital | Donation verification | Linked hospital opens its review queue | The donor's donation awaiting review | Row present with Verify/Reject | `evidence/workflows/10-donation-verification/verification_01_hospital_queue.png` | PASS |
| W12 | donor / hospital | Donation verification | Hospital verifies | Row leaves the queue | Queue updated | `evidence/workflows/10-donation-verification/verification_02_verified_removed_from_queue.png` | PASS |
| W13 | donor | Donation verification | Donor sees the outcome | Verified by facility; certificate offered | Badge updated | `evidence/workflows/02-donor/donor_04_donation_verified.png` | PASS |
| W14 | donor | Certificate | Get certificate | Certificate page with donor name and status | Verified Digital Copy | `evidence/workflows/12-certificates/certificate_01_issued.png` | PASS |
| W15 | donor | Certificate download | Download PDF link | 200 application/pdf, %PDF- header | PDF returned | `evidence/workflows/12-certificates/certificate_02_pdf_download_ok.png` | PASS |
| W16 | security (uploads) | Malware scanning | Upload a file carrying the EICAR signature | Refused with a security-scan message | "File failed a security scan" shown | `evidence/workflows/11-documents/documents_01_infected_upload_refused.png` | PASS |
| W17 | security (uploads) | Upload validation | Executable bytes declared as PDF | Refused (content does not match type) | Error: error
File content does not match the declared file type | `evidence/workflows/11-documents/documents_02_wrong_type_refused.png` | PASS |
| W18 | security (uploads) | Document upload | Upload a clean PDF | Submitted, awaiting review | Status shown | `evidence/workflows/11-documents/documents_03_clean_upload_submitted.png` | PASS |
| W19 | security (uploads) | Document review | Admin opens the request | Document marked clean, link available | Scan status: clean | `evidence/workflows/11-documents/documents_04_admin_review_scan_clean.png` | PASS |
| W20 | administration | Verification approval | Admin downloads the document and approves | Download 200; request APPROVED | Download 200; APPROVED | `evidence/workflows/04-admin/admin_01_verification_approved.png` | PASS |
| W21 | hospital | Hospital dashboard | Hospital signs in | Command center | Rendered | `evidence/workflows/03-hospital/hospital_01_dashboard.png` | PASS |
| W22 | inventory | Inventory | Open inventory console | Per-blood-group cards and per-component table | Rendered | `evidence/workflows/05-inventory/inventory_01_console.png` | PASS |
| W23 | inventory | Inventory by component | Add stock dialog | Component selector (Whole blood, RBC, Plasma, Platelets) | Platelets chosen, 3 units | `evidence/workflows/05-inventory/inventory_02_component_selector.png` | PASS |
| W24 | inventory | Add stock | Confirm add | Component table updates | Platelets row updated | `evidence/workflows/05-inventory/inventory_03_after_add_platelets.png` | PASS |
| W25 | inventory | Reserve / release | Reserve 1 then release 1 | Both succeed; log shows them | Dialogs closed without error | `evidence/workflows/05-inventory/inventory_04_after_reserve_release.png` | PASS |
| W26 | inventory | Inventory invariants | Release more than reserved | Visible error; nothing changes | Error: Cannot release 999 unit(s) -- only 0 are reserved | `evidence/workflows/05-inventory/inventory_05_invalid_release_refused.png` | PASS |
| W27 | patient | Blood request | Guided request form | Blood group, units, urgency | AB-, 2 units | `evidence/workflows/06-blood-requests/request_01_guided_form.png` | PASS |
| W28 | patient | Blood request | Submit | Request created and tracked | Submitted | `evidence/workflows/06-blood-requests/request_02_created.png` | PASS |
| W29 | hospital / patient | Partial fulfilment | Hospital allocates 1 of 2 | PARTIALLY_FULFILLED, 1/2 | 1/2 shown | `evidence/workflows/07-fulfillment/fulfillment_01_hospital_partial.png` | PASS |
| W30 | failure handling | Realtime | Patient page open, no reload | 1/2 appears live | Updated via SSE | `evidence/workflows/14-realtime/realtime_01_patient_sees_partial_live.png` | PASS |
| W31 | hospital / patient | Full fulfilment | Hospital allocates the last unit | Fulfilled, 2/2 | 2/2 in Fulfilled tab | `evidence/workflows/07-fulfillment/fulfillment_02_patient_full.png` | PASS |
| W32 | all roles | Notifications | Open the bell after fulfilment | Fulfilment notifications listed | Panel open | `evidence/workflows/13-notifications/notifications_01_panel.png` | PASS |
| W33 | shipments / cold chain | Shipment creation | New shipment form | Destination, blood type, component, units | To Ahmedabad Blood Bank & Transfusion Centre, O-, RBC, 2 | `evidence/workflows/08-shipment/shipment_01_create_form.png` | PASS |
| W34 | shipments / cold chain | Shipment | Create | CREATED with reserved units | CREATED | `evidence/workflows/08-shipment/shipment_02_created.png` | PASS |
| W35 | shipments / cold chain | Transport | Dispatch, in transit, report delay | Timeline shows each step | IN TRANSIT with delay | `evidence/workflows/08-shipment/shipment_03_in_transit_delayed.png` | PASS |
| W36 | shipments | Failed shipment | Shipment failed after dispatch | Reconciliation required; units not silently returned | Required shown | `evidence/workflows/09-reconciliation/reconciliation_01_required.png` | PASS |
| W37 | shipments | Reconciliation | Record an attested outcome | Status Quarantined; panel gone | Quarantined | `evidence/workflows/09-reconciliation/reconciliation_02_recorded.png` | PASS |
| W38 | administration | Admin dashboard | Admin signs in | Platform KPIs | Rendered | `evidence/workflows/04-admin/admin_02_overview.png` | PASS |
| W39 | administration | User governance | Open /admin/users | Data table from the real API | Rendered | `evidence/workflows/04-admin/admin_03_users.png` | PASS |
| W40 | administration | Facilities | Open /admin/facilities | Data table from the real API | Rendered | `evidence/workflows/04-admin/admin_04_facilities.png` | PASS |
| W41 | administration | Shipments (admin) | Open /admin/shipments | Data table from the real API | Rendered | `evidence/workflows/04-admin/admin_05_shipments.png` | PASS |
| W42 | administration | Donor governance | Open /admin/donors | Data table from the real API | Rendered | `evidence/workflows/04-admin/admin_06_donors.png` | PASS |
| W43 | administration | Analytics | Open analytics | Breakdowns + certificate registry | Rendered | `evidence/workflows/17-analytics/analytics_01_admin.png` | PASS |
| W44 | administration | Audit chain | Verify chain | No evidence of tampering; not claimed immutable | Verified | `evidence/workflows/04-admin/admin_07_audit_chain_verified.png` | PASS |
| W45 | failure handling | Realtime status | Stream connected | 'Live' | Live | `evidence/workflows/14-realtime/realtime_02_live.png` | PASS |
| W46 | security | Role isolation (UI) | Donor opens /admin/users | Access denied page, no admin data | Landed on /admin/users | `evidence/workflows/18-security/security_01_wrong_role_denied.png` | PASS |
| W47 | security | Authentication (UI) | Anonymous opens /hospital/inventory | Sent to sign-in | Landed on /login | `evidence/workflows/18-security/security_02_anonymous_redirected.png` | PASS |
| W48 | security | Session expiry | Session revoked server-side | Session-ended / sign-in page, no data | Landed on /login | `evidence/workflows/18-security/security_03_session_ended.png` | PASS |
| W49 | route failover | Route failover | Hospital plans the emergency in Decision Transparency | An ACTIVE plan and at least one BACKUP | ACTIVE v1 from Mumbai Blood Bank & Transfusion Centre; 3 backup(s) | `evidence/workflows/16-route-failover/failover_01_plan_with_backups.png` | PASS |
| W50 | route failover | Route failover | Admin disrupts the route the ACTIVE plan uses | Route marked DISRUPTED with the reason | Route Mumbai Blood Bank & Transfusion Centre – LifeLink Demo Hospital disrupted | `evidence/workflows/16-route-failover/failover_02_route_disrupted_by_admin.png` | PASS |
| W51 | route failover | Route failover | Hospital reopens the plan after the failure | Old plan INVALIDATED; a new ACTIVE plan (re-validated backup, or a re-plan) that avoids the failed route | v1 INVALIDATED; v5 ACTIVE, re-planned (persisted state and timeline checked through the API) | `evidence/workflows/16-route-failover/failover_03_backup_active_old_invalidated.png` | PASS |

## B. Emergency planning and live research pages

These were produced by `frontend/e2e/gap_live_pages.spec.ts` in the final CI-identical run, after its assertions passed:

* the planner really ran;
* the result headings were visible;
* there was no horizontal overflow;
* there were 0 axe-core WCAG 2.1 A/AA violations (log: `evidence/accessibility/axe_gap_pages_final_run.json`, 33 audits, 0 violations).

| Page | Widths | Files |
|---|---|---|
| `/decision-transparency` | 375, 768, 1280 px | `evidence/emergency-planning/gap-decision-transparency-{375,768,1280}.png` |
| `/recommendation` | 375, 768, 1280 px | `evidence/emergency-planning/gap-donor-recommendation-{375,768,1280}.png` |
| `/graph-engine` | 375, 768, 1280 px | `evidence/emergency-planning/gap-graph-engine-{375,768,1280}.png` |
| `/mathematical-model` | 375, 768, 1280 px | `evidence/emergency-planning/gap-mathematical-model-{375,768,1280}.png` |
| `/research-mode` | 375, 768, 1280 px | `evidence/emergency-planning/gap-research-mode-{375,768,1280}.png` |
| `/simulation` | 375, 768, 1280 px | `evidence/emergency-planning/gap-simulation-{375,768,1280}.png` |

## C. Route crawl screenshots

`frontend/scripts/qa-crawl.mjs` covered every route of `src/App.tsx` at 375, 768 and 1280 px, signed in as an allowed role: 186 page visits. It also ran 75 authorisation checks (each guarded route anonymously and as a wrong role, 75/75 denied).

**Files:**

* page visits: `evidence/frontend-routes/shots/<route>__<role>__<width>.jpg`;
* authorisation checks: `…/shots/authz_<route>__<role>.jpg`;
* re-check after fix D5: `…/recheck_after_fixes/shots/`;
* results: `evidence/frontend-routes/crawl-results-final.json`.

## D. Other visual evidence

* **Containerised frontend.** The Docker clean start (`evidence/clean-start/docker/docker_clean_start_run.log`) ran `container_frontend.spec.ts` and `clean_start.spec.ts` against the nginx image: 2/2 passed, with 0 page or console errors.
* **Earlier rounds.** Earlier screenshots are kept unchanged in `docs/final-verification/evidence/external/*/` and `docs/QA/earlier_round/`.
