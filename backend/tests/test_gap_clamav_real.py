"""Gap elimination section 13: malware scanning against a REAL clamd.

Signature database honesty: in this sandbox the official database
(database.clamav.net) is unreachable, so clamd loads the project-local
database built by `clamav/build_local_signatures.py`:
  * LifeLink.Test.EICAR       -- the industry-standard EICAR test string;
  * LifeLink.Policy.PDF.*     -- POLICY rules that flag active content in
    PDFs (JavaScript, launch actions, embedded files); "suspicious", not
    "known malware";
  * MD5 hashes of the harmless ClamAV project test files
    (/usr/share/clamav-testfiles, Debian package clamav-testfiles).
No real malware is downloaded or stored. With the official database the
same tests apply (EICAR and the clam* test files are in it too).

Every outcome the spec lists is covered: clean, EICAR, suspicious,
unsupported type, oversized, corrupted, timeout, unavailable.
"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.verification import VerificationRequest
from app.services.document_scanner import ClamdScanner, ScanVerdict
from tests.helpers import auth_client
from tests.test_hardening_malware_scanning import (
    EICAR,
    REAL_HOST,
    REAL_PORT,
    _submit,
    needs_real_clamd,
    real_clamd,  # noqa: F401 - fixture re-export
)

TESTFILES = Path("/usr/share/clamav-testfiles")
JS_PDF = (
    b"%PDF-1.4\n1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n"
    b"2 0 obj << /S /JavaScript /JS (app.alert('x')) >> endobj\ntrailer << /Root 1 0 R >>\n"
    b"%%EOF\n"
)


@needs_real_clamd
def test_real_clean_eicar_and_suspicious_pdf_verdicts():
    scanner = ClamdScanner(REAL_HOST, REAL_PORT, 10.0)
    assert scanner.scan(b"%PDF-1.4 ordinary document").verdict == ScanVerdict.CLEAN
    eicar = scanner.scan(EICAR)
    assert eicar.verdict == ScanVerdict.INFECTED and "EICAR" in eicar.signature
    js = scanner.scan(JS_PDF)
    assert js.verdict == ScanVerdict.INFECTED and "PDF.JavaScript" in js.signature


@needs_real_clamd
@pytest.mark.skipif(not (TESTFILES / "clam.exe").exists(), reason="clamav-testfiles not installed")
def test_real_clamav_project_test_files_are_detected():
    scanner = ClamdScanner(REAL_HOST, REAL_PORT, 20.0)
    for name in ("clam.exe", "clam-upx.exe"):
        r = scanner.scan((TESTFILES / name).read_bytes())
        assert r.verdict == ScanVerdict.INFECTED, (name, r)


@needs_real_clamd
def test_real_eicar_and_suspicious_uploads_are_refused_and_never_stored(real_clamd):  # noqa: F811
    donor, _ = auth_client("DONOR")
    for payload in (b"%PDF-1.4\n" + EICAR, JS_PDF):
        r = _submit(donor, files=[("id.pdf", payload, "application/pdf")])
        assert r.status_code == 422, r.text
    with SessionLocal() as db:
        assert db.scalar(select(VerificationRequest)) is None


@needs_real_clamd
def test_unsupported_oversized_and_corrupted_files_are_rejected_before_scanning(
    real_clamd,  # noqa: F811 - pytest fixture injected by name
    monkeypatch,
):
    from app.core.config import settings

    donor, _ = auth_client("DONOR")
    exe = _submit(donor, files=[("tool.exe", b"MZ\x90\x00", "application/x-msdownload")])
    assert exe.status_code in (415, 422)
    corrupted = _submit(donor, files=[("id.pdf", b"\x00\x01garbage-not-a-pdf", "application/pdf")])
    assert corrupted.status_code in (415, 422)
    monkeypatch.setattr(settings, "verification_max_file_bytes", 64)
    big = _submit(donor, files=[("id.pdf", b"%PDF-1.4" + b"0" * 200, "application/pdf")])
    assert big.status_code in (413, 422)
    with SessionLocal() as db:
        assert db.scalar(select(VerificationRequest)) is None


def test_timeout_against_a_silent_server_is_a_scan_error_never_clean():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_and_stall():
        conn, _ = srv.accept()
        stop.wait(5)
        conn.close()

    t = threading.Thread(target=accept_and_stall, daemon=True)
    t.start()
    try:
        r = ClamdScanner("127.0.0.1", port, 0.5).scan(b"%PDF-1.4 hello")
        assert r.verdict == ScanVerdict.ERROR
    finally:
        stop.set()
        srv.close()


def test_unavailable_daemon_is_a_scan_error_never_clean():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    assert ClamdScanner("127.0.0.1", port, 1.0).scan(b"x").verdict == ScanVerdict.ERROR


def test_required_real_clamd_is_really_there():
    if os.environ.get("CLAMD_TEST_REQUIRED", "").lower() in {"1", "true", "yes"}:
        assert ClamdScanner(REAL_HOST, REAL_PORT, 5.0).scan(b"ping").verdict == ScanVerdict.CLEAN
