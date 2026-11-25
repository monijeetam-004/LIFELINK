"""Phase 35 -- component-aware compatibility, layered on top of the
existing, unchanged `app.core.blood_compatibility` (red-cell ABO/Rh
engine, untouched by this phase).

The master prompt's Section 2 is explicit: a unit must never be allocated
merely because ABO/Rh matches if the COMPONENT has additional/different
requirements -- plasma compatibility is famously the *inverse* of red-cell
compatibility (AB is the universal plasma donor, O the universal plasma
recipient -- the opposite of red cells), and platelets are commonly
transfused across ABO types in practice. Hardcoding a plausible-sounding
table from memory is exactly the "invented clinical rule" the master
prompt forbids, so every table below cites the real, named source it was
read from during this phase.

Source: University of Texas Medical Branch (UTMB) Blood Bank, "Blood
Component ABO Compatibility Chart" --
https://www.utmb.edu/bloodbank/blood-componenet-abo-compatability-chart
(fetched live 2026-10-05). This chart gives the standard reference most
US hospital transfusion services publish; it does not carry its own
version/date stamp, so none is claimed here beyond the fetch date above.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from app.core.blood_compatibility import can_donate_to as _can_donate_red_cells
from app.models.enums import BloodComponent, BloodType

# Phase E -- machine-readable provenance for the tables in this module
# (previously only in the docstring above). Same contract as
# app.core.clinical_sources: editing a table changes
# `compatibility_fingerprint()`, which a regression test pins.
SOURCE_ID = "UTMB-ABO-COMPONENT-CHART"
SOURCE_ORGANIZATION = "University of Texas Medical Branch (UTMB) Blood Bank"
SOURCE_DOCUMENT = "Blood Component ABO Compatibility Chart"
SOURCE_URL = "https://www.utmb.edu/bloodbank/blood-componenet-abo-compatability-chart"
SOURCE_VERSION: str | None = None  # the chart carries no version stamp; none claimed
RETRIEVED_ON = date(2026, 10, 5)
RULESET_VERSION = "2026.10.05-1"

# UTMB chart, "Plasma" row: recipient -> compatible donor ABO groups.
# Rh is NOT a factor for plasma compatibility (plasma carries no red-cell
# Rh antigen) -- the chart gives this table in ABO groups only, so this
# module intentionally ignores the Rh suffix of `BloodType` for plasma
# and matches on the ABO letter group alone.
_PLASMA_ABO_GROUPS = ("O", "A", "B", "AB")


def _abo_group(bt: BloodType) -> str:
    return bt.value.split("_")[0]


# donor ABO group -> set of recipient ABO groups it is plasma-compatible with
# (the UTMB "Plasma" column, inverted from its recipient-keyed table above
# into the donor-keyed shape the rest of this module's functions use, to
# mirror blood_compatibility.can_donate_to's own donor-first signature).
_PLASMA_DONOR_TO_RECIPIENTS: dict[str, frozenset[str]] = {
    "O": frozenset({"O"}),
    "A": frozenset({"O", "A"}),
    "B": frozenset({"O", "B"}),
    "AB": frozenset({"O", "A", "B", "AB"}),
}


def can_donate_plasma_to(donor: BloodType, recipient: BloodType) -> bool:
    """Whether `donor`'s PLASMA is ABO-compatible with `recipient`, per the
    UTMB chart cited in this module's docstring. Deliberately the inverse
    shape of red-cell compatibility -- AB is the universal plasma donor,
    O the universal plasma recipient."""
    return _abo_group(recipient) in _PLASMA_DONOR_TO_RECIPIENTS[_abo_group(donor)]


def is_abo_restricted(component: BloodComponent) -> bool:
    """Whether this component's allocation should be blocked on an ABO/Rh
    mismatch at all. Per the UTMB chart: platelets and cryoprecipitate are
    NOT ABO-restricted in practice ("while the same ABO as the patient is
    the first choice, any ABO type component may be used") -- this
    product has no cryoprecipitate component, but PLATELETS gets the same
    treatment here, sourced from the same chart."""
    return component in (BloodComponent.WHOLE_BLOOD, BloodComponent.RED_BLOOD_CELLS)


def is_component_compatible(
    donor: BloodType, recipient: BloodType, component: BloodComponent
) -> bool:
    """The one function `inventory_service` and the matching engine should
    call -- dispatches to the right rule set for `component` rather than
    assuming every component behaves like red cells.

    WHOLE_BLOOD / RED_BLOOD_CELLS: the existing, unchanged red-cell ABO/Rh
    engine (`app.core.blood_compatibility.can_donate_to`).
    PLASMA: the inverted ABO-only table above.
    PLATELETS: never blocks on ABO/Rh (`is_abo_restricted` is False) --
    always returns True. A same-type preference, where desired, is a
    separate ranking concern (FEFO-style sort), not a hard compatibility
    gate, and does not belong in this boolean.
    """
    if component in (BloodComponent.WHOLE_BLOOD, BloodComponent.RED_BLOOD_CELLS):
        return _can_donate_red_cells(donor, recipient)
    if component == BloodComponent.PLASMA:
        return can_donate_plasma_to(donor, recipient)
    # PLATELETS (and any future non-ABO-restricted component): unrestricted.
    return True


def prefers_same_type(component: BloodComponent) -> bool:
    """Whether same-ABO-type is merely a *preference* (not a hard gate)
    for this component -- true for PLATELETS per the UTMB chart's "same
    ABO as the patient is the first choice" language. Callers doing
    FEFO-style unit selection may use this to sort same-type units first
    among an already-compatible pool, without rejecting a mismatched one.
    """
    return component == BloodComponent.PLATELETS


def compatibility_fingerprint() -> str:
    """SHA-256 over the component-specific tables this module owns (the
    plasma table and which components are ABO-restricted) plus their
    provenance. The red-cell matrix itself lives in
    app.core.blood_compatibility and is pinned exhaustively (64 cases) by
    its own Phase 9 tests. Change detection, not a security boundary."""
    payload = {
        "version": RULESET_VERSION,
        "source": [SOURCE_ID, SOURCE_URL, SOURCE_VERSION, RETRIEVED_ON.isoformat()],
        "plasma": {k: sorted(v) for k, v in sorted(_PLASMA_DONOR_TO_RECIPIENTS.items())},
        "abo_restricted": sorted(c.value for c in BloodComponent if is_abo_restricted(c)),
        "same_type_preferred": sorted(c.value for c in BloodComponent if prefers_same_type(c)),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
