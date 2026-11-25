# Research limitations (state these wherever results are reported)

1. **All data is synthetic.** Donors, facilities, stock, routes, requests
   and emergencies come from `scripts/seed_demo_data.py`,
   `scripts/benchmark_pathways.py` and the test suites. No real patient,
   donor or hospital data was used. `DATA_IS_SYNTHETIC=true` is reported by
   `/pathways/config`, forecasts and every live research page.
2. **No clinical validation.** The system has not been evaluated in any
   hospital, with any clinician, or against patient outcomes. Nothing here
   supports a claim that it saves time, units or lives.
3. **Planning parameters are assumptions, not measurements**: handling time
   per delivery (30 min), deadline-risk margin (15 min), straight-line speed
   for unrouted estimates (45 km/h), maximum hops (6), maximum backups (3),
   donor distance limit (50 km), hospital alert radius (50 km), telemetry
   gap limit (30 min). All configurable; all reported with each plan.
4. **Clinical rules are cited, not derived**: shelf lives (35/42/5/365 days),
   the 24-hour post-thaw plasma window, storage temperature ranges and
   component ABO compatibility come from the sources cited in
   `app/core/clinical_sources.py` and `app/core/component_compatibility.py`
   (ruleset `2026.10.10-1`). They are simplifications: no irradiation,
   CMV status, antibody screening, cross-matching or paediatric rules.
5. **Transport model**: routes are undirected edges with a fixed distance
   and transit time; no live traffic, vehicle availability, courier
   capacity or time-of-day effects. Straight-line estimates are labelled
   and never classified better than DEADLINE_RISK under a deadline.
6. **Graph engine**: Neo4j integration is implemented and unit-tested, but
   was not run against a live Neo4j server in the verification environment
   (BLOCKED). All reported planner results come from the relational engine.
7. **Cold chain**: there is no device driver; readings are ingested via the
   API. Demonstration readings are labelled SIMULATED. No physical sensor
   was used.
8. **Forecasting**: simple exponential smoothing on synthetic history; no
   seasonality or covariates; backtests report when it fails to beat naive
   baselines. Not a clinical or operational prediction.
9. **Performance numbers** come from a 2-vCPU sandbox on synthetic networks
   (20/100/400 facilities); they show scaling behaviour, not production
   capacity.
10. **External delivery**: no real email (Gmail), SMS (MSG91) or geocoder
    traffic was exercised; MinIO and Mailpit were replaced by protocol
    stand-ins (moto, MailDev) in the sandbox.
