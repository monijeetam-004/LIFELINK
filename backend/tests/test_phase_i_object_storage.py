"""Phase I -- the S3/MinIO storage backend through the real HTTP API.

Root cause of the Phase 34 "10 Playwright failures": every request that
stores an object (verification upload, certificate issuance) failed with
EndpointConnectionError because no object store was running and nothing
waited for one. Infrastructure is fixed in docker-compose.yml (MinIO
healthcheck + one-shot bucket init gating api/worker). This file proves
the APPLICATION side of that path works against an S3 API:

  * startup bucket init creates a missing bucket (and is idempotent);
  * a verification upload lands in the bucket under an app-generated key,
    with the bytes and checksum intact, and the admin download returns it;
  * certificate issuance stores the PDF in the bucket and the owner's
    download returns it.

The S3 API is moto's in-process simulator at the botocore layer (the same
library tests/test_phase20_secure_file_storage.py uses). It is an S3 API
simulator, not MinIO itself -- MinIO images and binaries are not reachable
from this sandbox (registry/download hosts return 403). The same flows were
also run out-of-process (moto_server + real uvicorn + Playwright) -- see
docs/LIFELINK_PHASE_I_INFRASTRUCTURE_REPORT.md.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest
from moto import mock_aws

from app.core import storage
from tests.helpers import auth_client

BUCKET = "lifelink-phase-i-it"
PDF = ("id.pdf", b"%PDF-1.4 phase I s3 integration document", "application/pdf")


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setattr(storage.settings, "s3_endpoint_url", "https://s3.amazonaws.com")
    monkeypatch.setattr(storage.settings, "s3_access_key", "testing")
    monkeypatch.setattr(storage.settings, "s3_secret_key", "testing")
    monkeypatch.setattr(storage.settings, "s3_region", "us-east-1")
    monkeypatch.setattr(storage.settings, "s3_bucket", BUCKET)
    with mock_aws():
        storage.get_s3_client.cache_clear()
        try:
            yield storage.get_s3_client()
        finally:
            storage.get_s3_client.cache_clear()


def _keys(client) -> list[str]:
    return [o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]


def test_startup_bucket_init_creates_missing_bucket_idempotently(s3) -> None:
    assert storage.storage_backend() == "s3"
    assert BUCKET not in {b["Name"] for b in s3.list_buckets()["Buckets"]}
    storage.ensure_bucket()
    storage.ensure_bucket()  # second call (every restart) is a no-op
    assert [b["Name"] for b in s3.list_buckets()["Buckets"]].count(BUCKET) == 1


def test_verification_upload_and_admin_download_go_through_the_bucket(s3) -> None:
    storage.ensure_bucket()
    donor, _ = auth_client("DONOR")
    r = donor.post("/verifications", data={"kind": "DONOR_IDENTITY"}, files=[("files", PDF)])
    assert r.status_code == 201, r.text
    vid = r.json()["id"]

    keys = _keys(s3)
    assert len(keys) == 1
    # App-generated key: fixed prefix + random component; the client's
    # filename survives only as a sanitized suffix (Phase 20).
    assert keys[0].startswith("verifications/") and keys[0] != "id.pdf"
    stored = s3.get_object(Bucket=BUCKET, Key=keys[0])["Body"].read()
    assert stored == PDF[1]

    admin, _ = auth_client("ADMIN")
    detail = admin.get(f"/admin/verifications/{vid}")
    assert detail.status_code == 200, detail.text
    docs = detail.json()["documents"]
    assert docs and docs[0].get("sha256") in (None, hashlib.sha256(PDF[1]).hexdigest())
    dl = admin.get(f"/admin/verifications/{vid}/documents/0")
    assert dl.status_code == 200, dl.text
    assert dl.content == PDF[1]


def test_certificate_pdf_is_stored_in_the_bucket_and_downloadable(s3) -> None:
    storage.ensure_bucket()
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    d = donor.post(
        "/donors/me/donations",
        json={
            "donated_on": date.today().isoformat(),
            "units": 1,
            "hospital_name": "Metro General",
        },
    )
    assert d.status_code == 201, d.text
    admin, _ = auth_client("ADMIN")
    assert admin.post(f"/donations/{d.json()['id']}/verify", json={}).status_code == 200

    c = donor.post("/certificates", json={"donation_id": d.json()["id"]})
    assert c.status_code == 201, c.text
    keys = _keys(s3)
    assert len(keys) == 1 and keys[0].endswith(".pdf")
    body = s3.get_object(Bucket=BUCKET, Key=keys[0])["Body"].read()
    assert body.startswith(b"%PDF")

    dl = donor.get(f"/certificates/{c.json()['id']}/download")
    assert dl.status_code == 200, dl.text
    assert dl.content == body

    stranger, _ = auth_client("DONOR")
    # Phase 15 contract: any unauthorized download is 401 (and audited).
    assert stranger.get(f"/certificates/{c.json()['id']}/download").status_code == 401
    assert uuid.UUID(c.json()["id"])
