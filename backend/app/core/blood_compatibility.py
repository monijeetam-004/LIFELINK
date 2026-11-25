"""Centralized, authoritative ABO/Rh red-cell compatibility engine.

Phase 9 requirement: "Create one authoritative domain implementation. Do
NOT duplicate compatibility logic in: frontend, controller, hospital page,
donor page, analytics. Everything must call the central engine."

Before this module existed, the compatibility matrix (`_CAN_DONATE_TO`,
`can_donate_to`, `compatible_donor_types`) lived inside `app.models.enums`
-- a large grab-bag of unrelated domain enums -- and was already the
single backend source of truth (an audit of every backend call site found
no second implementation). The one real duplication was on the frontend:
`BloodCompatibilityChart.tsx` had its own hand-transcribed copy of the
same table. This module is now the one place the matrix is defined; the
backend routes that used to import it from `app.models.enums` import it
from here instead, and the frontend consumes it over the wire via
`GET /matching/blood-compatibility` (see `app.api.routes.matching`)
instead of hardcoding it.

SCOPE / ASSUMPTIONS (documented explicitly, per the Phase 9 spec):
  * Red-cell (whole blood) donor -> recipient compatibility only. This
    system does not model donation components (platelets, plasma, etc.)
    -- see the comment on `DonorProfile.alert_min_urgency` in
    app/models/profile.py, which was already a deliberate product
    decision predating this phase. `compatible_donor_types` /
    `compatible_recipient_types` describe red-cell transfusion safety,
    nothing else.
  * The eight canonical groups are exactly `BloodType`'s eight members.
    There is no "unknown"/"other" group; a `None` blood type (donor or
    recipient hasn't recorded one yet) is a distinct, separately-handled
    case at every call site -- this module only ever operates on real
    `BloodType` values, never on `None`.
  * UI labels ("A+", "O-", ...) are a presentation-only alias for the
    canonical enum members. `to_ui_label`/`normalize_blood_type` are the
    single authoritative mapping between the two; nothing else (frontend
    included) should hardcode this table.
  * `normalize_blood_type` accepts case-insensitive UI labels
    ("a+", "AB-") and canonical names in any case/whitespace ("o_neg",
    " O_NEG "), so a caller can safely take either shape from a client.
    Anything else raises `ValueError` with a message safe to show a user.
"""
from __future__ import annotations

from app.models.enums import BloodType

# Donor blood type -> set of recipient blood types it can donate red cells
# to. This is the one and only definition of the compatibility matrix.
_CAN_DONATE_TO: dict[BloodType, frozenset[BloodType]] = {
    BloodType.O_NEG: frozenset(BloodType),
    BloodType.O_POS: frozenset(
        {BloodType.O_POS, BloodType.A_POS, BloodType.B_POS, BloodType.AB_POS}
    ),
    BloodType.A_NEG: frozenset(
        {BloodType.A_NEG, BloodType.A_POS, BloodType.AB_NEG, BloodType.AB_POS}
    ),
    BloodType.A_POS: frozenset({BloodType.A_POS, BloodType.AB_POS}),
    BloodType.B_NEG: frozenset(
        {BloodType.B_NEG, BloodType.B_POS, BloodType.AB_NEG, BloodType.AB_POS}
    ),
    BloodType.B_POS: frozenset({BloodType.B_POS, BloodType.AB_POS}),
    BloodType.AB_NEG: frozenset({BloodType.AB_NEG, BloodType.AB_POS}),
    BloodType.AB_POS: frozenset({BloodType.AB_POS}),
}

# The single authoritative canonical<->UI-label mapping. Declaration order
# doubles as the canonical display order used by `compatible_donor_types`
# / `compatible_recipient_types` (both iterate `BloodType`, whose member
# order matches this).
UI_LABELS: dict[BloodType, str] = {
    BloodType.A_POS: "A+",
    BloodType.A_NEG: "A-",
    BloodType.B_POS: "B+",
    BloodType.B_NEG: "B-",
    BloodType.AB_POS: "AB+",
    BloodType.AB_NEG: "AB-",
    BloodType.O_POS: "O+",
    BloodType.O_NEG: "O-",
}
_LABEL_TO_TYPE: dict[str, BloodType] = {label: bt for bt, label in UI_LABELS.items()}


def can_donate_to(donor: BloodType, recipient: BloodType) -> bool:
    """Whether `donor`'s red cells are safe to transfuse into `recipient`."""
    return recipient in _CAN_DONATE_TO[donor]


def compatible_donor_types(recipient: BloodType) -> list[BloodType]:
    """Blood types whose red cells `recipient` can safely receive, in
    canonical `BloodType` declaration order."""
    return [d for d in BloodType if recipient in _CAN_DONATE_TO[d]]


def compatible_recipient_types(donor: BloodType) -> list[BloodType]:
    """Blood types `donor`'s red cells can safely be given to, in
    canonical `BloodType` declaration order. The donor-side inverse of
    `compatible_donor_types` -- added for Phase 9's full compatibility
    surface (`GET /matching/blood-compatibility`); no backend caller
    needed this direction before."""
    return [r for r in BloodType if r in _CAN_DONATE_TO[donor]]


def to_ui_label(blood_type: BloodType) -> str:
    """Canonical group -> UI label, e.g. `BloodType.A_NEG` -> `"A-"`."""
    return UI_LABELS[blood_type]


def normalize_blood_type(value: str) -> BloodType:
    """Parse a canonical group name or UI label into a `BloodType`.

    Tolerant of surrounding whitespace and case in both directions
    ("a+", " O_NEG ", "ab-", "AB_pos"). Raises `ValueError` (with a
    message safe to surface to an API caller) for anything that is
    neither a known canonical name nor a known UI label, and for an
    empty/whitespace-only value.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Blood group is required")
    upper = cleaned.upper()
    if upper in _LABEL_TO_TYPE:
        return _LABEL_TO_TYPE[upper]
    try:
        return BloodType(upper)
    except ValueError as exc:
        raise ValueError(f"Unrecognized blood group: {value!r}") from exc


def try_normalize_blood_type(value: str | None) -> BloodType | None:
    """Same as `normalize_blood_type`, but degrades to `None` for a
    missing or unrecognized value instead of raising -- for call sites
    that already treat "no blood type on file" as a valid state rather
    than an error (matches the existing `blood_type: BloodType | None`
    convention used throughout the profile/request models)."""
    if not value:
        return None
    try:
        return normalize_blood_type(value)
    except ValueError:
        return None
