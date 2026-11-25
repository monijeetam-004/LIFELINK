# Evidence: audit remediation, 2026-10-31

These are the raw outputs of the runs reported in `docs/remediation/FINAL_REMEDIATION_REPORT.md` §7. Nothing here was edited by hand, apart from three changes:

* screenshots were colour-quantised to reduce their size;
* earlier failed runs were renamed;
* a one-line context header was added at the top of some logs.

Failed and superseded runs are **kept**, under the `*_run1*`, `*_run2*` and `*killed*` names.

| What | Final run | Earlier runs kept |
|---|---|---|
| Backend pytest (real PostgreSQL, Redis, clamd, MinIO, Mailpit) | `backend_final.log`, `backend_junit_final.xml`, `backend_final.meta`, `routes_final.log` | `backend_run1_before_test_setup_fixes.log` (11 failed), `backend_run2_960e749/` (passed), `backend_targeted_after_wording_fix.log` |
| Vitest, tsc, eslint, build | `vitest_final.log`, `tsc.txt`, `eslint.txt`, `build.txt` | — |
| Playwright, CI-identical harness | `playwright_ci_harness.log`, `playwright/`, `playwright_rerun_phase26.log` | `playwright_run1_3_failed_alert_scope.log`, `playwright_run2_qa_evidence_race.log`, `playwright_ci_harness_killed_by_tool_timeout.log` |
| Shortlist panel in the browser (axe, 3 widths) | `shortlist_ui_spec.txt`, `screenshots/` | — |
| Native clean start | `native_clean_start_run.log`, `native_check_result.json` | — |
| Container clean start | `docker/` | `docker_run1_worker_failed/` (a real defect, fixed), `docker_run2_teardown_blocked/` |
| Demo seed and walkthrough | `demo_seed.txt`, `demo_walkthrough.txt`, `demo_results.json` | `demo_seed_run1_invariant31_fail.txt`, `demo_walkthrough_run1.txt`, `demo_results_run1.json` |
