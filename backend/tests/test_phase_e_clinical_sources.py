"""Phase E -- clinical-rule source integrity.

Direct regression tests for `app.core.clinical_sources` and
`app.core.component_compatibility` (previously exercised only indirectly
through inventory tests):

  * every shelf-life entry carries complete, machine-readable provenance
  * the figures themselves are pinned (a silent edit fails here)
  * the whole rule table is pinned by fingerprint -- changing ANY field,
    including a URL or note, requires a deliberate version bump + re-pin
  * PLASMA's disclosed post-thaw limitation is still disclosed
  * a REAL unit's expiry is computed from the cited figure and records
    exactly which rule produced it (`expiry_rule_ref`)
  * the removed dead `blood_unit_shelf_life_days` setting stays removed
  * the plasma compatibility table is pinned exhaustively (16 ABO pairs)

If one of the pinned values below needs to change, that is a clinical
rule change: update the citation in the source module, bump its
RULESET_VERSION, and re-pin here in the same reviewed change.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core import clinical_sources as cs
from app.core import component_compatibility as cc
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.blood_unit import BloodUnit
from app.models.enums import BloodComponent as C
from app.models.enums import BloodType as BT
from tests.helpers import auth_client

# Re-pinned deliberately in the gap-elimination round (RULESET_VERSION
# 2026.10.10-1): the fingerprint now also covers the cited post-thaw rule
# (THAWED_PLASMA, AABB Circular of Information) and the storage temperature
# ranges used by the cold-chain evaluator. No shelf-life figure changed --
# test_shelf_life_figures_are_pinned still pins 35/42/5/365.
PINNED_SHELF_LIFE_FINGERPRINT = "7ce1d8855cb95759dfd726048d3df41ec9a532169a46e29f33437cd935e0b5ff"
PINNED_COMPAT_FINGERPRINT = "321c7aaafd796a2123171e1881383694360b4e6c21112a5d00c07b80749ad15f"


def test_every_component_has_an_entry() -> None:
    assert set(cs.COMPONENT_SHELF_LIFE) == set(C)
    for comp, entry in cs.COMPONENT_SHELF_LIFE.items():
        assert entry.component == comp


@pytest.mark.parametrize("comp", list(C))
def test_entry_has_complete_provenance(comp: C) -> None:
    e = cs.COMPONENT_SHELF_LIFE[comp]
    assert e.source_id.strip()
    assert e.source_organization.strip()
    assert e.source_document.strip()
    assert e.source_url.startswith("https://")
    assert e.rule.strip() and e.storage_condition.strip()
    assert e.retrieved_on.isoformat() == "2026-10-05"
    # source_version is allowed to be None (never invented), but if set it
    # must be non-empty.
    assert e.source_version is None or e.source_version.strip()


def test_shelf_life_figures_are_pinned() -> None:
    assert {c: cs.shelf_life_days(c) for c in C} == {
        C.WHOLE_BLOOD: 35, C.RED_BLOOD_CELLS: 42, C.PLATELETS: 5, C.PLASMA: 365,
    }


def test_ruleset_fingerprint_is_pinned() -> None:
    assert cs.RULESET_VERSION == "2026.10.10-1"
    assert cs.ruleset_fingerprint() == PINNED_SHELF_LIFE_FINGERPRINT, (
        "clinical_sources changed: re-cite, bump RULESET_VERSION, and re-pin "
        "deliberately -- never edit a clinical figure silently"
    )


def test_fingerprint_detects_any_field_change(monkeypatch) -> None:
    from dataclasses import replace

    original = cs.COMPONENT_SHELF_LIFE[C.PLATELETS]
    patched = dict(cs.COMPONENT_SHELF_LIFE)
    patched[C.PLATELETS] = replace(original, shelf_life_days=7)
    monkeypatch.setattr(cs, "COMPONENT_SHELF_LIFE", patched)
    assert cs.ruleset_fingerprint() != PINNED_SHELF_LIFE_FINGERPRINT
    patched[C.PLATELETS] = replace(original, source_url=original.source_url + "?x")
    assert cs.ruleset_fingerprint() != PINNED_SHELF_LIFE_FINGERPRINT


def test_plasma_post_thaw_window_is_cited_and_enforced() -> None:
    """Formerly 'post-thaw NOT enforced' (a disclosed limitation). Since the
    gap-elimination round the 24-hour window is cited (THAWED_PLASMA) and
    enforced (BloodUnit.thaw_expires_at); the note must say both."""
    note = cs.COMPONENT_SHELF_LIFE[C.PLASMA].note or ""
    assert "FROZEN" in note and "24 hours" in note and "enforced" in note
    assert cs.post_thaw_hours(C.PLASMA) == 24
    assert all(cs.post_thaw_hours(c) is None for c in C if c != C.PLASMA)
    assert cs.THAWED_PLASMA.source_url.startswith("https://")


def test_rule_ref_format() -> None:
    assert cs.rule_ref(C.RED_BLOOD_CELLS) == "shelf_life.RED_BLOOD_CELLS@2026.10.10-1"


def test_dead_config_setting_stays_removed() -> None:
    """The old 35-day setting was read by nothing since Phase 35; it must
    not come back as a knob that silently does nothing."""
    assert not hasattr(settings, "blood_unit_shelf_life_days")


@pytest.mark.parametrize("comp", list(C))
def test_real_unit_expiry_uses_cited_rule_and_records_it(comp: C) -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": f"H-{uuid.uuid4().hex[:6]}", "city": "Metro"})
    hid = hosp.get("/hospitals/me").json()["id"]
    r = hosp.post("/hospitals/me/inventory/add",
                  json={"blood_type": "O_NEG", "units": 1, "component": comp.value})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        unit = db.scalars(select(BloodUnit).where(
            BloodUnit.hospital_id == uuid.UUID(hid), BloodUnit.component == comp,
        )).one()
        assert unit.expires_at - unit.received_at == timedelta(days=cs.shelf_life_days(comp))
        assert unit.expiry_rule_ref == cs.rule_ref(comp)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Component compatibility provenance + exhaustive plasma table
# --------------------------------------------------------------------------- #
def test_compatibility_provenance_fields() -> None:
    assert cc.SOURCE_URL.startswith("https://")
    assert cc.SOURCE_ID and cc.SOURCE_ORGANIZATION and cc.SOURCE_DOCUMENT
    assert cc.SOURCE_VERSION is None  # chart has no version stamp; none claimed
    assert cc.RETRIEVED_ON.isoformat() == "2026-10-05"


def test_compatibility_fingerprint_is_pinned() -> None:
    assert cc.compatibility_fingerprint() == PINNED_COMPAT_FINGERPRINT


_PLASMA_EXPECTED = {
    # donor ABO -> recipients it may give plasma to (UTMB chart)
    "O": {"O"},
    "A": {"O", "A"},
    "B": {"O", "B"},
    "AB": {"O", "A", "B", "AB"},
}


@pytest.mark.parametrize("donor", list(BT))
@pytest.mark.parametrize("recipient", list(BT))
def test_plasma_table_exhaustive(donor: BT, recipient: BT) -> None:
    d, r = donor.value.split("_")[0], recipient.value.split("_")[0]
    assert cc.can_donate_plasma_to(donor, recipient) is (r in _PLASMA_EXPECTED[d])


def test_platelets_never_abo_blocked_red_cells_are() -> None:
    assert all(cc.is_component_compatible(d, r, C.PLATELETS) for d in BT for r in BT)
    assert not cc.is_component_compatible(BT.A_POS, BT.O_NEG, C.RED_BLOOD_CELLS)
    assert cc.is_component_compatible(BT.O_NEG, BT.AB_POS, C.WHOLE_BLOOD)
