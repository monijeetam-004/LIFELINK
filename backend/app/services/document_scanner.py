"""Malware scanning for uploaded documents (production hardening).

History: Phase 20 added this seam with only a `NoOpScanner` that always
reported "clean" without inspecting anything. This module now provides a
real scanner and explicit, honest verdicts.

Verdicts (`ScanVerdict`) -- the only three outcomes a scan can have:

  CLEAN     the scanner inspected the bytes and found nothing.
  INFECTED  the scanner matched a signature (`signature` says which).
  ERROR     no verdict: scanner unreachable, timed out, refused the
            stream, or answered something unparseable. NEVER treated as
            clean -- the document is quarantined (SCAN_FAILED) and only a
            later successful rescan can release it.

Document lifecycle (stored per document as `scan_status`, see
`app.services.document_quarantine`):

  PENDING_SCAN -> CLEAN | INFECTED | SCAN_FAILED ;  SCAN_FAILED -> (rescan) -> ...

Backends (`DOCUMENT_SCANNER` setting):

  "clamd"  `ClamdScanner`: ClamAV's clamd daemon over TCP using its
           documented INSTREAM protocol (zINSTREAM, 4-byte big-endian
           length-prefixed chunks, zero-length terminator). No extra Python
           dependency. Required in production (config refuses to start
           otherwise unless explicitly overridden).
  "none"   `DisabledScanner`: development/test only. Reports CLEAN with
           scanner name "noop" -- an explicit, recorded statement that NO scan
           happened (shown to admins as "not scanned -- scanning disabled"),
           never presented as a malware check.
"""

from __future__ import annotations

import enum
import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings


class ScanVerdict(str, enum.Enum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    scanner: str
    signature: str | None = None
    detail: str | None = None

    @property
    def clean(self) -> bool:  # kept for older call sites/tests
        return self.verdict == ScanVerdict.CLEAN


class DocumentSecurityScanner(Protocol):
    name: str

    def scan(self, data: bytes) -> ScanResult: ...  # pragma: no cover - protocol


class DisabledScanner:
    """Explicitly disabled scanning (dev/test). Not a security control."""

    # Kept as "noop" (the Phase 20 name) so documents stored before and
    # after this hardening use one marker for "no scan was performed".
    name = "noop"

    def scan(self, data: bytes) -> ScanResult:  # noqa: ARG002 - intentionally unused
        return ScanResult(ScanVerdict.CLEAN, self.name, detail="scanning disabled")


# Backwards-compatible alias (Phase 20 name).
NoOpScanner = DisabledScanner


class ClamdScanner:
    """ClamAV daemon client (INSTREAM over TCP)."""

    name = "clamd"
    _CHUNK = 64 * 1024

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def _error(self, detail: str) -> ScanResult:
        return ScanResult(ScanVerdict.ERROR, self.name, detail=detail)

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), self._CHUNK):
                    chunk = data[i : i + self._CHUNK]
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
        except TimeoutError:
            return self._error("scanner timed out")
        except OSError as exc:
            return self._error(f"scanner unavailable ({type(exc).__name__})")

        text = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
        # Documented replies: "stream: OK", "stream: <Name> FOUND",
        # "INSTREAM size limit exceeded. ERROR", "<...> ERROR".
        if text.endswith("FOUND"):
            body = text.split(":", 1)[-1].strip()
            return ScanResult(
                ScanVerdict.INFECTED, self.name, signature=body[: -len("FOUND")].strip()
            )
        if text == "stream: OK":
            return ScanResult(ScanVerdict.CLEAN, self.name)
        return self._error(f"unexpected scanner reply: {text[:120] or '<empty>'}")


def get_scanner() -> DocumentSecurityScanner:
    backend = settings.document_scanner.strip().lower()
    if backend == "clamd":
        return ClamdScanner(
            settings.clamd_host, settings.clamd_port, settings.clamd_timeout_seconds
        )
    return DisabledScanner()
