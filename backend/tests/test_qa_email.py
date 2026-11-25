"""Final QA -- email: both providers, the full verification pipeline through
the real RQ worker and a real SMTP server, TLS certificate verification,
authentication failure and secret hygiene.

Real pieces: aiosmtpd SMTP servers on localhost (plain, and STARTTLS with
a freshly generated certificate), the real (test) Redis + rq.SimpleWorker,
the real API. What is NOT exercised here (no credentials / egress in this
environment): delivery through smtp.gmail.com itself -- see
docs/LIFELINK_EMAIL.md and the QA report.
"""

from __future__ import annotations

import email
import logging
import re
import socket
import ssl
import subprocess

import pytest
from pydantic import SecretStr, ValidationError
from rq import SimpleWorker
from rq.registry import FailedJobRegistry

from app.core.config import Settings, settings
from app.core.redis import rq_redis
from app.services import email_service
from app.workers.queue import email_queue


@pytest.fixture(autouse=True)
def _fresh_tls_context():
    # The verifying TLS context is cached per process; tests that swap the
    # trust store must not see a context built by an earlier test.
    email_service._tls_context.cache_clear()
    yield
    email_service._tls_context.cache_clear()


def test_plain_smtp_never_builds_a_tls_context_and_tls_builds_it_once(monkeypatch) -> None:
    """Regression: building the CA bundle on every send made a blood
    request's inline donor fan-out take 10-40 s."""
    calls = []
    real = ssl.create_default_context
    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(ssl, "create_default_context", counting)

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): assert context.verify_mode == ssl.CERT_REQUIRED
        def login(self, *a): pass
        def send_message(self, msg): pass

    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(settings, "mail_starttls", False)
    for _ in range(5):
        email_service._smtp_send("a@example.com", "s", "<p>x</p>")
    assert calls == []
    monkeypatch.setattr(settings, "mail_starttls", True)
    for _ in range(5):
        email_service._smtp_send("a@example.com", "s", "<p>x</p>")
    assert len(calls) == 1

ORIGIN = {"Origin": "http://localhost:5173"}
APP_PW = "abcd efgh ijkl mnop"  # shape of a Google App Password; not real


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Sink:
    def __init__(self) -> None:
        self.messages: list[email.message.Message] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        self.messages.append(email.message_from_bytes(envelope.content))
        return "250 OK"


def _auth_ok(server, session, envelope, mechanism, auth_data):
    from aiosmtpd.smtp import AuthResult

    ok = auth_data.login == b"lifelink.qa@gmail.com" and auth_data.password == APP_PW.encode()
    return AuthResult(success=ok)


@pytest.fixture
def plain_smtp(monkeypatch):
    """Mailpit-mode stand-in: plain SMTP, no auth (what Mailpit accepts)."""
    from aiosmtpd.controller import Controller

    sink, port = _Sink(), _free_port()
    ctl = Controller(sink, hostname="127.0.0.1", port=port)
    ctl.start()
    for k, v in {
        "mail_host": "127.0.0.1",
        "mail_port": port,
        "mail_starttls": False,
        "mail_use_ssl": False,
        "mail_username": None,
        "mail_password": None,
        "rq_sync": False,
    }.items():
        monkeypatch.setattr(settings, k, v)
    try:
        yield sink
    finally:
        ctl.stop()


@pytest.fixture
def tls_material(tmp_path):
    """A throwaway CA-less self-signed certificate for 127.0.0.1."""
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(crt),
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return key, crt


@pytest.fixture
def tls_smtp(monkeypatch, tls_material):
    """Gmail-shaped server: STARTTLS required before AUTH, AUTH required."""
    from aiosmtpd.controller import Controller

    key, crt = tls_material
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(str(crt), str(key))
    sink, port = _Sink(), _free_port()
    ctl = Controller(
        sink,
        hostname="127.0.0.1",
        port=port,
        tls_context=ctx,
        require_starttls=True,
        authenticator=_auth_ok,
        auth_required=True,
    )
    ctl.start()
    for k, v in {
        "mail_host": "127.0.0.1",
        "mail_port": port,
        "mail_starttls": True,
        "mail_use_ssl": False,
        "mail_username": "lifelink.qa@gmail.com",
        "mail_password": SecretStr(APP_PW),
        "mail_from": "lifelink.qa@gmail.com",
    }.items():
        monkeypatch.setattr(settings, k, v)
    try:
        yield sink, crt
    finally:
        ctl.stop()


def _burst() -> None:
    SimpleWorker([email_queue], connection=rq_redis).work(burst=True)


def _link(msg: email.message.Message) -> str:
    html = next(
        p.get_payload(decode=True).decode()
        for p in msg.walk()
        if p.get_content_type() == "text/html"
    )
    m = re.search(r'href="([^"]+/verify-email\?token=([^"]+))"', html)
    assert m, html
    return m.group(2)


# --------------------------------------------------------------------------- #
# Configuration: both modes, refusals, secret hygiene
# --------------------------------------------------------------------------- #
def test_mailpit_mode_and_gmail_mode_are_both_valid_configurations() -> None:
    mp = Settings(mail_host="mailpit", mail_port=1025, mail_starttls=False)
    assert mp.mail_provider == "mailpit"
    assert mp.mail_summary() == "mailpit mailpit:1025 plain no-auth"
    gm = Settings(
        mail_host="smtp.gmail.com",
        mail_port=587,
        mail_starttls=True,
        mail_username="x@gmail.com",
        mail_password=APP_PW,
        mail_from="x@gmail.com",
    )
    assert gm.mail_provider == "gmail"
    assert gm.mail_summary() == "gmail smtp.gmail.com:587 starttls auth"
    ssl465 = Settings(
        mail_host="smtp.gmail.com",
        mail_port=465,
        mail_use_ssl=True,
        mail_username="x@gmail.com",
        mail_password=APP_PW,
    )
    assert ssl465.mail_summary().startswith("gmail smtp.gmail.com:465 ssl")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {"mail_host": "smtp.gmail.com", "mail_port": 587, "mail_starttls": True},
            "Gmail SMTP needs MAIL_USERNAME",
        ),
        (
            {
                "mail_host": "smtp.gmail.com",
                "mail_port": 25,
                "mail_starttls": False,
                "mail_username": "x@gmail.com",
                "mail_password": APP_PW,
            },
            "Gmail SMTP requires MAIL_PORT=587",
        ),
        (
            {
                "mail_host": "smtp.gmail.com",
                "mail_port": 587,
                "mail_starttls": False,
                "mail_username": "x@gmail.com",
                "mail_password": APP_PW,
            },
            "Gmail SMTP requires MAIL_PORT=587",
        ),
        ({"mail_username": "x@example.com"}, "must be set together"),
        ({"mail_password": APP_PW}, "must be set together"),
        ({"mail_starttls": True, "mail_use_ssl": True}, "mutually exclusive"),
        (
            {"mail_host": "smtp.example.com", "mail_username": "u", "mail_password": APP_PW},
            "unencrypted connection",
        ),
    ],
)
def test_unworkable_mail_configurations_are_refused_at_startup(kwargs, message) -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(**kwargs)
    assert message in str(exc.value)
    assert APP_PW not in str(exc.value)


def test_app_password_never_appears_in_repr_summary_or_dump() -> None:
    s = Settings(
        mail_host="smtp.gmail.com",
        mail_port=587,
        mail_starttls=True,
        mail_username="x@gmail.com",
        mail_password=APP_PW,
    )
    assert APP_PW not in repr(s)
    assert APP_PW not in s.mail_summary()
    assert APP_PW not in str(s.model_dump())
    assert s.mail_password.get_secret_value() == APP_PW


def test_readiness_reports_the_provider_but_never_credentials(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "mail_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "mail_username", "x@gmail.com")
    monkeypatch.setattr(settings, "mail_password", SecretStr(APP_PW))
    body = client.get("/health/ready").text
    assert '"provider":"gmail"' in body.replace(" ", "")
    assert APP_PW not in body and "x@gmail.com" not in body


# --------------------------------------------------------------------------- #
# TLS + authentication (the Gmail path, against a local Gmail-shaped server)
# --------------------------------------------------------------------------- #
def test_starttls_verifies_the_server_certificate(tls_smtp) -> None:
    """An untrusted certificate is refused BEFORE credentials are sent --
    previously starttls() ran without a verifying context."""
    sink, _ = tls_smtp
    with pytest.raises(ssl.SSLCertVerificationError):
        email_service._smtp_send("someone@example.com", "s", "<p>x</p>")
    assert sink.messages == []


def test_starttls_auth_and_delivery_with_a_trusted_certificate(tls_smtp, monkeypatch) -> None:
    sink, crt = tls_smtp
    real = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: real(cafile=str(crt)))
    email_service._smtp_send("someone@example.com", "Hello over TLS", "<p>x</p>")
    assert len(sink.messages) == 1
    assert sink.messages[0]["Subject"] == "Hello over TLS"
    assert sink.messages[0]["From"] == "lifelink.qa@gmail.com"


def test_wrong_app_password_fails_loudly_in_the_worker_without_leaking_it(
    tls_smtp,
    monkeypatch,
    caplog,
) -> None:
    sink, crt = tls_smtp
    real = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: real(cafile=str(crt)))
    monkeypatch.setattr(settings, "mail_password", SecretStr("wrong-password-value"))
    monkeypatch.setattr(settings, "rq_sync", False)
    monkeypatch.setattr(settings, "email_job_max_retries", 0)
    caplog.set_level(logging.DEBUG)
    email_service.send_notification_email("someone@example.com", "S", "T", "B")
    _burst()
    assert sink.messages == []
    assert len(FailedJobRegistry(queue=email_queue).get_job_ids()) == 1
    assert "wrong-password-value" not in caplog.text
    assert "someone@example.com" not in caplog.text  # masked address only


# --------------------------------------------------------------------------- #
# Full pipeline: register -> queue -> RQ worker -> SMTP -> link -> verified
# --------------------------------------------------------------------------- #
def test_registration_email_goes_through_the_worker_and_its_link_verifies(
    client,
    plain_smtp,
) -> None:
    addr = "pipeline.qa@example.com"
    r = client.post(
        "/auth/register",
        headers=ORIGIN,
        json={
            "full_name": "Pipeline QA",
            "email": addr,
            "password": "Pipeline-QA-pass-2026",
            "role": "DONOR",
        },
    )
    assert r.status_code == 201, r.text
    # Worker not running yet: the email waits in the queue; registration is done.
    assert email_queue.count == 1 and plain_smtp.messages == []
    _burst()
    assert len(plain_smtp.messages) == 1
    msg = plain_smtp.messages[0]
    assert msg["To"] == addr and msg["Subject"] == "Verify your LifeLink email"
    token = _link(msg)

    assert (
        client.post(
            "/auth/login", headers=ORIGIN, json={"email": addr, "password": "Pipeline-QA-pass-2026"}
        ).status_code
        == 200
    )
    assert client.get("/notifications", headers=ORIGIN).status_code == 403  # unverified
    assert (
        client.post("/auth/verify-email", headers=ORIGIN, json={"token": token}).status_code == 200
    )
    assert client.get("/notifications", headers=ORIGIN).status_code == 200
    # Reused link
    assert (
        client.post("/auth/verify-email", headers=ORIGIN, json={"token": token}).status_code == 400
    )


def test_resend_through_the_worker_supersedes_the_first_link(client, plain_smtp) -> None:
    addr = "resend.qa@example.com"
    client.post(
        "/auth/register",
        headers=ORIGIN,
        json={
            "full_name": "Resend QA",
            "email": addr,
            "password": "Resend-QA-pass-2026",
            "role": "DONOR",
        },
    )
    _burst()
    first = _link(plain_smtp.messages[-1])
    r = client.post("/auth/verify-email/resend", headers=ORIGIN, json={"email": addr})
    assert r.status_code == 202
    _burst()
    assert len(plain_smtp.messages) == 2
    second = _link(plain_smtp.messages[-1])
    assert second != first
    assert (
        client.post("/auth/verify-email", headers=ORIGIN, json={"token": first}).status_code == 400
    )
    assert (
        client.post("/auth/verify-email", headers=ORIGIN, json={"token": second}).status_code == 200
    )


def test_zero_retries_means_one_attempt_not_a_crash(client, plain_smtp, monkeypatch) -> None:
    """Regression: EMAIL_JOB_MAX_RETRIES=0 made every enqueue raise (rq
    rejects Retry(max=0)), turning registration into an HTTP 500."""
    monkeypatch.setattr(settings, "email_job_max_retries", 0)
    r = client.post(
        "/auth/register",
        headers=ORIGIN,
        json={
            "full_name": "Zero QA",
            "email": "zero.qa@example.com",
            "password": "Zero-QA-pass-2026",
            "role": "DONOR",
        },
    )
    assert r.status_code == 201, r.text
    _burst()
    assert len(plain_smtp.messages) == 1


def test_smtp_down_does_not_lose_the_email(client, monkeypatch) -> None:
    """Nothing listening on the SMTP port: the job fails into the failed
    registry (retry policy applies) -- the registration itself succeeded."""
    port = _free_port()
    for k, v in {
        "mail_host": "127.0.0.1",
        "mail_port": port,
        "mail_starttls": False,
        "mail_username": None,
        "mail_password": None,
        "rq_sync": False,
        "email_job_max_retries": 1,
    }.items():
        monkeypatch.setattr(settings, k, v)
    r = client.post(
        "/auth/register",
        headers=ORIGIN,
        json={
            "full_name": "Down QA",
            "email": "down.qa@example.com",
            "password": "Down-QA-pass-2026",
            "role": "DONOR",
        },
    )
    assert r.status_code == 201
    _burst()
    assert len(FailedJobRegistry(queue=email_queue).get_job_ids()) == 1
