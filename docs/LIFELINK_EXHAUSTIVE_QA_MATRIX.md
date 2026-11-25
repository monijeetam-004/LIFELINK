> **Superseded (2026-10-12):** the counts in this report (1,270 tests, 141 operations) describe the build at that date. The gap-elimination round added features, tests and 19 API operations (160 in total). Current results: `docs/final-verification/FINAL_TEST_REPORT.md` and `API_COVERAGE.md`.

# LifeLink — Exhaustive QA Matrix

Statuses: **PASS** (tested here, passed) · **FIXED** (defect found here, fixed, regression test added, re-tested PASS) · **BLOCKED** (cannot be executed in this environment — reason given) · **OUT OF SCOPE** (deliberately not part of the product). No item is left NOT TESTED.

Inventory source: the live route table (140 API operations, `docs/QA/evidence/` — enumerated from `app.main`), the 62 frontend routes in `src/App.tsx`, the models/services/jobs in `backend/app`. Evidence keys: `T:` test file(s) · `S:` screenshot ID in `docs/QA/SCREENSHOT_INDEX.md` · `E:` evidence file in `docs/QA/evidence/`.

## A. Authentication
| Item | Status | Evidence |
|---|---|---|
| Registration (UI + API) | PASS | T: test_auth_flow, test_qa_email · S: W01–W03 |
| Registration validation error (weak password) | PASS | S: W02 |
| Same email registered twice concurrently | **FIXED** (was HTTP 500 → now one account, others 409/429) | T: test_qa_concurrency |
| Self-assigning privileged role at registration | PASS (422) | T: test_qa_attack_paths |
| Login / logout / session persistence | PASS | T: test_auth_flow, test_phase23_security_hardening |
| Logout & password change kill server-side sessions | PASS | T: test_qa_attack_paths, test_auth_hardening_phase7 |
| Session expiry / session revoked in browser | PASS | S: W50 |
| Forged / tampered session cookies | PASS (401) | T: test_qa_attack_paths |
| Password reset / OTP lockout | PASS | T: test_auth_hardening_phase7, test_phase25_qa_hardening |
| Password change | PASS | T: test_auth_flow |
| Email verification enforced server-side | PASS | T: test_hardening_email_verification · S: W04 |
| Verification via the real email link | PASS | T: test_qa_email (real SMTP + RQ worker) · S: W05–W07 |
| Resend verification (supersedes old link) | PASS | T: test_qa_email |
| Invalid / expired / reused token | PASS | T: test_qa_email, test_hardening_email_verification |
| Anonymous session check without a 401 | **FIXED** (new `GET /auth/session`; every public page logged a 401 console error) | T: test_qa_attack_paths · crawl: 0 console errors |
| Role isolation (API) | PASS | T: test_phase_h_security (role sweep over every route) |
| Role isolation (UI) | PASS | S: Z01–Z74, W48–W49 |

## B. Donor
| Item | Status | Evidence |
|---|---|---|
| Dashboard, profile, blood group, eligibility | PASS | S: W08, C-rows `/donor*` |
| Donation creation (UI) | PASS | S: W09–W10 |
| Donation history / self-reported state | PASS | S: W10 |
| Donation verification by hospital | PASS | T: test_phase_e_donation_verification · S: W11–W13 |
| Certificate issue + eligibility gate | PASS | T: test_phase_e_donation_verification · S: W14 |
| Certificate download (PDF, token) | PASS | S: W15 · T: test_qa_attack_paths (tampered/expired/foreign tokens 401) |
| Certificate page layout | **FIXED** (undefined Tailwind tokens → broken layout; redesigned) | crawl C-rows `/certificate*` |
| Document upload (identity) | PASS | S: W18–W19 |
| Notifications / read | PASS | T: test_phase18_notifications · S: W32 |
| Mark-read double click | PASS (new race test) | T: test_qa_concurrency |
| Request/emergency response | PASS | T: test_blood_request_lifecycle_phase10, test_phase12_emergency_sos_engine |

## C. Hospital
| Item | Status | Evidence |
|---|---|---|
| Dashboard | PASS | S: W21 |
| Inventory by blood group and by component | PASS | S: W22–W24 · T: test_phase35_blood_component |
| Add / reserve / release / allocate / expire | PASS | S: W24–W25 · T: test_phase34_blood_unit_lifecycle |
| Invalid release refused with visible error | PASS | S: W26 |
| Expiry handling | PASS | T: test_phase34_blood_unit_lifecycle, test_phase_g_* |
| Compatibility enforcement | PASS | T: test_blood_compatibility_matrix_phase9, test_phase35_blood_component |
| Request creation, partial → full fulfilment, over-allocation 409 | PASS | S: W27–W31 · T: test_phase_e_fulfillment |
| Shipment create/dispatch/in-transit/delay | PASS | S: W33–W35 · T: test_phase_d_shipment |
| Receive (live on origin) | PASS (API + E2E) | T: test_phase_d_shipment, e2e phase_j_journeys |
| Failed shipment + reconciliation (+ authorization) | PASS | S: W36–W37 · T: test_phase_e_shipment_reconciliation |
| Transport disruption / recovery | PASS | T: test_phase17_resilience, test_phase16_graph_engine_security |
| Analytics | PASS | S: W43 |

## D. Admin
| Item | Status | Evidence |
|---|---|---|
| Dashboard, users, donors, facilities, shipments | PASS | S: W38–W42 |
| Verification queue, document review (scan state), approve/reject | PASS | S: W19–W20 · T: test_hardening_malware_scanning |
| Certificates registry / revoke | PASS | T: test_phase31_*, e2e phase31_1_domain_signals |
| Audit log + chain verification + signed checkpoints | PASS | S: W44 · T: test_phase_e_audit_chain, test_hardening_audit_chain |
| System health | PASS | T: test_qa_attack_paths (no secrets), test_health |

## E. Blood domain (WHOLE_BLOOD, RED_BLOOD_CELLS, PLASMA, PLATELETS)
| Item | Status | Evidence |
|---|---|---|
| Compatibility per component (incl. plasma inverse ABO) | PASS | T: test_phase35_blood_component |
| Inventory/reservation/allocation/expiry per component | PASS | T: test_phase35_blood_component, test_phase34_blood_unit_lifecycle · S: W23–W24 (Platelets), W33 (RBC) |
| Shipment per component | PASS | S: W33 · e2e phase_e_domain_integrity (Plasma) |
| Plasma post-thaw window | OUT OF SCOPE (no cited rule; not invented) | docs/LIFELINK_HARDENING_REPORT.md §6 |

## F. Realtime
| Item | Status | Evidence |
|---|---|---|
| Transactional outbox, post-commit publish, ordering, seq | PASS | T: test_phase_f_realtime, test_hardening_realtime |
| SSE auth/authorization/audience | PASS | T: test_phase_f_realtime_stream |
| Last-Event-ID replay / resync / lagging stream | PASS | T: test_phase_f_realtime_stream, test_hardening_realtime |
| Live update in the browser | PASS | S: W30 |
| Reconnect after API crash (process killed) | PASS | S: W45–W47 |
| Redis outage → resync | PASS | T: test_hardening_realtime |
| Polling fallback | PASS | T: realtime.test.tsx |
| SIGTERM held open by SSE streams | **FIXED** (`--timeout-graceful-shutdown`, default 5 s; measured 5.26 s vs unbounded) | E: graceful_shutdown.txt |

## G. Notifications — see B/F (creation, ownership, read, unread count, realtime, authz): PASS.

## H. Documents / storage
| Item | Status | Evidence |
|---|---|---|
| MIME/magic-byte validation, size, count | PASS | T: test_phase14_verification_security · S: W17 |
| Malware: infected refused | PASS (real clamd, EICAR) | S: W16 · T: test_hardening_malware_scanning |
| Scanner down → quarantine, not clean | PASS | T: test_hardening_malware_scanning · E: failure_injection.txt |
| Private storage, admin-only download, checksum, versioning | PASS | T: test_phase20_*, test_phase_e_document_versioning |
| Path traversal / filename traversal | PASS | T: test_qa_attack_paths |
| Storage outage → clean 503, nothing half-written | PASS | E: failure_injection.txt |
| MinIO itself | BLOCKED (image/binary download refused); S3 path run against the moto S3 server | E: docker_attempt.txt |

## I. Email
| Item | Status | Evidence |
|---|---|---|
| Mailpit mode (catcher) end-to-end | PASS with **MailDev as stand-in** (Mailpit binary/image unobtainable) | S: W05–W06 · T: test_qa_email |
| Queue → RQ worker → SMTP → link → verified | PASS | T: test_qa_email · S: W05–W06 |
| Gmail configuration (587 STARTTLS / 465 SSL, validation) | PASS | T: test_qa_email, test_phase11_registration_limits_and_gmail |
| STARTTLS certificate verification | **FIXED** (was unverified TLS) | T: test_qa_email |
| App Password never in logs / errors / repr | **FIXED** (config errors echoed it) | T: test_qa_email |
| Docker silently forcing MAIL_HOST=mailpit | **FIXED** | E: compose check in QA report |
| SMTP auth failure, SMTP down, worker down | PASS | T: test_qa_email, test_phase18_notifications |
| `EMAIL_JOB_MAX_RETRIES=0` | **FIXED** (500 on registration) | T: test_qa_email |
| Live Gmail delivery | **BLOCKED** — no credentials; smtp.gmail.com:587 unreachable (timeout) | E: gmail_attempt.txt |
| Test-only dependency missing from hashed lock (aiosmtpd) | **FIXED** | lock re-verified on Python 3.12 (54 tests) |
| TLS context rebuilt on every send (regression from the TLS fix): blood request 9.6–44 s with inline fan-out | **FIXED** (built once, only for TLS; now ~0.6 s) | T: test_qa_email::test_plain_smtp_never_builds_a_tls_context_and_tls_builds_it_once · Playwright 97/97 |

## J. Database
| Item | Status | Evidence |
|---|---|---|
| Fresh DB → head, downgrade, re-upgrade, base → head | PASS | E: migration_check.txt (QA report §11) |
| ORM vs migrations | PASS (indexes/constraints/triggers identical) | E: migration_check.txt |
| Constraints, rollback, concurrency, invariants | PASS | T: test_concurrency, test_phase_d_concurrency, test_phase_e_fulfillment, test_phase_j_cross_workflow_races, scripts/check_invariants |

## K. Security
| Item | Status | Evidence |
|---|---|---|
| Unauthenticated / role / CSRF sweeps over every route | PASS | T: test_phase_h_security |
| IDOR/BOLA sweep (35 ID-taking calls by same-role attackers) | PASS (0 leaks) | T: test_qa_attack_paths |
| Token manipulation, path traversal, SQLi-shaped input, stored XSS, error leakage, secret leakage, headers, CORS | PASS | T: test_qa_attack_paths |
| Dev route map exposed in production build | **FIXED** (`/dev-index` now dev-only) | crawl Z-row `/dev-index` |
| Secret scan of the deliverable | PASS | QA report §20 |
| Every API operation exercised (route log of the full backend run) | PASS — 141/141 exercised; 138 with a 2xx, 2 more covered by new success tests; `POST /admin/graph/resync` success needs Neo4j → BLOCKED | E: route_coverage.txt · T: test_qa_route_gaps |

## L. Frontend
| Item | Status | Evidence |
|---|---|---|
| Every route × 375/768/1280 | PASS (183 visits: 0 overflow, 0 blank, 0 console errors, 0 failed API calls except blocked map tiles) | S: C001–C183 |
| Undefined Tailwind classes (56) | **FIXED** (tokens defined; 0 left except a no-op hook) | QA report §6 |
| Raw enum label "Partially_fulfilled" | **FIXED** | S: W30–W31 |
| Upload errors not announced (no role=alert) | **FIXED** | S: W17 |
| Accessibility (axe serious/critical) | **FIXED** 3 contrast + 2 scroll-region issues (last two after crawl 2; re-checked in the browser 2026-10-07: 0 serious/critical on /analytics, /hospital, /certificate/:id, /donor/identity-verification, /hospital/verification) | S: C-rows |
| Keyboard / focus / modal Escape | PASS | e2e phase_j_journeys, phase27_accessibility |

## M. Infrastructure
| Item | Status | Evidence |
|---|---|---|
| `docker compose up` | **BLOCKED** (registry pulls 403); compose validated in both mail modes | E: docker_attempt.txt |
| PostgreSQL outage/recovery | PASS (4/4) | E: failure_injection.txt (23/23 full-stack run) |
| Redis outage/recovery | PASS | E: failure_injection.txt |
| Worker unavailable → queued, delivered on restart | PASS | T: test_qa_email |
| Health checks | PASS | E: failure_injection.txt |

## N. Performance — PASS (measured this round and the hardening round; see QA report §18).
