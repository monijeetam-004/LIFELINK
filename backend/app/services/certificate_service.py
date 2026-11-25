"""Donation certificate generation, storage and signed-download tokens.

Reuses the S3/MinIO storage layer (``app.core.storage``), the HMAC helper
(``app.core.security.sign``), audit and notification services.
"""
from __future__ import annotations

import io
import logging
import secrets
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import settings
from app.core.security import constant_time_equals, sign
from app.models.certificate import Certificate
from app.models.donation import Donation
from app.models.user import User
from app.services import audit_service, notification_service

_PDF_CONTENT_TYPE = "application/pdf"


def _make_serial() -> str:
    from datetime import datetime

    year = datetime.now().year
    return f"LL-{year}-{secrets.token_hex(4).upper()}"


def _storage_key(donor_id: uuid.UUID, cert_id: uuid.UUID) -> str:
    return f"certificates/{donor_id}/{cert_id}.pdf"


def verify_url_path(verify_code: str) -> str:
    return f"/verify/{verify_code}"


# --------------------------------------------------------------------------- #
# PDF rendering (ReportLab)
# --------------------------------------------------------------------------- #
def render_pdf(*, donor_name: str, serial: str, verify_code: str,
               donated_on: str, units: int, hospital_name: str | None) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page = landscape(A4)
    w, h = page
    c = canvas.Canvas(buf, pagesize=page)
    c.setTitle(f"LifeLink Certificate {serial}")

    primary = HexColor("#B3261E")
    ink = HexColor("#1B1B1F")
    muted = HexColor("#5F5F66")

    # Border frame
    c.setStrokeColor(primary)
    c.setLineWidth(3)
    c.rect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm)
    c.setLineWidth(0.75)
    c.setStrokeColor(muted)
    c.rect(16 * mm, 16 * mm, w - 32 * mm, h - 32 * mm)

    c.setFillColor(primary)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(w / 2, h - 40 * mm, "LIFELINK")
    c.setFillColor(muted)
    c.setFont("Helvetica", 13)
    c.drawCentredString(w / 2, h - 50 * mm, "CERTIFICATE OF LIFE SAVING")

    c.setFillColor(ink)
    c.setFont("Helvetica-Oblique", 12)
    c.drawCentredString(w / 2, h - 68 * mm,
                        "This is to formally certify the meritorious contribution of")

    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(w / 2, h - 84 * mm, donor_name)
    c.setStrokeColor(muted)
    c.setLineWidth(0.75)
    name_w = c.stringWidth(donor_name, "Helvetica-Bold", 26)
    c.line(w / 2 - max(name_w, 120 * mm) / 2, h - 88 * mm,
           w / 2 + max(name_w, 120 * mm) / 2, h - 88 * mm)

    detail = f"Blood donation of {units} unit(s) on {donated_on}"
    if hospital_name:
        detail += f" at {hospital_name}"
    c.setFont("Helvetica", 12)
    c.setFillColor(ink)
    c.drawCentredString(w / 2, h - 100 * mm, detail)
    c.setFillColor(muted)
    c.setFont("Helvetica", 10)
    c.drawCentredString(
        w / 2, h - 110 * mm,
        "Your donation directly supports patients during critical shortages.",
    )

    # Footer: serial + verification + QR
    c.setFillColor(muted)
    c.setFont("Helvetica", 9)
    c.drawString(24 * mm, 26 * mm, f"Serial: {serial}")
    c.drawString(24 * mm, 21 * mm,
                 f"Verify at: {settings.frontend_base_url}{verify_url_path(verify_code)}")

    try:
        from reportlab.graphics import renderPDF
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing

        qr = QrCodeWidget(f"{settings.frontend_base_url}{verify_url_path(verify_code)}")
        b = qr.getBounds()
        size = 26 * mm
        d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0,
                                           size / (b[3] - b[1]), 0, 0])
        d.add(qr)
        renderPDF.draw(d, c, w - 24 * mm - size, 20 * mm)
    except Exception:  # pragma: no cover - QR is decorative
        # Phase L: the certificate is still valid without the QR (the
        # printed code + verify URL carry the same information), but a
        # silently missing QR would be invisible to operators.
        logging.getLogger("lifelink.certificates").warning(
            "certificate QR rendering failed; PDF issued without QR", exc_info=True
        )

    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Issue / fetch
# --------------------------------------------------------------------------- #
def get_or_issue(db: Session, donor: User, donation: Donation) -> Certificate:
    existing = db.scalar(
        select(Certificate).where(Certificate.donation_id == donation.id)
    )
    if existing is not None:
        return existing

    cert = Certificate(
        id=uuid.uuid4(),
        donation_id=donation.id,
        donor_user_id=donor.id,
        serial=_make_serial(),
        verify_code=secrets.token_urlsafe(16),
        storage_key="",
    )
    cert.storage_key = _storage_key(donor.id, cert.id)

    pdf = render_pdf(
        donor_name=donor.full_name,
        serial=cert.serial,
        verify_code=cert.verify_code,
        donated_on=donation.donated_on.isoformat(),
        units=donation.units,
        hospital_name=donation.hospital_name,
    )
    # Phase 20: this write happens BEFORE `db.add(cert)` below (already the
    # case pre-Phase-20, not a new ordering) -- so if storage is down, no
    # DB row is ever created; the database can never claim a certificate
    # was issued when the PDF was never actually stored. Previously this
    # exception was unhandled and surfaced as a bare 500; wrapped here so
    # the route gets a class it can turn into an honest 503 instead.
    try:
        storage.put_bytes(cert.storage_key, pdf, _PDF_CONTENT_TYPE)
    except Exception as exc:
        raise CertificateStorageError("Failed to store the certificate PDF") from exc

    db.add(cert)
    db.flush()
    audit_service.record(db, "certificate.issued", actor_user_id=donor.id,
                         target_type="certificate", target_id=cert.id,
                         meta={"serial": cert.serial})
    notification_service.notify(
        db, donor.id, type="certificate.ready",
        title="Your donation certificate is ready",
        body=f"Certificate {cert.serial} for your donation on "
             f"{donation.donated_on.isoformat()}.",
        data={"certificate_id": str(cert.id)},
    )
    return cert


class CertificateStorageError(Exception):
    """Raised when a certificate PDF cannot be written to or read from
    object storage. The route layer turns this into an honest 503 --
    never a bare unhandled exception, and never a false "issued"/
    "downloaded" response."""


def load_pdf(cert: Certificate) -> bytes:
    return storage.get_bytes(cert.storage_key)


# --------------------------------------------------------------------------- #
# Signed download token (lets the browser fetch without the session cookie)
# --------------------------------------------------------------------------- #
def make_download_token(cert_id: uuid.UUID, ttl: int | None = None) -> str:
    exp = int(time.time()) + (ttl or settings.certificate_download_ttl_seconds)
    payload = f"{cert_id}:{exp}"
    return f"{exp}.{sign(payload)}"


def check_download_token(cert_id: uuid.UUID, token: str) -> bool:
    try:
        exp_str, digest = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    return constant_time_equals(sign(f"{cert_id}:{exp}"), digest)
