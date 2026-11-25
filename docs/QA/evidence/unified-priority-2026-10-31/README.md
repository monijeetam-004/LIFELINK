# Evidence: Unified Donor Priority Score, 2026-10-31

The raw outputs behind `docs/remediation/UNIFIED_PRIORITY_REPORT.md` §E and §F. Nothing here was edited by hand.

| File | What it is |
|---|---|
| `baseline_targeted.log` | Matching and shortlist tests at `365fe04`, before any change |
| `backend_final.*`, `routes_final.log` | Full backend suite at `2c80172` |
| `vitest_final.log`, `tsc.txt`, `eslint.txt`, `build.txt` | Frontend checks |
| `playwright_ci_harness.log`, `playwright/` | CI-identical Playwright run |
| `shortlist_ui_spec.txt`, `screenshots/` | Demo-stack browser checks (scenarios A–D and the score breakdown), with axe |
| `demo_seed.txt`, `demo_walkthrough.txt`, `demo_results.json` | Demo database and scripted scenarios under the unified default |
| `ranking_comparison.json`, `ranking_comparison.txt` | Offline five-mode comparison. Synthetic; see the caveat inside |
