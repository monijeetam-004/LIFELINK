"""Central blood-product compatibility service.

Every part of LifeLink that has to decide "can this blood be given to that
patient?" goes through this module: pathway planning, stock reservation and
allocation, emergency fulfilment, donor alerts and donor ranking. It does
not define any clinical rule of its own. It composes the two rule tables the
project already cites:

* ``app.core.blood_compatibility`` -- the red-cell ABO/Rh matrix (used for
  WHOLE_BLOOD and RED_BLOOD_CELLS);
* ``app.core.component_compatibility`` -- the component rules from the UTMB
  "Blood Component ABO Compatibility Chart" (PLASMA is the inverse ABO
  table with Rh ignored; PLATELETS are not ABO-restricted, same type only
  preferred).

What this module adds is consistency: one function that lists the
compatible source types for a (recipient, component) pair in a fixed,
documented preference order, and one function that explains a match in
words. Nothing here treats one component as interchangeable with another:
a request for PLASMA is only ever satisfied by PLASMA units.
"""

from __future__ import annotations

from app.core import component_compatibility
from app.models.enums import BloodComponent, BloodType

# Fixed enum order -- the final, deterministic tie-breaker.
_TYPE_ORDER: tuple[BloodType, ...] = tuple(BloodType)


def abo_group(bt: BloodType) -> str:
    return bt.value.split("_")[0]


def rh_factor(bt: BloodType) -> str:
    return bt.value.split("_")[1]


def is_compatible(source: BloodType, recipient: BloodType, component: BloodComponent) -> bool:
    """Whether `component` of blood type `source` may be given to a patient
    of blood type `recipient`. Thin, explicit delegation to the cited
    component rules -- the single entry point every caller must use."""
    return component_compatibility.is_component_compatible(source, recipient, component)


def compatible_source_types(recipient: BloodType, component: BloodComponent) -> list[BloodType]:
    """All blood types whose `component` is compatible with `recipient`, in
    preference order:

    1. the identical type (the clinically preferred choice for every
       component, and the only way to conserve universal-donor stock);
    2. same ABO group, other Rh;
    3. same Rh, other ABO group;
    4. everything else that is still compatible;
    with the fixed enum order as the final tie-breaker so the list is
    deterministic. Incompatible types are never in the list.
    """
    compatible = [t for t in _TYPE_ORDER if is_compatible(t, recipient, component)]

    def key(t: BloodType) -> tuple[int, int, int, int]:
        return (
            0 if t == recipient else 1,
            0 if abo_group(t) == abo_group(recipient) else 1,
            0 if rh_factor(t) == rh_factor(recipient) else 1,
            _TYPE_ORDER.index(t),
        )

    return sorted(compatible, key=key)


def compatible_recipient_types(source: BloodType, component: BloodComponent) -> list[BloodType]:
    """Inverse view: which recipient types `source` `component` can serve."""
    return [r for r in _TYPE_ORDER if is_compatible(source, r, component)]


def explain(source: BloodType, recipient: BloodType, component: BloodComponent) -> str:
    """A one-line, human-readable reason for a compatibility decision,
    naming the rule family that produced it."""
    s, r = source.value, recipient.value
    comp = component.value
    if not is_compatible(source, recipient, component):
        return f"{s} {comp} is NOT compatible with a {r} patient"
    if source == recipient:
        return f"{s} {comp} is an identical-type match for a {r} patient"
    if component in (BloodComponent.WHOLE_BLOOD, BloodComponent.RED_BLOOD_CELLS):
        return f"{s} {comp} is red-cell compatible with a {r} patient (ABO/Rh red-cell rules)"
    if component == BloodComponent.PLASMA:
        return (
            f"{s} plasma is ABO-compatible with a {r} patient "
            "(inverse ABO plasma rules; Rh not a factor)"
        )
    return (
        f"{s} platelets may be given to a {r} patient (platelets are not "
        "ABO-restricted; same type is only preferred)"
    )


def preference_rank(source: BloodType, recipient: BloodType, component: BloodComponent) -> int:
    """Position of `source` in the preference order for this recipient and
    component, or a large number if incompatible (used for sorting)."""
    order = compatible_source_types(recipient, component)
    return order.index(source) if source in order else len(_TYPE_ORDER) + 1
