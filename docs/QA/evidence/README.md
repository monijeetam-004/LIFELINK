# QA evidence (2026-10-20)

This is the raw output of the runs reported in `../FINAL_AUDIT_REPORT.md`. Nothing here was edited by hand except one redaction, noted in `security/security_probe_live.txt`: the first run printed a partial session-cookie value, which was replaced with `<redacted>`.

## Where each area of the audit brief's evidence lives

| Brief folder | Location here |
|---|---|
| baseline/ | `baseline/`: backend log and JUnit, route log, static checks, vitest baseline |
| authentication/ | `workflows/01-auth/`, `workflows/15-email/` (real Mailpit) |
| donor/ | `workflows/02-donor/`, `workflows/10-donation-verification/`, `workflows/12-certificates/` |
| patient/ | `workflows/06-blood-requests/`, `workflows/07-fulfillment/` |
| hospital/ | `workflows/03-hospital/`, `workflows/07-fulfillment/` |
| inventory/ | `workflows/05-inventory/` |
| emergency-planning/ | `emergency-planning/` (planner, simulation, research mode, decision transparency, recommendation, mathematical model at 3 widths) |
| route-failover/ | `workflows/16-route-failover/` (admin disrupts the route in the UI → new plan) |
| shipments/ | `workflows/08-shipment/`, `workflows/09-reconciliation/` |
| cold-chain/ | `workflows/08-shipment/` (status in the shipment panels). The cold-chain *rules* are backend-tested (FEATURE_TEST_MATRIX.md §1 F); no physical sensor exists |
| administration/ | `workflows/04-admin/`, `workflows/17-analytics/` |
| integrations/ | `workflows/15-email/`; `../INTEGRATION_STATUS.md`; MinIO evidence in `failure-injection/` and `clean-start/docker/` |
| security/ | `security/` (live probe, bandit, pip-audit, npm audits, secret scan); `workflows/11-documents/`, `workflows/18-security/` |
| accessibility/ | `accessibility/axe_gap_pages_final_run.json`; per-route axe results in `frontend-routes/crawl-results-final.json` |
| performance/ | `performance/` (benchmark, live load test, query counts) |
| clean-start/ | `clean-start/docker/`, `clean-start/native/` |
| final/ | `final/`: backend, vitest and Playwright final runs, invariants, static checks |
| (extra) failure-injection/ | `failure-injection/run1_before_fixes.txt`, `run2_after_fixes.txt` |
| (extra) frontend-routes/ | Route crawl: 186 visits, 75 authorisation checks, screenshots |
| (extra) inventory/ | `inventory/inventory.json` (generated from the source) |
