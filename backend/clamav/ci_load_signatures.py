"""Load LifeLink's local signatures into a running CI clamd and wait for them.

Why: CI runs the stock `clamav/clamav` image, which only has the official
database. LifeLink's PDF policy rules (LifeLink.Policy.PDF.*) live in
`build_local_signatures.py`, so without this step clamd never flags a PDF
with JavaScript and the real-clamd tests fail.

What it does:
  1. builds lifelink-local.ndb with build_local_signatures.py;
  2. copies it into the clamd container's database directory (docker cp);
  3. sends clamd RELOAD;
  4. scans a PDF with a /JavaScript action until clamd reports a
     LifeLink.Policy.PDF rule (reload is asynchronous), or fails after a timeout.

Usage: python ci_load_signatures.py CONTAINER_ID [HOST] [PORT]
"""
from __future__ import annotations

import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_local_signatures  # noqa: E402

JS_PDF = (
    b"%PDF-1.4\n1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n"
    b"2 0 obj << /S /JavaScript /JS (app.alert('x')) >> endobj\ntrailer << /Root 1 0 R >>\n"
    b"%%EOF\n"
)


def clamd(host: str, port: int, command: bytes, payload: bytes | None = None) -> str:
    with socket.create_connection((host, port), timeout=30) as s:
        s.sendall(command)
        if payload is not None:
            s.sendall(struct.pack("!I", len(payload)) + payload + struct.pack("!I", 0))
        chunks = []
        while True:
            part = s.recv(4096)
            if not part:
                break
            chunks.append(part)
            if part.endswith(b"\0"):
                break
    return b"".join(chunks).rstrip(b"\0").decode(errors="replace")


def main(container: str, host: str = "127.0.0.1", port: str = "3310") -> int:
    p = int(port)
    with tempfile.TemporaryDirectory() as tmp:
        build_local_signatures.main(tmp)
        # Only the body-signature file: the .hdb is empty on CI runners
        # (no clamav-testfiles), and clamd rejects an empty database file.
        subprocess.run(
            ["docker", "cp", f"{tmp}/lifelink-local.ndb",
             f"{container}:/var/lib/clamav/lifelink-local.ndb"],
            check=True,
        )
    print("reload:", clamd(host, p, b"zRELOAD\0"))
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            reply = clamd(host, p, b"zINSTREAM\0", JS_PDF)
        except OSError as exc:  # clamd briefly busy while reloading
            reply = f"error: {exc}"
        if "LifeLink.Policy.PDF" in reply:
            print("LifeLink signatures active:", reply)
            return 0
        time.sleep(3)
    print("LifeLink signatures did not load in time; last reply:", reply, file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
