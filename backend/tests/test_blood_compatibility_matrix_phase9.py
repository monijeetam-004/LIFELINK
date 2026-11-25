"""PHASE 9 -- full 64-combination blood compatibility test matrix.

Tests `app.core.blood_compatibility`, the one authoritative domain
implementation the Phase 9 spec calls for. The 8x8 truth table below is
transcribed independently from standard ABO/Rh red-cell transfusion
compatibility rules (not derived from the implementation under test), so
these tests actually check correctness rather than tautologically
re-asserting whatever the code already computes.

ASSUMPTIONS (see also the module docstring of `app.core.blood_compatibility`):
  * Red-cell (whole blood) donation only -- no platelet/plasma/component
    compatibility exists in this system to test.
  * The eight canonical `BloodType` members are the only valid groups;
    there is no ninth "unknown" group. A missing/invalid blood group is
    a distinct case (`normalize_blood_type` raising, or
    `try_normalize_blood_type` returning `None`), tested separately
    below rather than folded into the 64-combination matrix.
  * UI labels ("A+", "O-", ...) are presentation-only aliases for the
    canonical enum members; normalization is case- and
    whitespace-tolerant in both directions.
"""
from __future__ import annotations

import pytest

from app.core.blood_compatibility import (
    UI_LABELS,
    can_donate_to,
    compatible_donor_types,
    compatible_recipient_types,
    normalize_blood_type,
    to_ui_label,
    try_normalize_blood_type,
)
from app.models.enums import BloodType
from tests.helpers import auth_client

# Independently-authored ABO/Rh red-cell compatibility ground truth:
# donor type -> the set of recipient types it may safely donate to.
# O- is the universal red-cell donor; AB+ is the universal recipient.
_EXPECTED_CAN_DONATE_TO: dict[str, set[str]] = {
    "O_NEG": {"O_NEG", "O_POS", "A_NEG", "A_POS", "B_NEG", "B_POS", "AB_NEG", "AB_POS"},
    "O_POS": {"O_POS", "A_POS", "B_POS", "AB_POS"},
    "A_NEG": {"A_NEG", "A_POS", "AB_NEG", "AB_POS"},
    "A_POS": {"A_POS", "AB_POS"},
    "B_NEG": {"B_NEG", "B_POS", "AB_NEG", "AB_POS"},
    "B_POS": {"B_POS", "AB_POS"},
    "AB_NEG": {"AB_NEG", "AB_POS"},
    "AB_POS": {"AB_POS"},
}

_ALL_TYPES = [
    "A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG",
]

_EXPECTED_UI_LABELS = {
    "A_POS": "A+", "A_NEG": "A-", "B_POS": "B+", "B_NEG": "B-",
    "AB_POS": "AB+", "AB_NEG": "AB-", "O_POS": "O+", "O_NEG": "O-",
}

# All 64 donor x recipient combinations, each with its independently-known
# expected outcome -- the literal "TEST MATRIX: create all 64
# donor-recipient combinations" from the Phase 9 spec.
_ALL_64_COMBINATIONS = [
    (donor, recipient, recipient in _EXPECTED_CAN_DONATE_TO[donor])
    for donor in _ALL_TYPES
    for recipient in _ALL_TYPES
]


def test_matrix_has_all_64_combinations() -> None:
    assert len(_ALL_64_COMBINATIONS) == 64
    assert len({(d, r) for d, r, _ in _ALL_64_COMBINATIONS}) == 64


@pytest.mark.parametrize("donor,recipient,expected", _ALL_64_COMBINATIONS)
def test_can_donate_to_matrix(donor: str, recipient: str, expected: bool) -> None:
    assert can_donate_to(BloodType(donor), BloodType(recipient)) is expected


def test_compatible_donor_types_matches_matrix_for_every_recipient() -> None:
    """The recipient-side view (`compatible_donor_types`) must agree with
    the same ground truth, from the opposite direction."""
    for recipient in _ALL_TYPES:
        expected_donors = {
            d for d in _ALL_TYPES if recipient in _EXPECTED_CAN_DONATE_TO[d]
        }
        actual = {d.value for d in compatible_donor_types(BloodType(recipient))}
        assert actual == expected_donors, f"recipient={recipient}"


def test_compatible_recipient_types_matches_matrix_for_every_donor() -> None:
    """The donor-side view (`compatible_recipient_types`) must agree with
    the same ground truth."""
    for donor in _ALL_TYPES:
        actual = {r.value for r in compatible_recipient_types(BloodType(donor))}
        assert actual == _EXPECTED_CAN_DONATE_TO[donor], f"donor={donor}"


def test_universal_donor_and_universal_recipient() -> None:
    """O- can donate to all 8 groups; AB+ can receive from all 8 groups --
    the two textbook edge cases worth naming explicitly."""
    assert set(compatible_recipient_types(BloodType.O_NEG)) == set(BloodType)
    assert set(compatible_donor_types(BloodType.AB_POS)) == set(BloodType)


def test_self_compatibility_always_true() -> None:
    """Every blood type can donate to its own type (a same-group
    transfusion is always ABO/Rh compatible)."""
    for bt in BloodType:
        assert can_donate_to(bt, bt) is True


# ---- UI representation --------------------------------------------------

def test_ui_labels_match_expected_and_are_exhaustive() -> None:
    assert {bt.value: label for bt, label in UI_LABELS.items()} == _EXPECTED_UI_LABELS
    assert set(UI_LABELS.keys()) == set(BloodType)


@pytest.mark.parametrize("canonical,label", list(_EXPECTED_UI_LABELS.items()))
def test_to_ui_label(canonical: str, label: str) -> None:
    assert to_ui_label(BloodType(canonical)) == label


# ---- Normalization --------------------------------------------------------

@pytest.mark.parametrize("canonical,label", list(_EXPECTED_UI_LABELS.items()))
def test_normalize_ui_label_round_trip(canonical: str, label: str) -> None:
    assert normalize_blood_type(label) == BloodType(canonical)
    # Case-insensitive.
    assert normalize_blood_type(label.lower()) == BloodType(canonical)


@pytest.mark.parametrize("canonical", _ALL_TYPES)
def test_normalize_canonical_name_tolerant_of_case_and_whitespace(canonical: str) -> None:
    assert normalize_blood_type(canonical) == BloodType(canonical)
    assert normalize_blood_type(canonical.lower()) == BloodType(canonical)
    assert normalize_blood_type(f"  {canonical}  ") == BloodType(canonical)


def test_normalize_missing_blood_group_raises() -> None:
    with pytest.raises(ValueError):
        normalize_blood_type("")
    with pytest.raises(ValueError):
        normalize_blood_type("   ")


def test_normalize_invalid_blood_group_raises() -> None:
    for bad in ("X+", "O_POSITIVE", "not a blood type", "AB", "O"):
        with pytest.raises(ValueError):
            normalize_blood_type(bad)


def test_try_normalize_degrades_gracefully() -> None:
    assert try_normalize_blood_type(None) is None
    assert try_normalize_blood_type("") is None
    assert try_normalize_blood_type("not a blood type") is None
    assert try_normalize_blood_type("a+") == BloodType.A_POS
    assert try_normalize_blood_type(" O_NEG ") == BloodType.O_NEG


# ---- Wire-level: GET /matching/blood-compatibility ------------------------

def test_blood_compatibility_endpoint_matches_the_matrix() -> None:
    """Any signed-in user can fetch the full matrix over the wire -- this
    is what the frontend now calls instead of hardcoding the table."""
    client, _ = auth_client("PATIENT")
    resp = client.get("/matching/blood-compatibility")
    assert resp.status_code == 200
    body = resp.json()
    groups = {g["blood_type"]: g for g in body["groups"]}
    assert set(groups) == set(_ALL_TYPES)
    for bt in _ALL_TYPES:
        g = groups[bt]
        assert g["ui_label"] == _EXPECTED_UI_LABELS[bt]
        assert set(g["can_donate_to"]) == _EXPECTED_CAN_DONATE_TO[bt]
        assert set(g["can_receive_from"]) == {
            d for d in _ALL_TYPES if bt in _EXPECTED_CAN_DONATE_TO[d]
        }


def test_blood_compatibility_endpoint_requires_auth() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/matching/blood-compatibility").status_code == 401
