# MinIO (S3) setup

Uploaded verification documents and certificates are stored in S3-compatible
object storage when `S3_ENDPOINT_URL` is set (MinIO in development), else in
a local directory.

```bash
docker compose up -d minio createbuckets     # bucket lifelink-dev, anonymous access: none
# .env
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=<secret store>
S3_SECRET_KEY=<secret store>
S3_BUCKET=lifelink-dev
```

Integration test (any S3 server): `S3_TEST_ENDPOINT=http://localhost:9000
S3_TEST_ACCESS_KEY=... S3_TEST_SECRET_KEY=... pytest tests/test_gap_infrastructure.py`
— bucket creation (idempotent), put/get/exists/delete, presigned GET, and
that an object is NOT readable without a signature.

**Status in the sandbox: PARTIAL (stand-in).** MinIO itself was unobtainable
(registries and dl.min.io blocked). The same tests passed against
`moto_server` 5.1.4 (an S3 protocol implementation from PyPI) on
127.0.0.1:9100 — this proves the S3 code path, not MinIO specifically.
