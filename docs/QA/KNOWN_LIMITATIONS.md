# Known limitations

Recorded at the start of the QA round, then updated with what the round found. Each item says whether it is a limitation of the **environment** (it could not be checked here), of the **product** (a known gap in the software), or a **clinical** boundary.

## Environment: could not be verified here

| # | Item | Why | What exists instead | How to close it |
|---|---|---|---|---|
| E1 | Neo4j graph engine against a real server | No Neo4j server obtainable: registries 403, distribution and Maven blocked | Unit tests of the answer handling and trust rule; the relational engine everywhere | INTEGRATION_STATUS.md, "Neo4j" |
| E2 | Gmail delivery | No credentials; SMTP egress unavailable | Real Mailpit delivery through the real worker | INTEGRATION_STATUS.md, "Gmail SMTP" |
| E3 | MSG91 SMS delivery | No account or DLT template; host unreachable | In-process transport tests; console provider marked LOGGED_ONLY | INTEGRATION_STATUS.md, "MSG91" |
| E4 | Live geocoder | Public Nominatim refused by the egress proxy | Fallbacks verified live | INTEGRATION_STATUS.md, "Geocoder" |
| E5 | Live map tiles | Tile hosts refused by the egress proxy | Fallback banner (E2E) | Run the crawl on an unrestricted network |
| E6 | Official container base images | Registries unreachable | Real compose run on locally built Ubuntu 24.04 bases; nginx ran with a root master | INTEGRATION_STATUS.md, "Official container images" |
| E7 | Official ClamAV signature database | database.clamav.net unreachable | Project-built signatures (EICAR, PDF active-content rules, ClamAV test files) | Run `freshclam` on a connected host; re-run `CLAMD_TEST_REQUIRED=1 pytest tests/test_gap_clamav_real.py` |
| E8 | Production-scale performance | 2-vCPU sandbox; the load generator shares the machine | Reproducible benchmark and load test with the machine recorded | PERFORMANCE_RESULTS.md, "Reproduce" |
| E9 | Manual screen-reader test | No assistive technology in the sandbox | axe-core automation (0 serious or critical violations); keyboard paths in Playwright | Manual NVDA/VoiceOver pass on the main journeys |
| E10 | External penetration test | Out of scope for an automated audit | Live black-box probe (27 checks), in-process security suites, bandit, dependency audits | Commission a test against a staging deployment |

## Product: known gaps (not defects found and fixed in this round)

| # | Item | Severity | Notes |
|---|---|---|---|
| P1 | Transport is modelled as fixed route lengths and times: no road network, traffic or courier capacity | Medium (design scope) | Paper, Limitations |
| P2 | Cold-chain readings come through the API; no device integration | Medium (design scope) | Demo readings are labelled SIMULATED |
| P3 | `GET /hospitals/me/inventory/components` and `/hospitals/me/units` run a lazy expiry sweep per inventory row: 28 SQL statements for the demo hospital, bounded by at most 8 groups × 4 components | Low | PERFORMANCE_RESULTS.md. Bounded, not N+1 over units; could be batched |
| P4 | Dev-only npm advisories (braces via Tailwind 3; postcss-selector-parser, moderate) | Low | Not in the shipped bundle (`npm audit --omit=dev` = 0); SECURITY_FINDINGS.md |
| P5 | `ruff format --check` lists 179 files whose formatting predates this round | Informational | Not a CI gate; no reformatting churn was introduced |
| P6 | The SPA bundle is larger than 500 kB after minification (Vite warning) | Low | No code-splitting yet |

## Clinical boundaries

See CLINICAL_ASSUMPTIONS_AND_LIMITATIONS.md. In short:

* The rules implemented are cited simplifications.
* **The 56-day donation interval has no cited source and may not match the target jurisdiction.**
* There is no crossmatch, antibody screen, irradiation, CMV or TTI handling.
* LifeLink must not be used for real transfusion decisions.
