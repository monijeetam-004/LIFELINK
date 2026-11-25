# LifeLink — Screenshot Index

Every screenshot was taken from the running application during this QA round (no mock-ups). Section A: workflow evidence from `frontend/e2e/qa_evidence.spec.ts` (full local stack: real RQ worker, MailDev as the Mailpit stand-in, real ClamAV, S3 stand-in) — a screenshot is written only after the assertions before it passed. Section B: the route crawl (`frontend/scripts/qa-crawl.mjs`), every route at 375/768/1280 px plus authorization checks.

## A. Workflow evidence (50 screenshots)

| ID | Feature | Test | Expected | Actual | File | Result |
|---|---|---|---|---|---|---|
| W01 | Registration | Open the registration form | Role cards, name/email/password fields | Form rendered with all roles and fields | `screenshots/01-auth/auth_01_registration.png` | PASS |
| W02 | Registration | Submit a weak password | Server-side validation error shown; no account created | Error shown: "Value error, This password is too common and appears in known breach lists. Please choose " | `screenshots/01-auth/auth_02_validation_error.png` | PASS |
| W03 | Registration | Submit valid details | Account created; user sent to 'check your inbox' | Landed on /verify-email | `screenshots/01-auth/auth_03_registered_check_inbox.png` | PASS |
| W04 | Email verification enforcement | Unverified user opens /donor | Redirected to /verify-email; API returns 403 | Redirected; GET /notifications -> 403 | `screenshots/01-auth/auth_04_unverified_blocked.png` | PASS |
| W05 | Email (worker + SMTP) | Registration email delivered to the mail catcher | Subject 'Verify your LifeLink email' with a verify link | Received by MailDev (Mailpit stand-in) for qa.donor.1791319448139@example.com | `screenshots/15-email/email_01_verification_email_received.png` | PASS |
| W06 | Email verification | Follow the link from the email | 'Email verified' confirmation | Verified page shown | `screenshots/01-auth/auth_05_email_verified.png` | PASS |
| W07 | Login | Verified donor signs in | Donor dashboard | Landed on /donor | `screenshots/01-auth/auth_06_signed_in_dashboard.png` | PASS |
| W08 | Donor dashboard | Donor signs in | Dashboard with eligibility and requests | Rendered | `screenshots/02-donor/donor_01_dashboard.png` | PASS |
| W09 | Donation creation | Open the log-donation form | Date, facility, units, note | Form filled | `screenshots/02-donor/donor_02_donation_form.png` | PASS |
| W10 | Donation verification | Donation saved | Listed as Self-reported; no certificate offered | Self-reported badge, no certificate button | `screenshots/02-donor/donor_03_donation_submitted_self_reported.png` | PASS |
| W11 | Donation verification | Linked hospital opens its review queue | The donor's donation awaiting review | Row present with Verify/Reject | `screenshots/10-donation-verification/verification_01_hospital_queue.png` | PASS |
| W12 | Donation verification | Hospital verifies | Row leaves the queue | Queue updated | `screenshots/10-donation-verification/verification_02_verified_removed_from_queue.png` | PASS |
| W13 | Donation verification | Donor sees the outcome | Verified by facility; certificate offered | Badge updated | `screenshots/02-donor/donor_04_donation_verified.png` | PASS |
| W14 | Certificate | Get certificate | Certificate page with donor name and status | Verified Digital Copy | `screenshots/12-certificates/certificate_01_issued.png` | PASS |
| W15 | Certificate download | Download PDF link | 200 application/pdf, %PDF- header | PDF returned | `screenshots/12-certificates/certificate_02_pdf_download_ok.png` | PASS |
| W16 | Malware scanning | Upload a file carrying the EICAR signature | Refused with a security-scan message | "File failed a security scan" shown | `screenshots/11-documents/documents_01_infected_upload_refused.png` | PASS |
| W17 | Upload validation | Executable bytes declared as PDF | Refused (content does not match type) | Error: File content does not match the declared file type | `screenshots/11-documents/documents_02_wrong_type_refused.png` | PASS |
| W18 | Document upload | Upload a clean PDF | Submitted, awaiting review | Status shown | `screenshots/11-documents/documents_03_clean_upload_submitted.png` | PASS |
| W19 | Document review | Admin opens the request | Document marked clean, link available | Scan status: clean | `screenshots/11-documents/documents_04_admin_review_scan_clean.png` | PASS |
| W20 | Verification approval | Admin downloads the document and approves | Download 200; request APPROVED | Download 200; APPROVED | `screenshots/04-admin/admin_01_verification_approved.png` | PASS |
| W21 | Hospital dashboard | Hospital signs in | Command center | Rendered | `screenshots/03-hospital/hospital_01_dashboard.png` | PASS |
| W22 | Inventory | Open inventory console | Per-blood-group cards and per-component table | Rendered | `screenshots/05-inventory/inventory_01_console.png` | PASS |
| W23 | Inventory by component | Add stock dialog | Component selector (Whole blood, RBC, Plasma, Platelets) | Platelets chosen, 3 units | `screenshots/05-inventory/inventory_02_component_selector.png` | PASS |
| W24 | Add stock | Confirm add | Component table updates | Platelets row updated | `screenshots/05-inventory/inventory_03_after_add_platelets.png` | PASS |
| W25 | Reserve / release | Reserve 1 then release 1 | Both succeed; log shows them | Dialogs closed without error | `screenshots/05-inventory/inventory_04_after_reserve_release.png` | PASS |
| W26 | Inventory invariants | Release more than reserved | Visible error; nothing changes | Error: Cannot release 999 unit(s) -- only 0 are reserved | `screenshots/05-inventory/inventory_05_invalid_release_refused.png` | PASS |
| W27 | Blood request | Guided request form | Blood group, units, urgency | AB-, 2 units | `screenshots/06-blood-requests/request_01_guided_form.png` | PASS |
| W28 | Blood request | Submit | Request created and tracked | Submitted | `screenshots/06-blood-requests/request_02_created.png` | PASS |
| W29 | Partial fulfilment | Hospital allocates 1 of 2 | PARTIALLY_FULFILLED, 1/2 | 1/2 shown | `screenshots/07-fulfillment/fulfillment_01_hospital_partial.png` | PASS |
| W30 | Realtime | Patient page open, no reload | 1/2 appears live | Updated via SSE | `screenshots/14-realtime/realtime_01_patient_sees_partial_live.png` | PASS |
| W31 | Full fulfilment | Hospital allocates the last unit | Fulfilled, 2/2 | 2/2 in Fulfilled tab | `screenshots/07-fulfillment/fulfillment_02_patient_full.png` | PASS |
| W32 | Notifications | Open the bell after fulfilment | Fulfilment notifications listed | Panel open | `screenshots/13-notifications/notifications_01_panel.png` | PASS |
| W33 | Shipment creation | New shipment form | Destination, blood type, component, units | To Ahmedabad Blood Bank & Transfusion Centre, O-, RBC, 2 | `screenshots/08-shipment/shipment_01_create_form.png` | PASS |
| W34 | Shipment | Create | CREATED with reserved units | CREATED | `screenshots/08-shipment/shipment_02_created.png` | PASS |
| W35 | Transport | Dispatch, in transit, report delay | Timeline shows each step | IN TRANSIT with delay | `screenshots/08-shipment/shipment_03_in_transit_delayed.png` | PASS |
| W36 | Failed shipment | Shipment failed after dispatch | Reconciliation required; units not silently returned | Required shown | `screenshots/09-reconciliation/reconciliation_01_required.png` | PASS |
| W37 | Reconciliation | Record an attested outcome | Status Quarantined; panel gone | Quarantined | `screenshots/09-reconciliation/reconciliation_02_recorded.png` | PASS |
| W38 | Admin dashboard | Admin signs in | Platform KPIs | Rendered | `screenshots/04-admin/admin_02_overview.png` | PASS |
| W39 | User governance | Open /admin/users | Data table from the real API | Rendered | `screenshots/04-admin/admin_03_users.png` | PASS |
| W40 | Facilities | Open /admin/facilities | Data table from the real API | Rendered | `screenshots/04-admin/admin_04_facilities.png` | PASS |
| W41 | Shipments (admin) | Open /admin/shipments | Data table from the real API | Rendered | `screenshots/04-admin/admin_05_shipments.png` | PASS |
| W42 | Donor governance | Open /admin/donors | Data table from the real API | Rendered | `screenshots/04-admin/admin_06_donors.png` | PASS |
| W43 | Analytics | Open analytics | Breakdowns + certificate registry | Rendered | `screenshots/17-analytics/analytics_01_admin.png` | PASS |
| W44 | Audit chain | Verify chain | No evidence of tampering; not claimed immutable | Verified | `screenshots/04-admin/admin_07_audit_chain_verified.png` | PASS |
| W45 | Realtime status | Stream connected | 'Live' | Live | `screenshots/14-realtime/realtime_02_live.png` | PASS |
| W46 | Realtime status | API process stopped | 'Reconnecting…' | Reconnecting shown | `screenshots/14-realtime/realtime_03_reconnecting.png` | PASS |
| W47 | Realtime status | API back | 'Live' again, no reload | Live | `screenshots/14-realtime/realtime_04_live_after_reconnect.png` | PASS |
| W48 | Role isolation (UI) | Donor opens /admin/users | Access denied page, no admin data | Landed on /admin/users | `screenshots/18-security/security_01_wrong_role_denied.png` | PASS |
| W49 | Authentication (UI) | Anonymous opens /hospital/inventory | Sent to sign-in | Landed on /login | `screenshots/18-security/security_02_anonymous_redirected.png` | PASS |
| W50 | Session expiry | Session revoked server-side | Session-ended / sign-in page, no data | Landed on /login | `screenshots/18-security/security_03_session_ended.png` | PASS |

## B. Route crawl (183 page visits + 74 authorization checks)

PASS = page rendered, no horizontal overflow, no blank page, no console error and no failed API call other than blocked map tiles (external tile servers are unreachable in this environment). Axe = serious/critical violations at 1280 px (fixes for the last two were applied after this crawl and verified by build + unit tests; see the QA report).

| ID | Route | Role | Width | Overflow px | Console errors (non-tile) | Failed API calls | Axe | File | Result |
|---|---|---|---|---|---|---|---|---|---|
| C001 | `/` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/root__public__375.jpg` | PASS |
| C002 | `/` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/root__public__768.jpg` | PASS |
| C003 | `/` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/root__public__1280.jpg` | PASS |
| C004 | `/login` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/login__public__375.jpg` | PASS |
| C005 | `/login` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/login__public__768.jpg` | PASS |
| C006 | `/login` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/login__public__1280.jpg` | PASS |
| C007 | `/register` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/register__public__375.jpg` | PASS |
| C008 | `/register` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/register__public__768.jpg` | PASS |
| C009 | `/register` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/register__public__1280.jpg` | PASS |
| C010 | `/verify-email` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/verify-email__public__375.jpg` | PASS |
| C011 | `/verify-email` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/verify-email__public__768.jpg` | PASS |
| C012 | `/verify-email` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/verify-email__public__1280.jpg` | PASS |
| C013 | `/verify/:code` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/verify_code__public__375.jpg` | PASS |
| C014 | `/verify/:code` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/verify_code__public__768.jpg` | PASS |
| C015 | `/verify/:code` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/verify_code__public__1280.jpg` | PASS |
| C016 | `/forgot-password` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/forgot-password__public__375.jpg` | PASS |
| C017 | `/forgot-password` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/forgot-password__public__768.jpg` | PASS |
| C018 | `/forgot-password` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/forgot-password__public__1280.jpg` | PASS |
| C019 | `/reset-password` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/reset-password__public__375.jpg` | PASS |
| C020 | `/reset-password` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/reset-password__public__768.jpg` | PASS |
| C021 | `/reset-password` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/reset-password__public__1280.jpg` | PASS |
| C022 | `/dev-index` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/dev-index__DONOR__375.jpg` | PASS |
| C023 | `/dev-index` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/dev-index__DONOR__768.jpg` | PASS |
| C024 | `/dev-index` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/dev-index__DONOR__1280.jpg` | PASS |
| C025 | `/403` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/403__public__375.jpg` | PASS |
| C026 | `/403` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/403__public__768.jpg` | PASS |
| C027 | `/403` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/403__public__1280.jpg` | PASS |
| C028 | `/session-expired` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/session-expired__public__375.jpg` | PASS |
| C029 | `/session-expired` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/session-expired__public__768.jpg` | PASS |
| C030 | `/session-expired` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/session-expired__public__1280.jpg` | PASS |
| C031 | `/500` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/500__public__375.jpg` | PASS |
| C032 | `/500` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/500__public__768.jpg` | PASS |
| C033 | `/500` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/500__public__1280.jpg` | PASS |
| C034 | `/offline` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/offline__public__375.jpg` | PASS |
| C035 | `/offline` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/offline__public__768.jpg` | PASS |
| C036 | `/offline` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/offline__public__1280.jpg` | PASS |
| C037 | `/recommendation` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/recommendation__public__375.jpg` | PASS |
| C038 | `/recommendation` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/recommendation__public__768.jpg` | PASS |
| C039 | `/recommendation` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/recommendation__public__1280.jpg` | PASS |
| C040 | `/decision-transparency` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/decision-transparency__public__375.jpg` | PASS |
| C041 | `/decision-transparency` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/decision-transparency__public__768.jpg` | PASS |
| C042 | `/decision-transparency` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/decision-transparency__public__1280.jpg` | PASS |
| C043 | `/mathematical-model` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/mathematical-model__public__375.jpg` | PASS |
| C044 | `/mathematical-model` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/mathematical-model__public__768.jpg` | PASS |
| C045 | `/mathematical-model` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/mathematical-model__public__1280.jpg` | PASS |
| C046 | `/graph-engine` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/graph-engine__public__375.jpg` | PASS |
| C047 | `/graph-engine` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/graph-engine__public__768.jpg` | PASS |
| C048 | `/graph-engine` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/graph-engine__public__1280.jpg` | PASS |
| C049 | `/simulation` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/simulation__public__375.jpg` | PASS |
| C050 | `/simulation` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/simulation__public__768.jpg` | PASS |
| C051 | `/simulation` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/simulation__public__1280.jpg` | PASS |
| C052 | `/research-mode` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/research-mode__public__375.jpg` | PASS |
| C053 | `/research-mode` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/research-mode__public__768.jpg` | PASS |
| C054 | `/research-mode` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/research-mode__public__1280.jpg` | PASS |
| C055 | `/help` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/help__public__375.jpg` | PASS |
| C056 | `/help` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/help__public__768.jpg` | PASS |
| C057 | `/help` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/help__public__1280.jpg` | PASS |
| C058 | `/legal` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/legal__public__375.jpg` | PASS |
| C059 | `/legal` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/legal__public__768.jpg` | PASS |
| C060 | `/legal` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/legal__public__1280.jpg` | PASS |
| C061 | `/legal/:section` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/legal_section__public__375.jpg` | PASS |
| C062 | `/legal/:section` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/legal_section__public__768.jpg` | PASS |
| C063 | `/legal/:section` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/legal_section__public__1280.jpg` | PASS |
| C064 | `/patient` | PATIENT | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient__PATIENT__375.jpg` | PASS |
| C065 | `/patient` | PATIENT | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient__PATIENT__768.jpg` | PASS |
| C066 | `/patient` | PATIENT | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/patient__PATIENT__1280.jpg` | PASS |
| C067 | `/patient/find-donor` | PATIENT | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_find-donor__PATIENT__375.jpg` | PASS |
| C068 | `/patient/find-donor` | PATIENT | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_find-donor__PATIENT__768.jpg` | PASS |
| C069 | `/patient/find-donor` | PATIENT | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/patient_find-donor__PATIENT__1280.jpg` | PASS |
| C070 | `/patient/sos-tracking` | PATIENT | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_sos-tracking__PATIENT__375.jpg` | PASS |
| C071 | `/patient/sos-tracking` | PATIENT | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_sos-tracking__PATIENT__768.jpg` | PASS |
| C072 | `/patient/sos-tracking` | PATIENT | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/patient_sos-tracking__PATIENT__1280.jpg` | PASS |
| C073 | `/patient/new-request` | PATIENT | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_new-request__PATIENT__375.jpg` | PASS |
| C074 | `/patient/new-request` | PATIENT | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_new-request__PATIENT__768.jpg` | PASS |
| C075 | `/patient/new-request` | PATIENT | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/patient_new-request__PATIENT__1280.jpg` | PASS |
| C076 | `/patient/history` | PATIENT | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_history__PATIENT__375.jpg` | PASS |
| C077 | `/patient/history` | PATIENT | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/patient_history__PATIENT__768.jpg` | PASS |
| C078 | `/patient/history` | PATIENT | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/patient_history__PATIENT__1280.jpg` | PASS |
| C079 | `/family` | FAMILY | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/family__FAMILY__375.jpg` | PASS |
| C080 | `/family` | FAMILY | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/family__FAMILY__768.jpg` | PASS |
| C081 | `/family` | FAMILY | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/family__FAMILY__1280.jpg` | PASS |
| C082 | `/family/patients` | FAMILY | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/family_patients__FAMILY__375.jpg` | PASS |
| C083 | `/family/patients` | FAMILY | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/family_patients__FAMILY__768.jpg` | PASS |
| C084 | `/family/patients` | FAMILY | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/family_patients__FAMILY__1280.jpg` | PASS |
| C085 | `/family/patients/:patientId` | FAMILY | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/family_patients_patientId__FAMILY__375.jpg` | PASS |
| C086 | `/family/patients/:patientId` | FAMILY | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/family_patients_patientId__FAMILY__768.jpg` | PASS |
| C087 | `/family/patients/:patientId` | FAMILY | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/family_patients_patientId__FAMILY__1280.jpg` | PASS |
| C088 | `/donor` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor__DONOR__375.jpg` | PASS |
| C089 | `/donor` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor__DONOR__768.jpg` | PASS |
| C090 | `/donor` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/donor__DONOR__1280.jpg` | PASS |
| C091 | `/donor/mobile` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_mobile__DONOR__375.jpg` | PASS |
| C092 | `/donor/mobile` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_mobile__DONOR__768.jpg` | PASS |
| C093 | `/donor/mobile` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/donor_mobile__DONOR__1280.jpg` | PASS |
| C094 | `/donor/eligibility` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_eligibility__DONOR__375.jpg` | PASS |
| C095 | `/donor/eligibility` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_eligibility__DONOR__768.jpg` | PASS |
| C096 | `/donor/eligibility` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/donor_eligibility__DONOR__1280.jpg` | PASS |
| C097 | `/donor/ready-console` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_ready-console__DONOR__375.jpg` | PASS |
| C098 | `/donor/ready-console` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_ready-console__DONOR__768.jpg` | PASS |
| C099 | `/donor/ready-console` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/donor_ready-console__DONOR__1280.jpg` | PASS |
| C100 | `/donor/milestones` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_milestones__DONOR__375.jpg` | PASS |
| C101 | `/donor/milestones` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_milestones__DONOR__768.jpg` | PASS |
| C102 | `/donor/milestones` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/donor_milestones__DONOR__1280.jpg` | PASS |
| C103 | `/donor/identity-verification` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_identity-verification__DONOR__375.jpg` | PASS |
| C104 | `/donor/identity-verification` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/donor_identity-verification__DONOR__768.jpg` | PASS |
| C105 | `/donor/identity-verification` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/donor_identity-verification__DONOR__1280.jpg` | PASS |
| C106 | `/hospital` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital__HOSPITAL__375.jpg` | PASS |
| C107 | `/hospital` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital__HOSPITAL__768.jpg` | PASS |
| C108 | `/hospital` | HOSPITAL | 1280 | 0 | 0 | 0 | 1 | `screenshots/19-responsive/hospital__HOSPITAL__1280.jpg` | PASS |
| C109 | `/hospital/inventory` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_inventory__HOSPITAL__375.jpg` | PASS |
| C110 | `/hospital/inventory` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_inventory__HOSPITAL__768.jpg` | PASS |
| C111 | `/hospital/inventory` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_inventory__HOSPITAL__1280.jpg` | PASS |
| C112 | `/hospital/donors` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_donors__HOSPITAL__375.jpg` | PASS |
| C113 | `/hospital/donors` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_donors__HOSPITAL__768.jpg` | PASS |
| C114 | `/hospital/donors` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_donors__HOSPITAL__1280.jpg` | PASS |
| C115 | `/hospital/requests/:requestId` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_requests_requestId__HOSPITAL__375.jpg` | PASS |
| C116 | `/hospital/requests/:requestId` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_requests_requestId__HOSPITAL__768.jpg` | PASS |
| C117 | `/hospital/requests/:requestId` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_requests_requestId__HOSPITAL__1280.jpg` | PASS |
| C118 | `/hospital/analytics-mobile` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_analytics-mobile__HOSPITAL__375.jpg` | PASS |
| C119 | `/hospital/analytics-mobile` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_analytics-mobile__HOSPITAL__768.jpg` | PASS |
| C120 | `/hospital/analytics-mobile` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_analytics-mobile__HOSPITAL__1280.jpg` | PASS |
| C121 | `/hospital/resource-pathways` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_resource-pathways__HOSPITAL__375.jpg` | PASS |
| C122 | `/hospital/resource-pathways` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_resource-pathways__HOSPITAL__768.jpg` | PASS |
| C123 | `/hospital/resource-pathways` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_resource-pathways__HOSPITAL__1280.jpg` | PASS |
| C124 | `/hospital/shipments` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_shipments__HOSPITAL__375.jpg` | PASS |
| C125 | `/hospital/shipments` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_shipments__HOSPITAL__768.jpg` | PASS |
| C126 | `/hospital/shipments` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_shipments__HOSPITAL__1280.jpg` | PASS |
| C127 | `/hospital/verification` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_verification__HOSPITAL__375.jpg` | PASS |
| C128 | `/hospital/verification` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_verification__HOSPITAL__768.jpg` | PASS |
| C129 | `/hospital/verification` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_verification__HOSPITAL__1280.jpg` | PASS |
| C130 | `/hospital/donation-reviews` | HOSPITAL | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_donation-reviews__HOSPITAL__375.jpg` | PASS |
| C131 | `/hospital/donation-reviews` | HOSPITAL | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/hospital_donation-reviews__HOSPITAL__768.jpg` | PASS |
| C132 | `/hospital/donation-reviews` | HOSPITAL | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/hospital_donation-reviews__HOSPITAL__1280.jpg` | PASS |
| C133 | `/admin` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin__ADMIN__375.jpg` | PASS |
| C134 | `/admin` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin__ADMIN__768.jpg` | PASS |
| C135 | `/admin` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin__ADMIN__1280.jpg` | PASS |
| C136 | `/admin/verifications` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_verifications__ADMIN__375.jpg` | PASS |
| C137 | `/admin/verifications` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_verifications__ADMIN__768.jpg` | PASS |
| C138 | `/admin/verifications` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_verifications__ADMIN__1280.jpg` | PASS |
| C139 | `/admin/users` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_users__ADMIN__375.jpg` | PASS |
| C140 | `/admin/users` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_users__ADMIN__768.jpg` | PASS |
| C141 | `/admin/users` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_users__ADMIN__1280.jpg` | PASS |
| C142 | `/admin/analytics` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_analytics__ADMIN__375.jpg` | PASS |
| C143 | `/admin/analytics` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_analytics__ADMIN__768.jpg` | PASS |
| C144 | `/admin/analytics` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_analytics__ADMIN__1280.jpg` | PASS |
| C145 | `/admin/transport-routes` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_transport-routes__ADMIN__375.jpg` | PASS |
| C146 | `/admin/transport-routes` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_transport-routes__ADMIN__768.jpg` | PASS |
| C147 | `/admin/transport-routes` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_transport-routes__ADMIN__1280.jpg` | PASS |
| C148 | `/admin/shipments` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_shipments__ADMIN__375.jpg` | PASS |
| C149 | `/admin/shipments` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_shipments__ADMIN__768.jpg` | PASS |
| C150 | `/admin/shipments` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_shipments__ADMIN__1280.jpg` | PASS |
| C151 | `/admin/facilities` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_facilities__ADMIN__375.jpg` | PASS |
| C152 | `/admin/facilities` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_facilities__ADMIN__768.jpg` | PASS |
| C153 | `/admin/facilities` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_facilities__ADMIN__1280.jpg` | PASS |
| C154 | `/admin/donors` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_donors__ADMIN__375.jpg` | PASS |
| C155 | `/admin/donors` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_donors__ADMIN__768.jpg` | PASS |
| C156 | `/admin/donors` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_donors__ADMIN__1280.jpg` | PASS |
| C157 | `/admin/audit-feed` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_audit-feed__ADMIN__375.jpg` | PASS |
| C158 | `/admin/audit-feed` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_audit-feed__ADMIN__768.jpg` | PASS |
| C159 | `/admin/audit-feed` | ADMIN | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_audit-feed__ADMIN__1280.jpg` | PASS |
| C160 | `/admin/verification` | public | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_verification__public__375.jpg` | PASS |
| C161 | `/admin/verification` | public | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/admin_verification__public__768.jpg` | PASS |
| C162 | `/admin/verification` | public | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/admin_verification__public__1280.jpg` | PASS |
| C163 | `/analytics` | ADMIN | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/analytics__ADMIN__375.jpg` | PASS |
| C164 | `/analytics` | ADMIN | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/analytics__ADMIN__768.jpg` | PASS |
| C165 | `/analytics` | ADMIN | 1280 | 0 | 0 | 0 | 1 | `screenshots/19-responsive/analytics__ADMIN__1280.jpg` | PASS |
| C166 | `/certificate` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/certificate__DONOR__375.jpg` | PASS |
| C167 | `/certificate` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/certificate__DONOR__768.jpg` | PASS |
| C168 | `/certificate` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/certificate__DONOR__1280.jpg` | PASS |
| C169 | `/certificate/:id` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/certificate_id__DONOR__375.jpg` | PASS |
| C170 | `/certificate/:id` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/certificate_id__DONOR__768.jpg` | PASS |
| C171 | `/certificate/:id` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/certificate_id__DONOR__1280.jpg` | PASS |
| C172 | `/matching/strategy-lab` | PATIENT | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/matching_strategy-lab__PATIENT__375.jpg` | PASS |
| C173 | `/matching/strategy-lab` | PATIENT | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/matching_strategy-lab__PATIENT__768.jpg` | PASS |
| C174 | `/matching/strategy-lab` | PATIENT | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/matching_strategy-lab__PATIENT__1280.jpg` | PASS |
| C175 | `/profile` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/profile__DONOR__375.jpg` | PASS |
| C176 | `/profile` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/profile__DONOR__768.jpg` | PASS |
| C177 | `/profile` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/profile__DONOR__1280.jpg` | PASS |
| C178 | `/settings` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/settings__DONOR__375.jpg` | PASS |
| C179 | `/settings` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/settings__DONOR__768.jpg` | PASS |
| C180 | `/settings` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/settings__DONOR__1280.jpg` | PASS |
| C181 | `/notifications` | DONOR | 375 | 0 | 0 | 0 | - | `screenshots/19-responsive/notifications__DONOR__375.jpg` | PASS |
| C182 | `/notifications` | DONOR | 768 | 0 | 0 | 0 | - | `screenshots/19-responsive/notifications__DONOR__768.jpg` | PASS |
| C183 | `/notifications` | DONOR | 1280 | 0 | 0 | 0 | 0 | `screenshots/19-responsive/notifications__DONOR__1280.jpg` | PASS |

### Authorization (UI)

| ID | Route | As | Landed on | Not rendered for this caller | File |
|---|---|---|---|---|---|
| Z01 | `/dev-index` | anonymous | `/dev-index` | PASS (route removed in production build: 'Page not found') | `screenshots/19-responsive/authz_dev-index__anonymous.jpg` |
| Z02 | `/patient` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_patient__anonymous.jpg` |
| Z03 | `/patient` | FAMILY | `/patient` | PASS | `screenshots/19-responsive/authz_patient__FAMILY.jpg` |
| Z04 | `/patient/find-donor` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_patient_find-donor__anonymous.jpg` |
| Z05 | `/patient/find-donor` | FAMILY | `/patient/find-donor` | PASS | `screenshots/19-responsive/authz_patient_find-donor__FAMILY.jpg` |
| Z06 | `/patient/sos-tracking` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_patient_sos-tracking__anonymous.jpg` |
| Z07 | `/patient/sos-tracking` | FAMILY | `/patient/sos-tracking` | PASS | `screenshots/19-responsive/authz_patient_sos-tracking__FAMILY.jpg` |
| Z08 | `/patient/new-request` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_patient_new-request__anonymous.jpg` |
| Z09 | `/patient/new-request` | FAMILY | `/patient/new-request` | PASS | `screenshots/19-responsive/authz_patient_new-request__FAMILY.jpg` |
| Z10 | `/patient/history` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_patient_history__anonymous.jpg` |
| Z11 | `/patient/history` | FAMILY | `/patient/history` | PASS | `screenshots/19-responsive/authz_patient_history__FAMILY.jpg` |
| Z12 | `/family` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_family__anonymous.jpg` |
| Z13 | `/family` | PATIENT | `/family` | PASS | `screenshots/19-responsive/authz_family__PATIENT.jpg` |
| Z14 | `/family/patients` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_family_patients__anonymous.jpg` |
| Z15 | `/family/patients` | PATIENT | `/family/patients` | PASS | `screenshots/19-responsive/authz_family_patients__PATIENT.jpg` |
| Z16 | `/family/patients/:patientId` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_family_patients_patientId__anonymous.jpg` |
| Z17 | `/family/patients/:patientId` | PATIENT | `/family/patients/68d37a70-683a-4ae0-8270-7e52ce985f88` | PASS | `screenshots/19-responsive/authz_family_patients_patientId__PATIENT.jpg` |
| Z18 | `/donor` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_donor__anonymous.jpg` |
| Z19 | `/donor` | PATIENT | `/donor` | PASS | `screenshots/19-responsive/authz_donor__PATIENT.jpg` |
| Z20 | `/donor/mobile` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_donor_mobile__anonymous.jpg` |
| Z21 | `/donor/mobile` | PATIENT | `/donor/mobile` | PASS | `screenshots/19-responsive/authz_donor_mobile__PATIENT.jpg` |
| Z22 | `/donor/eligibility` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_donor_eligibility__anonymous.jpg` |
| Z23 | `/donor/eligibility` | PATIENT | `/donor/eligibility` | PASS | `screenshots/19-responsive/authz_donor_eligibility__PATIENT.jpg` |
| Z24 | `/donor/ready-console` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_donor_ready-console__anonymous.jpg` |
| Z25 | `/donor/ready-console` | PATIENT | `/donor/ready-console` | PASS | `screenshots/19-responsive/authz_donor_ready-console__PATIENT.jpg` |
| Z26 | `/donor/milestones` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_donor_milestones__anonymous.jpg` |
| Z27 | `/donor/milestones` | PATIENT | `/donor/milestones` | PASS | `screenshots/19-responsive/authz_donor_milestones__PATIENT.jpg` |
| Z28 | `/donor/identity-verification` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_donor_identity-verification__anonymous.jpg` |
| Z29 | `/donor/identity-verification` | PATIENT | `/donor/identity-verification` | PASS | `screenshots/19-responsive/authz_donor_identity-verification__PATIENT.jpg` |
| Z30 | `/hospital` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital__anonymous.jpg` |
| Z31 | `/hospital` | PATIENT | `/hospital` | PASS | `screenshots/19-responsive/authz_hospital__PATIENT.jpg` |
| Z32 | `/hospital/inventory` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_inventory__anonymous.jpg` |
| Z33 | `/hospital/inventory` | PATIENT | `/hospital/inventory` | PASS | `screenshots/19-responsive/authz_hospital_inventory__PATIENT.jpg` |
| Z34 | `/hospital/donors` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_donors__anonymous.jpg` |
| Z35 | `/hospital/donors` | PATIENT | `/hospital/donors` | PASS | `screenshots/19-responsive/authz_hospital_donors__PATIENT.jpg` |
| Z36 | `/hospital/requests/:requestId` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_requests_requestId__anonymous.jpg` |
| Z37 | `/hospital/requests/:requestId` | PATIENT | `/hospital/requests/ab1aeb77-58b6-400f-b3a1-5d120844b6f2` | PASS | `screenshots/19-responsive/authz_hospital_requests_requestId__PATIENT.jpg` |
| Z38 | `/hospital/analytics-mobile` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_analytics-mobile__anonymous.jpg` |
| Z39 | `/hospital/analytics-mobile` | PATIENT | `/hospital/analytics-mobile` | PASS | `screenshots/19-responsive/authz_hospital_analytics-mobile__PATIENT.jpg` |
| Z40 | `/hospital/resource-pathways` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_resource-pathways__anonymous.jpg` |
| Z41 | `/hospital/resource-pathways` | PATIENT | `/hospital/resource-pathways` | PASS | `screenshots/19-responsive/authz_hospital_resource-pathways__PATIENT.jpg` |
| Z42 | `/hospital/shipments` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_shipments__anonymous.jpg` |
| Z43 | `/hospital/shipments` | PATIENT | `/hospital/shipments` | PASS | `screenshots/19-responsive/authz_hospital_shipments__PATIENT.jpg` |
| Z44 | `/hospital/verification` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_verification__anonymous.jpg` |
| Z45 | `/hospital/verification` | PATIENT | `/hospital/verification` | PASS | `screenshots/19-responsive/authz_hospital_verification__PATIENT.jpg` |
| Z46 | `/hospital/donation-reviews` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_hospital_donation-reviews__anonymous.jpg` |
| Z47 | `/hospital/donation-reviews` | PATIENT | `/hospital/donation-reviews` | PASS | `screenshots/19-responsive/authz_hospital_donation-reviews__PATIENT.jpg` |
| Z48 | `/admin` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin__anonymous.jpg` |
| Z49 | `/admin` | PATIENT | `/admin` | PASS | `screenshots/19-responsive/authz_admin__PATIENT.jpg` |
| Z50 | `/admin/verifications` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_verifications__anonymous.jpg` |
| Z51 | `/admin/verifications` | PATIENT | `/admin/verifications` | PASS | `screenshots/19-responsive/authz_admin_verifications__PATIENT.jpg` |
| Z52 | `/admin/users` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_users__anonymous.jpg` |
| Z53 | `/admin/users` | PATIENT | `/admin/users` | PASS | `screenshots/19-responsive/authz_admin_users__PATIENT.jpg` |
| Z54 | `/admin/analytics` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_analytics__anonymous.jpg` |
| Z55 | `/admin/analytics` | PATIENT | `/admin/analytics` | PASS | `screenshots/19-responsive/authz_admin_analytics__PATIENT.jpg` |
| Z56 | `/admin/transport-routes` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_transport-routes__anonymous.jpg` |
| Z57 | `/admin/transport-routes` | PATIENT | `/admin/transport-routes` | PASS | `screenshots/19-responsive/authz_admin_transport-routes__PATIENT.jpg` |
| Z58 | `/admin/shipments` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_shipments__anonymous.jpg` |
| Z59 | `/admin/shipments` | PATIENT | `/admin/shipments` | PASS | `screenshots/19-responsive/authz_admin_shipments__PATIENT.jpg` |
| Z60 | `/admin/facilities` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_facilities__anonymous.jpg` |
| Z61 | `/admin/facilities` | PATIENT | `/admin/facilities` | PASS | `screenshots/19-responsive/authz_admin_facilities__PATIENT.jpg` |
| Z62 | `/admin/donors` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_donors__anonymous.jpg` |
| Z63 | `/admin/donors` | PATIENT | `/admin/donors` | PASS | `screenshots/19-responsive/authz_admin_donors__PATIENT.jpg` |
| Z64 | `/admin/audit-feed` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_admin_audit-feed__anonymous.jpg` |
| Z65 | `/admin/audit-feed` | PATIENT | `/admin/audit-feed` | PASS | `screenshots/19-responsive/authz_admin_audit-feed__PATIENT.jpg` |
| Z66 | `/analytics` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_analytics__anonymous.jpg` |
| Z67 | `/analytics` | PATIENT | `/analytics` | PASS | `screenshots/19-responsive/authz_analytics__PATIENT.jpg` |
| Z68 | `/certificate` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_certificate__anonymous.jpg` |
| Z69 | `/certificate/:id` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_certificate_id__anonymous.jpg` |
| Z70 | `/matching/strategy-lab` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_matching_strategy-lab__anonymous.jpg` |
| Z71 | `/matching/strategy-lab` | DONOR | `/matching/strategy-lab` | PASS | `screenshots/19-responsive/authz_matching_strategy-lab__DONOR.jpg` |
| Z72 | `/profile` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_profile__anonymous.jpg` |
| Z73 | `/settings` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_settings__anonymous.jpg` |
| Z74 | `/notifications` | anonymous | `/login` | PASS | `screenshots/19-responsive/authz_notifications__anonymous.jpg` |
