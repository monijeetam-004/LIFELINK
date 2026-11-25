# Integration status

**Rule.** Each dependency is verified separately. A stub, mock or stand-in never counts as validation of the real service. The table says which kind of evidence exists for each one.

**Evidence kinds:**

* **Real** — exercised against the real service, in this round or in the final external validation of 2026-10-13. Real includes the official release binary of the same software (MinIO, Mailpit) run locally.
* **Stand-in** — exercised only against an in-process transport or a substitute.
* **Blocked** — the service could not be reached from this environment.

## Dependency matrix

| # | Dependency | Used for | Real-service evidence | Stand-in / unit-level evidence | Status |
|---|---|---|---|---|---|
| 1 | **PostgreSQL 16** | System of record, row locks, constraints, outbox, audit triggers | Every backend test runs on real PostgreSQL. This round added concurrency and race tests with real threads, a live outage → 503 → recovery (`evidence/failure-injection/run2_after_fixes.txt`), migrations from zero, and `alembic check`. | — | **IMPLEMENTED_AND_VERIFIED** |
| 2 | **Redis 7** | Sessions, rate limits, RQ queues, pub/sub | Real Redis in every test. Live outage: readiness 503, login and reads 503 (never 500), recovery, and sessions correctly gone after a restart without persistence. 100 SSE streams share 2 pub/sub connections (`evidence/performance/loadtest_live.txt`). | — | **IMPLEMENTED_AND_VERIFIED** |
| 3 | **RQ worker** (e-mail, SMS retry, re-scan) | Background jobs | Live: with the worker down, the job waits in the queue and is delivered **once** after a restart. With SMTP down, the job lands in the failed registry with no false success, and a resend works after recovery. In the Docker run the container is *healthy*, by its own health check. | — | **IMPLEMENTED_AND_VERIFIED** |
| 4 | **Deadline monitor** (scheduled loop) | Deadline warnings and escalations, expiry sweep | Real DB: three concurrent passes warn and escalate each emergency **exactly once** (`test_two_deadline_monitor_passes_at_once_warn_and_escalate_exactly_once`). Heartbeat-based health in Docker. | — | **IMPLEMENTED_AND_VERIFIED** |
| 5 | **SMTP → Mailpit** (dev mail server) | Verification, reset and notification e-mail | Real Mailpit v1.21.8: a LifeLink registration → API → Redis queue → RQ worker → SMTP → Mailpit. The message is read through Mailpit's API, its link is followed in the browser and the account is verified (`evidence/workflows/15-email/…`, `01-auth/auth_05_email_verified.png`). Infra test `test_smtp_delivery_reaches_a_real_mail_catcher` against Mailpit. | — | **IMPLEMENTED_AND_VERIFIED** |
| 6 | **Gmail SMTP** | Production mail | **None.** No credentials were supplied, and smtp.gmail.com gives no SMTP greeting through this sandbox's egress. | Precedence rule (Gmail never silently replaced by Mailpit) tested; STARTTLS and login path tested with an in-process fake | **BLOCKED_BY_ENVIRONMENT** |
| 7 | **MSG91 SMS** | Emergency SMS | **None.** No account or DLT template; api.msg91.com is unreachable. `scripts/send_test_sms.py` is ready for one controlled send. | MSG91 request and response through `httpx.MockTransport`; console provider records LOGGED_ONLY (never SENT); retry and backoff tested | **BLOCKED_BY_ENVIRONMENT** |
| 8 | **Object storage (MinIO / S3)** | Verification documents, certificate PDFs | Real MinIO (official release). This round: during an outage an upload returns 503 and stores nothing; after recovery the upload works and the admin download is byte-identical; the owner cannot use the admin route. 2026-10-13: private bucket (anonymous 403), presigned URLs, wrong credentials refused. Infra S3 tests in the full regression against MinIO. | — | **IMPLEMENTED_AND_VERIFIED** |
| 9 | **Malware scanner (clamd)** | Every upload | Real clamd (project-built signatures). Clean file passes, EICAR is refused, ClamAV test files, timeout, unavailable daemon. Live: with clamd down the file is **quarantined as SCAN_FAILED, not downloadable, not approvable**, and a re-scan after recovery releases it as CLEAN. | — | **IMPLEMENTED_AND_VERIFIED** (official signature DB unreachable; project DB used) |
| 10 | **Neo4j 5** (optional graph engine) | Path queries; PostgreSQL stays authoritative | **None.** No server obtainable (registries 403, downloads blocked). | Answer handling, trust rule and fallback with the driver call replaced (5 tests). Planner runs on the relational engine everywhere | **BLOCKED_BY_ENVIRONMENT** |
| 11 | **Geocoder** (Nominatim-compatible, optional) | Typed emergency addresses | **None.** The public Nominatim was refused by the egress proxy (403). Fallbacks verified live: unknown place gives no location, the gazetteer gives a labelled estimate, coordinates are taken as user input, invalid coordinates are rejected. | Success, failure and garbage responses through an in-process transport | **IMPLEMENTED_BUT_PARTIAL** (live service blocked) |
| 12 | **Map tiles** (OpenStreetMap) | Donor and hospital maps | **None** (egress blocked). The crawl recorded 106 tile-load errors, all from blocked tile hosts. The app shows its own fallback banner. | E2E with tile requests aborted | **IMPLEMENTED_BUT_PARTIAL** (live tiles blocked) |
| 13 | **Container services** | Deployment | `docker compose up --build` of the unchanged compose file, on locally built base images (registries blocked). Health checks for every service. Graceful shutdown bounded at 5 s in the image CMD (fixed this round); 5.22 s measured through the production entrypoint. Security headers from the frontend nginx (added this round). | — | **IMPLEMENTED_AND_VERIFIED** on local base images; the official base images are unverified |

## Exact procedure for every blocked integration

Put credentials only in `backend/.env`, which is git-ignored, or in a deployment secret store. Never put them in `.env.example`, in source code or in a commit. None of the commands below print a secret.

### Gmail SMTP

**Variables:**

```
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_STARTTLS=true
MAIL_USE_SSL=false
MAIL_USERNAME=<address>@gmail.com
MAIL_PASSWORD=<16-letter Google App Password>
MAIL_FROM=<address>@gmail.com
```

**Commands:**

```bash
cd backend
python -c "from app.core.config import settings; print(settings.mail_summary())"   # no password printed
python -m scripts.send_test_email <your inbox>                                     # ONE message
```

**Expected:** `accepted by smtp.gmail.com:587`, exit code 0, and the message is in the inbox.

**Evidence to keep:** the command output, and a screenshot of the received message with the address cropped out.

### MSG91

**Variables:**

```
SMS_PROVIDER=msg91
MSG91_ENABLED=true
MSG91_AUTH_KEY=…
MSG91_SENDER_ID=<6 letters>
MSG91_FLOW_ID=…                (or MSG91_TEMPLATE_FLOW_MAP with an EMERGENCY_CREATED entry)
MSG91_DLT_TEMPLATE_ID=…
```

**Command:**

```bash
python -m scripts.send_test_sms +91XXXXXXXXXX
```

**Expected:**

* exit code 0 with `ACCEPTED by the provider; provider_message_id=…`;
* then delivery shown in MSG91's delivery report and on the handset.

The number is printed masked.

### Neo4j

**Commands:**

```bash
docker compose --profile graph up -d neo4j
# backend/.env: NEO4J_URI=bolt://localhost:7687, NEO4J_USERNAME=neo4j, NEO4J_PASSWORD=<yours>
curl -X POST -b cookie.txt -H 'Origin: http://localhost:5173' localhost:8000/admin/graph/resync
python -m scripts.verify_neo4j
NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_TEST_USER=neo4j NEO4J_TEST_PASSWORD=… pytest tests/test_gap_neo4j.py -v
```

**Expected:**

* the resync reports `ok=true`;
* `verify_neo4j` passes;
* `test_real_neo4j_paths_equal_relational_paths` passes: both engines choose the same routes.

### Geocoder

**Setup:** set `GEOCODER_URL` to a reachable Nominatim-compatible `/search` endpoint. Respect its usage policy: identify yourself in `GEOCODER_USER_AGENT` and send at most one request per second to the public instance.

**Test:** as a patient, create an SOS with a typed address.

**Expected:** `geocode_source=GEOCODER` with coordinates. An unknown address still gives no location.

### Official container images

**Command:** on a machine with registry access, run the plain `docker compose up -d --build` in `backend/`, without the local-image override.

**Then run:**

* `docker compose exec api python -m scripts.clean_start_check`;
* `CONTAINER_FRONTEND_URL=http://localhost:5173 npx playwright test e2e/container_frontend.spec.ts`.
