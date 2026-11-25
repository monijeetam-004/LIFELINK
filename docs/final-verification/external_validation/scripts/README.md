# Final external validation: run scripts

These are the exact harness scripts used on 2026-10-13. In the sandbox they lived in `/srv/realsvc`. The evidence they produced is in `../../evidence/external/`.

| Script | What it does |
|---|---|
| `native_app_check.sh` | Runs the API, worker, deadline monitor and frontend natively against real MinIO (127.0.0.1:9000) and real Mailpit (1025/8025). Then runs `scripts/clean_start_check.py`. |
| `minio_workflow.py` | Runs the LifeLink document-upload workflow against real MinIO, including an outage (503, nothing stored), recovery after a restart, and an admin download. |
| `docker_clean_start.sh` | Docker clean start: lists volumes, runs `down -v`, creates `.env` from `.env.example`, runs `up --build`, then runs the functional checks inside the api container and Playwright against the containerised frontend. |

**Credentials.** MinIO root credentials are read from `$R/minio.env`, a 0600 file outside the repository that you create yourself. The scripts never print them.

**Paths.** Paths such as `/srv/realsvc` and `/srv/dockerclean` are the sandbox's. Adjust them for your machine.
