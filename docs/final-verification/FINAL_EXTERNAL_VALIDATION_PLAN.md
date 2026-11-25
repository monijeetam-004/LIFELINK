# Final external validation: plan

**Starting point** (`FINAL_GAP_MATRIX.md`, 2026-10-13): verdict **PASS WITH EXTERNAL VALIDATION REQUIRED**.

**Scope:** only the gaps below. No redesign and no unrelated refactoring.

**Rule:** a stand-in (moto, MailDev, mocked HTTP) can never turn a row into PASS for the real product.

## Egress re-probe at the start of this phase

Full output: `evidence/external/egress_probe_2026-10-13.txt`.

| Destination | Reachable? | Consequence |
|---|---|---|
| Docker Hub, public.ecr.aws, quay.io, ghcr.io, mirror.gcr.io, gcr.io, registry.k8s.io | **No** (egress proxy answers 403 to CONNECT) | Official container images cannot be pulled |
| dist.neo4j.org, neo4j.com, Maven Central (repo1 / repo.maven.apache.org / GCS mirror / aliyun / huaweicloud) | **No** | Neither a Neo4j server distribution nor its build dependencies can be downloaded |
| proxy.golang.org | **No** | MinIO and Mailpit cannot be built from source |
| **GitHub release downloads** (`github.com/<org>/<repo>/releases/download/...`) | **Yes** | The **official** Mailpit release binary and the **official** MinIO `.deb` can be downloaded |
| PyPI, npm, Ubuntu archive | **Yes** | Python and Node dependencies and Ubuntu packages (PostgreSQL 16, Redis 7, ClamAV, nginx) |
| smtp.gmail.com | **No** | Gmail cannot be reached from here |
| api.msg91.com, control.msg91.com | **No** | MSG91 cannot be reached from here |
| nominatim.openstreetmap.org | **No** | The public geocoder cannot be reached from here |

## Checklist

| Gap | Current Status | Required External Dependency | Validation Method | Target Result |
|-----|----------------|------------------------------|------------------|---------------|
| G8 Neo4j | BLOCKED | A Neo4j 5 server: Docker image, dist.neo4j.org tarball, or Maven Central to build it | Attempt every channel. If one works: start it, check Bolt and auth, run `POST /admin/graph/resync`, `scripts/verify_neo4j.py`, and `tests/test_gap_neo4j.py` with `NEO4J_TEST_URI` | PASS if a real server can be obtained. Otherwise **BLOCKED — REAL SERVER UNAVAILABLE**, with probe evidence and exact commands for the user |
| G10 MinIO | PARTIAL (moto stand-in) | The real MinIO server | Official `minio_…_amd64.deb` from the MinIO GitHub release (the version pinned in compose). Checks: health, credentials, bucket creation and privacy, the LifeLink storage layer (put / get / presign / delete), the infrastructure tests, and the LifeLink document-upload workflow, all against it | PASS if every check runs against real MinIO |
| G11 Mailpit | PARTIAL (MailDev stand-in) | The real Mailpit | Official Mailpit release binary (the version pinned in compose). A real LifeLink registration makes the RQ worker send the verification mail; check it through Mailpit's API (subject, recipient, body, template link, no secrets), and run `test_smtp_delivery_reaches_a_real_mail_catcher` against Mailpit | PASS if the real Mailpit receives the real LifeLink e-mail |
| G12 Gmail | BLOCKED | `smtp.gmail.com:587` reachable, plus the user's Gmail address and App Password in the environment | Check reachability and whether credentials exist. If both are present, run `python -m scripts.send_test_email` | **BLOCKED** unless both exist. Document exactly what the user must provide |
| G14 SMS / MSG91 | BLOCKED | A MSG91 account (auth key, sender ID, DLT-approved Flow ID) and reachability of control.msg91.com | Check reachability and credentials. Re-run the provider tests | **BLOCKED** unless both exist. Document exactly what the user must provide |
| G4 Live geocoder | PARTIAL | A reachable Nominatim-compatible service (`GEOCODER_URL`) | Probe the public Nominatim. Re-verify the fallbacks (unknown place, network failure, garbage response) with the real `geocoding.resolve` | PARTIAL / BLOCKED for the live service, with fallbacks re-verified |
| G9 Docker / frontend image | PARTIAL | Docker engine (available) and official base images (not pullable) | Build the real Dockerfiles on locally built Ubuntu-24.04 base images. Run a real `docker compose up` (project `lifelink`) with real PostgreSQL, Redis, ClamAV, the official Mailpit and MinIO binaries, the API, worker, deadline monitor and frontend containers. Then the functional checks and a browser workflow against the **containerised** frontend | PASS for the Dockerfiles, compose wiring and running application. Official base images recorded as unverifiable here |

**Not in scope** (already PASS, with evidence in `FINAL_GAP_MATRIX.md`): PostgreSQL, Redis and ClamAV. They are re-exercised by the Docker clean start and the regression runs.
