"""Build LifeLink's local ClamAV signature database.

Why this exists: production deployments run `freshclam` against the
official ClamAV database (main/daily/bytecode). Development and CI
machines often cannot reach database.clamav.net, and clamd refuses to start
with no signatures at all. This script writes a small, honest local
database so clamd can run anywhere:

  lifelink-local.ndb  body signatures
    * LifeLink.Test.EICAR            the industry-standard harmless EICAR test string
    * LifeLink.Policy.PDF.JavaScript a PDF containing a /JavaScript action
    * LifeLink.Policy.PDF.Launch     a PDF containing a /Launch action
    * LifeLink.Policy.PDF.EmbeddedFile  a PDF carrying an embedded file
    * LifeLink.Policy.PDF.RichMedia  a PDF with Flash/RichMedia content
    The PDF rules are a POLICY: identity/facility documents never need
    active content, so LifeLink rejects PDFs that carry it.
  lifelink-local.hdb  MD5 hash signatures of ClamAV's own harmless test
    executables (Debian/Ubuntu package `clamav-testfiles`), so packed-file
    detection can be exercised without any real malware.

It never downloads anything and contains no real malware. In production,
use the official database IN ADDITION to (or instead of) this file.

Usage: python build_local_signatures.py OUT_DIR [TESTFILES_DIR]
"""
from __future__ import annotations

import hashlib
import pathlib
import sys


def hexs(s: bytes) -> str:
    return s.hex()


NDB = [
    # Name:TargetType:Offset:HexSignature   (TargetType 0 = any file)
    ("LifeLink.Test.EICAR", 0, "*", hexs(b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE")),
    # PDF policy rules: anchored to files that start with %PDF- (offset 0) and
    # contain the active-content name anywhere after it.
    ("LifeLink.Policy.PDF.JavaScript", 0, "0", hexs(b"%PDF-") + "*" + hexs(b"/JavaScript")),
    ("LifeLink.Policy.PDF.JS", 0, "0", hexs(b"%PDF-") + "*" + hexs(b"/JS")
     + "(20|28|3c|0a|0d)"),
    ("LifeLink.Policy.PDF.Launch", 0, "0", hexs(b"%PDF-") + "*" + hexs(b"/Launch")),
    ("LifeLink.Policy.PDF.EmbeddedFile", 0, "0", hexs(b"%PDF-") + "*" + hexs(b"/EmbeddedFile")),
    ("LifeLink.Policy.PDF.RichMedia", 0, "0", hexs(b"%PDF-") + "*" + hexs(b"/RichMedia")),
]


def main(out_dir: str, testfiles_dir: str = "/usr/share/clamav-testfiles") -> int:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "lifelink-local.ndb").write_text(
        "".join(f"{n}:{t}:{o}:{sig}\n" for n, t, o, sig in NDB)
    )
    lines = []
    tdir = pathlib.Path(testfiles_dir)
    if tdir.is_dir():
        for f in sorted(tdir.iterdir()):
            if f.is_file() and f.name.startswith("clam") and f.suffix == ".exe":
                data = f.read_bytes()
                lines.append(f"{hashlib.md5(data).hexdigest()}:{len(data)}:"
                             f"LifeLink.Test.ClamAVTestFile.{f.stem}\n")
    (out / "lifelink-local.hdb").write_text("".join(lines))
    print(f"wrote {len(NDB)} body signatures and {len(lines)} hash signatures to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
