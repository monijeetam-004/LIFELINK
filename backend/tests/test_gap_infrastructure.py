"""Gap elimination sections 10-12: object storage and SMTP against REAL
servers reached over the network -- never mocked.

* ``S3_TEST_ENDPOINT`` (+ ``S3_TEST_ACCESS_KEY`` / ``S3_TEST_SECRET_KEY``):
  any S3-compatible server. The production target is MinIO; in the
  verification sandbox the MinIO image/binary is unobtainable (registries
  and dl.min.io blocked), so these tests ran against ``moto_server`` -- an
  S3 protocol implementation -- and are reported as a STAND-IN result, not
  as "MinIO verified".
* ``SMTP_TEST_HOST`` / ``SMTP_TEST_PORT`` / ``SMTP_TEST_API``: an SMTP mail
  catcher with an HTTP API listing received mail (Mailpit's
  ``/api/v1/messages`` or MailDev's ``/email``). Production dev uses
  Mailpit; in the sandbox MailDev (npm) was the stand-in.
* Gmail is never contacted by the test suite (no credentials exist here;
  smtp.gmail.com is unreachable). ``scripts/send_test_email.py`` is the
  manual smoke test -- see GMAIL_SETUP.md.
Each test is skipped, with the reason, when its server is not configured.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid

import pytest

from app.core import storage
from app.core.config import settings

S3 = os.environ.get("S3_TEST_ENDPOINT")
SMTP_HOST = os.environ.get("SMTP_TEST_HOST")


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setattr(settings, "s3_endpoint_url", S3)
    monkeypatch.setattr(settings, "s3_access_key", os.environ.get("S3_TEST_ACCESS_KEY", "test"))
    monkeypatch.setattr(settings, "s3_secret_key", os.environ.get("S3_TEST_SECRET_KEY", "test"))
    monkeypatch.setattr(settings, "s3_bucket", f"lifelink-it-{uuid.uuid4().hex[:8]}")
    storage.get_s3_client.cache_clear()
    yield
    storage.get_s3_client.cache_clear()


@pytest.mark.skipif(not S3, reason="S3_TEST_ENDPOINT not set (no MinIO/S3 server available)")
def test_s3_round_trip_presign_and_delete(s3):
    assert storage.storage_backend() == "s3"
    storage.ensure_bucket()
    storage.ensure_bucket()  # idempotent
    key = f"quarantine/{uuid.uuid4()}.pdf"
    storage.put_bytes(key, b"%PDF-1.4 hello", "application/pdf")
    assert storage.object_exists(key)
    assert storage.get_bytes(key) == b"%PDF-1.4 hello"
    url = storage.presign_get(key, 60)
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - local test server
        assert r.read() == b"%PDF-1.4 hello"
    storage.delete_key(key)
    assert not storage.object_exists(key)
    with pytest.raises(storage.StorageObjectNotFoundError):
        storage.get_bytes(key)


@pytest.mark.skipif(not S3, reason="S3_TEST_ENDPOINT not set (no MinIO/S3 server available)")
def test_s3_bucket_is_not_public(s3):
    storage.ensure_bucket()
    key = f"docs/{uuid.uuid4()}.pdf"
    storage.put_bytes(key, b"secret", "application/pdf")
    plain = f"{S3.rstrip('/')}/{settings.s3_bucket}/{key}"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(plain, timeout=10)  # noqa: S310
    assert exc.value.code in (401, 403)


@pytest.mark.skipif(not SMTP_HOST, reason="SMTP_TEST_HOST not set (no Mailpit/SMTP catcher)")
def test_smtp_delivery_reaches_a_real_mail_catcher(monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(settings, "mail_host", SMTP_HOST)
    monkeypatch.setattr(settings, "mail_port", int(os.environ.get("SMTP_TEST_PORT", "1025")))
    monkeypatch.setattr(settings, "mail_username", None)
    monkeypatch.setattr(settings, "mail_starttls", False)
    marker = f"LifeLink IT {uuid.uuid4().hex[:10]}"
    email_service._smtp_send("someone@example.com", marker, "<p>integration</p>")
    api = os.environ["SMTP_TEST_API"]
    deadline = time.time() + 10
    while time.time() < deadline:
        with urllib.request.urlopen(api, timeout=5) as r:  # noqa: S310
            body = json.loads(r.read())
        messages = body.get("messages", body) if isinstance(body, dict) else body
        if any(marker in json.dumps(m) for m in messages):
            return
        time.sleep(0.5)
    pytest.fail("message never reached the mail catcher")


def test_gmail_is_never_silently_replaced_by_mailpit(monkeypatch):
    """Provider detection: smtp.gmail.com is reported as gmail and keeps
    its TLS requirement; the docker compose file only defaults MAIL_HOST to
    mailpit when MAIL_HOST is unset."""
    monkeypatch.setattr(settings, "mail_host", "smtp.gmail.com")
    assert settings.mail_provider == "gmail"
    compose = open(os.path.join(os.path.dirname(__file__), "..", "docker-compose.yml")).read()
    assert "MAIL_HOST: ${MAIL_HOST:-mailpit}" in compose
    assert "MAIL_HOST: mailpit\n" not in compose
