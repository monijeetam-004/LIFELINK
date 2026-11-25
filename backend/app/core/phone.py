"""Phone number validation and normalization.

LifeLink stores phone numbers as free-text strings on the role-specific
profile tables (``PatientProfile.phone``, ``DonorProfile.phone``,
``HospitalProfile.phone``, ``PatientProfile.emergency_contact_phone``).
This module is the single place that turns whatever a user typed into a
normalized E.164 string (e.g. ``+919876543210``), or rejects it outright --
every profile-update schema that accepts a phone number calls
:func:`normalize_phone_e164` through a Pydantic field validator rather than
storing raw user input.

Real validation via Google's libphonenumber (the ``phonenumbers`` package),
not a hand-rolled regex -- a regex can't tell a real Indian mobile number
from ten random digits, and it's exactly the "reject malformed numbers"
requirement from the SMS provider integration spec.

LifeLink targets Indian users by default (SMS_DEFAULT_REGION), but this
module doesn't hard-reject non-Indian numbers -- a HospitalProfile or
DonorProfile could legitimately belong to someone reachable on a foreign
number. What matters for SMS delivery is that the stored form is always a
valid, complete E.164 number; MSG91's India-specific DLT rules are enforced
separately, at send time (see app/services/sms/msg91.py), not here.
"""
from __future__ import annotations

import phonenumbers

from app.core.config import settings


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number cannot be parsed/validated."""


def normalize_phone_e164(raw: str | None) -> str | None:
    """Parse ``raw`` and return it as a normalized E.164 string.

    ``None`` or an empty/whitespace-only string returns ``None`` (clearing
    the field is always allowed). Anything else that doesn't parse as a
    valid, *possible and valid* number for its region raises
    :class:`InvalidPhoneNumberError` with a message safe to surface as a
    422 validation error (it never echoes back more than the rejected
    input already was).

    Numbers with an explicit ``+<country code>`` are parsed as international
    regardless of region. A bare national number (e.g. ``9876543210``) is
    parsed against ``settings.sms_default_region`` (``IN`` by default).
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = phonenumbers.parse(stripped, settings.sms_default_region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhoneNumberError(f"'{stripped}' is not a valid phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumberError(f"'{stripped}' is not a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def validate_phone_field(raw: str | None) -> str | None:
    """Pydantic field-validator body: normalize or raise ``ValueError``
    (which Pydantic turns into a 422) -- shared by every profile-update
    schema that accepts a phone number (see app/schemas/profile.py and
    app/schemas/hospital.py)."""
    try:
        return normalize_phone_e164(raw)
    except InvalidPhoneNumberError as exc:
        raise ValueError(str(exc)) from exc


def is_indian_number(e164: str) -> bool:
    """True when ``e164`` (already-normalized) is a +91 India number."""
    return e164.startswith("+91")


def mask_phone(e164: str | None) -> str | None:
    """Mask a phone number for logs/audit records: keep the country code and
    last 2 digits, blank out the rest (e.g. ``+919876543210`` -> ``+91******10``).

    Never store or log a full phone number in the SMS audit trail
    (SmsNotification) -- this is the one place that trade-off is made.
    """
    if not e164:
        return None
    if not e164.startswith("+") or len(e164) < 6:
        return "***"
    country_and_hidden = e164[:-2]
    last_two = e164[-2:]
    # Keep the leading "+<country code>" (first 3-4 chars) visible, mask the
    # rest of the subscriber number.
    visible_prefix_len = min(3, len(country_and_hidden))
    prefix = country_and_hidden[:visible_prefix_len]
    hidden_len = max(0, len(country_and_hidden) - visible_prefix_len)
    return f"{prefix}{'*' * hidden_len}{last_two}"
