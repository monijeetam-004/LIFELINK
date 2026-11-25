# LifeLink demonstration guide: donor shortlist, failover and hand-off

This demo runs on one machine. It needs **no real donors and no external provider credentials**: no Gmail, MSG91, Neo4j or live geocoder.

The donors who answer are **synthetic**. They answer through the same response code a real donor uses, but a demo-only simulator decides what they say. That shows how the software behaves. It does **not** show that real donors would be available, and it says nothing about clinical outcomes.

The commands below were run on 2026-10-31. In that recorded run, the API listened on port 8500, and a production build of the web app was served by `vite preview` on port 4500 instead of the dev server. The evidence is in `docs/QA/evidence/remediation-2026-10-31/`:

* `demo_seed.txt`: seeding, a second seeding run to check idempotency, and the invariants;
* `demo_walkthrough.txt`: the scripted scenarios;
* `shortlist_ui_spec.txt` and `screenshots/`: the hospital page for scenarios A–D at 375/768/1280 px, with axe results.

To repeat the browser check after the walkthrough:

```bash
cd frontend
DEMO_FRONTEND_URL=http://localhost:5173 DEMO_RESULTS=<path to the --json file> \
  npx playwright test e2e/remediation_shortlist_demo.spec.ts
```

For this, pass `--json demo_results.json` to the walkthrough.

## 0. What you need

* PostgreSQL 16 and Redis 7 running locally.
  * One way: `cd backend && docker compose up -d db redis`.
  * Note that those containers publish ports 5432 and 6379.
* The backend virtual environment:
  ```bash
  cd backend
  python -m venv .venv
  .venv/bin/pip install -r requirements-dev.lock
  ```
* The frontend dependencies:
  ```bash
  cd frontend
  npm ci
  ```

## 1. Create and seed a dedicated demo database

Use a **separate** database (`lifelink_demo`). Never point these commands at a shared or real database. Both seed scripts refuse to run when `ENVIRONMENT=production`.

```bash
cd backend
# create (or re-create, for a reset) ONLY the demo database
psql -U postgres -h localhost -c "DROP DATABASE IF EXISTS lifelink_demo WITH (FORCE)" \
                                -c "CREATE DATABASE lifelink_demo OWNER lifelink"

export ENVIRONMENT=development \
       DATABASE_URL=postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_demo \
       REDIS_URL=redis://localhost:6379/7

.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_demo_data         # seed 1337: 1,000 donors, 61 facilities, 20 cities
.venv/bin/python -m scripts.seed_shortlist_demo    # seed 20261031: 400 SYNTHETIC donors + clusters A-D
```

`seed_shortlist_demo` is idempotent: every row is keyed by a fixed e-mail address, so running it twice does not duplicate anything. To **reset** the demo, run the whole block again; the first line re-creates the demo database.

## 2. Start the API in demonstration mode

```bash
cd backend
ENVIRONMENT=development \
DATABASE_URL=postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_demo \
REDIS_URL=redis://localhost:6379/7 \
SECRET_KEY=demo-only-secret-key-not-for-production-0123456789 \
DEMO_MODE=true RQ_SYNC=true NEO4J_URI= DOCUMENT_SCANNER=none \
MAIL_HOST=127.0.0.1 MAIL_PORT=1025 MAIL_STARTTLS=false \
CORS_ORIGINS=http://localhost:5173 \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

How each setting is used:

* **`DEMO_MODE=true`** turns on the admin-only response simulator. Even then, the simulator acts **only** on synthetic accounts (`@synthetic.lifelink.example`, names starting `[SYNTHETIC]`), and it labels every answer it records as simulated. `DEMO_MODE` is refused when `ENVIRONMENT=production`.
* **SMS** is never sent to synthetic accounts: they have no phone number, and the SMS service also refuses that e-mail domain.
* **Mail** goes to `127.0.0.1:1025`. If nothing is listening there, e-mail jobs simply fail. Run Mailpit if you want to see the mail.

## 3. Start the web app

```bash
cd frontend
VITE_API_URL=http://localhost:8000 npm run dev      # http://localhost:5173
```

**Sign-in accounts.** All of them use the documented demo password `LifeLink-Demo-2026!`.

| Who | E-mail |
|---|---|
| Scenario hospitals A–D (fictional towns "Demopur A–D") | `shortlist.hospital.a@lifelink-demo.example.com` … `shortlist.hospital.d@…` |
| Hospital for failover and hand-off | `demo.hospital@lifelink-demo.example.com` |
| Patient | `demo.patient@lifelink-demo.example.com` |
| Administrator | `demo.admin@lifelink-demo.example.com` |

## 4. Run the scripted demonstration

```bash
cd backend
.venv/bin/python -m scripts.demo_walkthrough --api http://localhost:8000 \
    --origin http://localhost:5173 --pause
```

With `--pause`, the script stops before each step and prints the page to open in the browser. Each step is one of:

* a hospital action, which is the same API call as the button named in the prompt;
* a synthetic donor's answer;
* an administrator action.

Leave the browser page open; it updates as the answers arrive. Without `--pause`, the script runs straight through in about a minute and prints everything.

**What each scenario shows, and where to look in the browser**

| # | What it shows | Browser page | What you see |
|---|---|---|---|
| A | Enough donors: 3 needed, 3 accept | Hospital A → `/hospital/requests/<id>` → **Donor shortlist** panel | **Before contact:** "6 eligible candidate(s) found", 0 contacted, "3 still needed". **Final:** 3 *Confirmed for coordination*, the rest *Declined*, state **Enough donors confirmed** |
| B | Honest partial result: 15 eligible, only 2 accept | Hospital B, same panel | **Final:** 2 confirmed, the rest declined or timed out, "still 1 donor(s) short", and escalation text: "Every eligible donor has been contacted; escalate to blood banks." No donor is shown as confirmed who was not |
| C | Nobody eligible | Hospital C, same panel | "0 eligible", the exclusion reasons (beyond the distance limit, interval not elapsed, location unknown, unavailable), and an escalation hint. **No made-up shortlist** |
| D | A ranking is not a confirmation | Hospital D, same panel | Donor **#1** (highest score) is *Timed out*; donor #2 is the one confirmed |
| E | Distance limit | (terminal output) | Donors beyond 50 km get no alert, and the request's notification count for the 80 km donor is 0 |
| F | Failover cannot look secured when it isn't | Demo hospital → `/decision-transparency` → pick the "[DEMO] failover + hand-off" emergency | After the route is disrupted, either the backup is **Reserved** (promoted together with its reservation), or the plan reads **"NOT secured — units could not be reserved … Coordinator action needed."** It is never shown as "Secured" without units |
| G | Plan → shipment hand-off keeps the reservation | Same page → **Hand off to shipment** | The plan reads **"In delivery: reservation handed to shipment(s), never released."**, and the shipments are listed under it. Repeating the hand-off returns the same shipments |

**Ranking order.**

* The panel's **Ranking** selector defaults to the *Unified priority score*. The four original strategies can also be chosen.
* **Why this rank** under a not-yet-contacted donor shows the factor breakdown, which adds up to the score, and the evidence coverage.
* The scenarios give the same counts under the unified order. This was re-run on 2026-10-31; see `docs/QA/evidence/unified-priority-2026-10-31/demo_walkthrough.txt`.

**Demo-mode indicator.** The shortlist panel shows a "DEMO MODE — rows marked SYNTHETIC are generated test donors…" banner. Each synthetic row is tagged **SYNTHETIC**, and simulated answers are tagged **(simulated answer)**.

### Doing the hospital's steps by hand in the browser

The hospital's own actions can be clicked rather than scripted.

1. Run the script with `--only B --pause`. It creates the request and prints its page. Stop at the first pause.
2. Sign in as `shortlist.hospital.b@lifelink-demo.example.com` and open that page. The **Donor shortlist** panel reads "Not contacted yet", with the eligible count and the number still needed.
3. Click **Contact next ranked donors**. The first ranked batch moves to *Awaiting response*. Then let the script continue. It sees that donors were already contacted and skips its own contact step.
4. The synthetic donors' answers come only from the administrator-only simulator, which the script calls.
   * The web app shows a "Simulate synthetic answers (demo)" button only to an administrator viewing a request drawer, and the admin screens do not open that drawer. So in practice the simulator is reached through the script, or as an API call by the demo admin: `POST /demo/shortlist/BLOOD_REQUEST/<id>/simulate`.
   * This is a known gap; see the remediation report §10.
5. For each *Accepted* donor, click **Confirm after re-check**. The eligibility and commitment checks run again, and the donor becomes *Confirmed for coordination*.

## 5. Limits of what the demo proves

* **Synthetic answers.** They show the workflow, the separate counts (eligible, contacted, awaiting, accepted, confirmed, still needed) and the escalation. They do not show real response rates.
* **Shortlist weights.** These are provisional engineering choices, not validated probabilities.
* **Notification state.** "Sent" means an in-app notification was written. Delivery to a phone or mailbox is not tracked, and the demo never claims it.
* **"Confirmed for coordination"** still requires the blood centre's own donor screening.
