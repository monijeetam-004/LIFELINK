"""Phase 35 -- the ONE place a clinically-meaningful number in this codebase
is allowed to come from, and the citation that justifies it.

The Phase 34 report explicitly disclosed `blood_unit_shelf_life_days=35` as
an unsourced operational placeholder -- a number that "worked" but had no
named authority behind it. The Phase 35 master prompt's Section 10 is
explicit: do not replace one guess with another guess; research a real
source, and if a safe, generalizable number cannot be found, say so and
keep the figure configurable rather than pretending to authority this
product does not have.

Every entry below was fetched from a live, named, citable source document
during this phase (not recalled from training data) and is recorded here
with exactly the fields Section 10 asks for: source organization, source
document/title, version/date if available, URL, applicable component,
rule, and effective date (= when this file recorded it, since none of the
source documents stamp a single "effective date" for the specific figure
quoted). Nothing in `inventory_service.py` or `blood_unit.py` hardcodes a
shelf-life number directly -- every call site reads it from
`COMPONENT_SHELF_LIFE` here, so a future correction is a one-line change
in ONE place with its citation updated alongside it, not a hunt through
the codebase for a magic number.

HONESTY BOUNDARY, stated plainly: this module records what named
authorities publish. It does not make this product a clinical authority,
does not account for jurisdiction-specific regulatory variation (a real
blood bank's actual shelf-life policy is set by its own regulatory body
and may legitimately differ from the figures below), and the plasma
post-thaw figure below is deliberately left unset rather than guessed --
see PLASMA's entry.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date

from app.models.enums import BloodComponent

#: Phase E -- the version of THIS rule table as a whole. Any change to a
#: clinically meaningful field below (a number, a storage condition, a
#: source) must bump this, and update the pinned fingerprint in
#: tests/test_phase_e_clinical_sources.py in the same change -- that test
#: exists precisely so a figure cannot be edited without the edit being a
#: deliberate, reviewed, re-cited act. Format: "<retrieval date>-<n>".
RULESET_VERSION = "2026.10.10-1"

#: When the sources below were actually fetched (Phase 35). Not an
#: "effective date" claimed by any source document -- none of them stamp
#: one for the specific figures quoted.
_RETRIEVED_ON = date(2026, 10, 5)


@dataclass(frozen=True)
class ShelfLifeSource:
    component: BloodComponent
    shelf_life_days: int | None
    storage_condition: str
    source_organization: str
    source_document: str
    source_url: str
    rule: str
    note: str | None = None
    # Phase E -- machine-readable provenance (previously only implied in
    # prose): a stable short id for the source, the document's own
    # version/edition string when it publishes one (None when it does
    # not -- never invented), and when this module fetched it.
    source_id: str = ""
    source_version: str | None = None
    retrieved_on: date = _RETRIEVED_ON
    # Gap elimination: the storage condition above as numbers, used as the
    # acceptable cold-chain range for a shipment carrying this component.
    # None = no bound on that side. Taken from the SAME cited storage
    # condition -- not a separate transport standard (which may allow a
    # wider range; this product does not model one).
    storage_min_c: float | None = None
    storage_max_c: float | None = None


# Fetched live during Phase 35 (2026-10-05) from each organization's own
# published document -- see each entry's `source_url`. Where a document
# gave a range depending on anticoagulant/additive ("21-35 days" for whole
# blood depending on CPD vs. CPDA-1; "21 days plain vs. 42 days with
# additive solution" for red cells), this module records the figure for
# the formulation this product's own ADD workflow assumes by default
# (CPDA-1 whole blood; AS-1/AS-3 additive-solution red cells -- the
# predominant modern storage method per both source documents), and says
# so explicitly rather than silently picking a number from the range.
COMPONENT_SHELF_LIFE: dict[BloodComponent, ShelfLifeSource] = {
    BloodComponent.WHOLE_BLOOD: ShelfLifeSource(
        component=BloodComponent.WHOLE_BLOOD,
        shelf_life_days=35,
        storage_condition="1-6 C",
        storage_min_c=1.0,
        storage_max_c=6.0,
        source_organization="AABB (Association for the Advancement of Blood & Biotherapies)",
        source_document="Circular of Information for the Use of Human Blood and Blood Components",
        source_url=(
            "https://www.aabb.org/docs/default-source/default-document-library/"
            "resources/circular-of-information-watermark.pdf"
        ),
        source_id="AABB-COI",
        # The watermark PDF fetched carries no edition stamp we could
        # verify, so none is claimed.
        source_version=None,
        rule=(
            "Whole Blood is stored at 1-6 C; shelf life is determined by the "
            "anticoagulant/preservative solution used, ranging 21-35 days. "
            "This entry uses the 35-day CPDA-1 figure (the upper end of the "
            "cited range), matching this product's pre-existing Phase 34 "
            "placeholder value -- now a cited figure instead of a guess."
        ),
    ),
    BloodComponent.RED_BLOOD_CELLS: ShelfLifeSource(
        component=BloodComponent.RED_BLOOD_CELLS,
        shelf_life_days=42,
        storage_condition="1-6 C",
        storage_min_c=1.0,
        storage_max_c=6.0,
        source_organization="AABB; corroborated by the American Red Cross",
        source_document=(
            "AABB Circular of Information; American Red Cross Compendium of "
            "Transfusion Practice (v.04, Jan 2021)"
        ),
        source_url=(
            "https://www.redcross.org/content/dam/redcrossblood/hospital-page-documents/"
            "334401_compendium_v04jan2021_bookmarkedworking_rwv01.pdf"
        ),
        source_id="AABB-COI+ARC-COMPENDIUM",
        source_version="ARC Compendium v.04 (Jan 2021)",
        rule=(
            "Red Blood Cells stored at 1-6 C with an FDA-approved additive "
            "solution (AS-1/AS-3) have a 42-day shelf life (21 days without "
            "additive solution, using CPD/CPDA-1 alone) -- this entry uses "
            "the additive-solution figure, the predominant modern storage "
            "method both sources describe."
        ),
    ),
    BloodComponent.PLATELETS: ShelfLifeSource(
        component=BloodComponent.PLATELETS,
        shelf_life_days=5,
        storage_condition="room temperature, 20-24 C, with agitation",
        storage_min_c=20.0,
        storage_max_c=24.0,
        source_organization="American Red Cross",
        source_document="Compendium of Transfusion Practice (v.04, Jan 2021)",
        source_url=(
            "https://www.redcross.org/content/dam/redcrossblood/hospital-page-documents/"
            "334401_compendium_v04jan2021_bookmarkedworking_rwv01.pdf"
        ),
        source_id="ARC-COMPENDIUM",
        source_version="v.04 (Jan 2021)",
        rule=(
            "Apheresis platelets (leukocyte-reduced, in platelet additive "
            "solution) and pathogen-reduced apheresis platelets both have a "
            "5-day post-collection shelf life; pooled whole-blood-derived "
            "platelets also carry a 5-day shelf life per the same document."
        ),
    ),
    BloodComponent.PLASMA: ShelfLifeSource(
        component=BloodComponent.PLASMA,
        shelf_life_days=365,
        storage_condition="-25 C or below (frozen)",
        storage_min_c=None,
        storage_max_c=-25.0,
        source_organization="Australian Red Cross Lifeblood",
        source_document="Fresh Frozen Plasma product information",
        source_url="https://www.lifeblood.com.au/health-professionals/products/blood-components/fresh-frozen-plasma",
        source_id="LIFEBLOOD-FFP",
        source_version=None,
        rule="FFP has a shelf life of 12 months (365 days) when stored frozen at -25 C or below.",
        note=(
            "This 365-day figure covers FROZEN storage only. Once a unit is "
            "thawed, the separate post-thaw window in THAWED_PLASMA (24 hours "
            "at 1-6 C) applies "
            "and the unit's usable life ends at whichever comes first "
            "(enforced since the gap-elimination round: BloodUnit.thawed_at / "
            "thaw_expires_at, app.services.inventory_service.thaw_plasma_unit)."
        ),
    ),
}


@dataclass(frozen=True)
class PostThawRule:
    hours: int
    storage_condition: str
    source_organization: str
    source_document: str
    source_url: str
    source_id: str
    rule: str
    retrieved_on: date = _RETRIEVED_ON


#: Post-thaw window for PLASMA. Same AABB Circular of Information this module
#: already cites for whole blood (and which the previous PLASMA note quoted
#: verbatim as the rule this product did not yet enforce).
THAWED_PLASMA = PostThawRule(
    hours=24,
    storage_condition="1-6 C after thawing",
    source_organization="AABB (Association for the Advancement of Blood & Biotherapies)",
    source_document="Circular of Information for the Use of Human Blood and Blood Components",
    source_url=(
        "https://www.aabb.org/docs/default-source/default-document-library/"
        "resources/circular-of-information-watermark.pdf"
    ),
    source_id="AABB-COI",
    rule=(
        "Thawed FFP must be used immediately or, if held at 1-6 C, discarded "
        "after 24 hours."
    ),
)


def post_thaw_hours(component: BloodComponent) -> int | None:
    """Hours a unit stays usable after thawing, or None if the component is
    not stored frozen (only PLASMA is, in this product)."""
    return THAWED_PLASMA.hours if component == BloodComponent.PLASMA else None


def thaw_rule_ref() -> str:
    return f"post_thaw.PLASMA@{RULESET_VERSION}"


def temperature_range_c(component: BloodComponent) -> tuple[float | None, float | None]:
    """(min, max) acceptable temperature in Celsius for `component`."""
    entry = COMPONENT_SHELF_LIFE[component]
    return entry.storage_min_c, entry.storage_max_c


def shelf_life_days(component: BloodComponent) -> int | None:
    """The cited shelf-life figure for `component`, or None if this module
    deliberately does not assert one (never a silent fabrication)."""
    return COMPONENT_SHELF_LIFE[component].shelf_life_days


def rule_ref(component: BloodComponent) -> str | None:
    """Stable reference to the exact rule that sets `component`'s expiry
    clock -- recorded on each BloodUnit at creation (`expiry_rule_ref`) so
    the unit keeps naming the rule it was created under even after this
    table changes. None when no figure is asserted (no clock is started)."""
    if COMPONENT_SHELF_LIFE[component].shelf_life_days is None:
        return None
    return f"shelf_life.{component.value}@{RULESET_VERSION}"


def _canonical(entry: ShelfLifeSource) -> dict:
    d = asdict(entry)
    d["component"] = entry.component.value
    d["retrieved_on"] = entry.retrieved_on.isoformat()
    return d


def ruleset_fingerprint() -> str:
    """SHA-256 over a canonical (sorted-key JSON) serialization of every
    entry in `COMPONENT_SHELF_LIFE`. Pinned by a regression test: editing
    ANY field -- a number, a URL, a note -- changes this, so an unsourced
    or unreviewed edit cannot land silently. This is a change-detection
    check, not a security boundary (anyone editing the code can update
    the pin too; the point is that doing so is visible in review)."""
    payload = [
        _canonical(COMPONENT_SHELF_LIFE[c])
        for c in sorted(COMPONENT_SHELF_LIFE, key=lambda c: c.value)
    ]
    thaw = asdict(THAWED_PLASMA)
    thaw["retrieved_on"] = THAWED_PLASMA.retrieved_on.isoformat()
    blob = json.dumps(
        {"version": RULESET_VERSION, "entries": payload, "post_thaw": thaw},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
