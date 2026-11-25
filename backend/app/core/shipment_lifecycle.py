"""Phase D -- the shipment lifecycle, formalized.

Pure and DB-free, in the same style as `app.core.request_lifecycle` /
`app.core.emergency_lifecycle` / `app.core.blood_unit_lifecycle`: this
module only knows about states and the transition relation between them.
The DB-touching half (locking the `Shipment` row, mutating `BloodUnit`/
`HospitalInventory` via `app.services.inventory_service`, writing
`ShipmentEvent`/`AuditLog`, sending notifications) lives in
`app.services.shipment_service`.

See `app.models.enums.ShipmentStatus`'s own docstring for the full
per-state justification (including why READY_FOR_DISPATCH and COMPLETED,
sometimes sketched for shipment-tracking systems in general, are not
implemented here -- they would have no distinct backing capability in
this product, the same "no decorative states" bar Phase 34 already set).
"""
from __future__ import annotations

from app.models.enums import SHIPMENT_TERMINAL_STATES
from app.models.enums import ShipmentStatus as S

# CREATED    -> DISPATCHED : physical departure from the origin. Units
#                            move RESERVED -> ALLOCATED at that moment
#                            (app.services.shipment_service.dispatch_shipment).
# CREATED    -> CANCELLED  : only legal before physical departure -- the
#                            origin's units are still only RESERVED (not
#                            yet terminal-ALLOCATED), so releasing them
#                            back to AVAILABLE is safe. See module
#                            docstring on ShipmentStatus for why CANCELLED
#                            is not reachable from any later state.
# DISPATCHED -> IN_TRANSIT : transport-underway confirmation.
# DISPATCHED -> FAILED     : something went wrong after physical
#                            departure, before a transit confirmation.
# IN_TRANSIT -> ARRIVED    : reached the destination's dock.
# IN_TRANSIT -> FAILED     : something went wrong en route.
# ARRIVED    -> RECEIVED   : destination formally takes custody --
#                            real inventory-ownership transfer happens
#                            here (app.services.shipment_service.receive_shipment).
# ARRIVED    -> FAILED     : something went wrong between arrival and
#                            formal receipt (e.g. a discrepancy discovered
#                            during receiving inspection that cannot be
#                            honestly resolved as a clean RECEIVED).
# RECEIVED/CANCELLED/FAILED -> (none): terminal.
TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.DISPATCHED, S.CANCELLED}),
    S.DISPATCHED: frozenset({S.IN_TRANSIT, S.FAILED}),
    S.IN_TRANSIT: frozenset({S.ARRIVED, S.FAILED}),
    S.ARRIVED: frozenset({S.RECEIVED, S.FAILED}),
    S.RECEIVED: frozenset(),
    S.CANCELLED: frozenset(),
    S.FAILED: frozenset(),
}

#: States in which a shipment's items are still considered "in an active
#: shipment" for Invariant 23 (a BloodUnit may belong to at most one active
#: shipment) -- i.e. every non-terminal state.
ACTIVE_STATES: frozenset[S] = frozenset(S) - SHIPMENT_TERMINAL_STATES

#: States in which DELAYED may be reported (an annotation, not a state --
#: see ShipmentStatus's docstring).
DELAYABLE_STATES: frozenset[S] = frozenset({S.DISPATCHED, S.IN_TRANSIT})


class InvalidShipmentTransitionError(ValueError):
    """Raised when a caller requests a shipment status transition that
    isn't in `TRANSITIONS`. Routes turn this into an HTTP 409, exactly
    like every other lifecycle's matching error in this codebase
    (`InvalidTransitionError`, `InvalidBloodUnitTransitionError`)."""

    def __init__(self, current: S, target: S) -> None:
        self.current = current
        self.target = target
        allowed = sorted(s.value for s in TRANSITIONS.get(current, frozenset()))
        super().__init__(
            f"Cannot move shipment from {current.value} to {target.value} "
            f"(allowed from {current.value}: {allowed or 'none -- terminal'})"
        )


def allowed_transitions(current: S) -> list[S]:
    return sorted(TRANSITIONS.get(current, frozenset()), key=lambda s: s.value)


def validate_transition(current: S, target: S) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidShipmentTransitionError(current, target)


def is_terminal(status: S) -> bool:
    return status in SHIPMENT_TERMINAL_STATES


def can_report_delay(status: S) -> bool:
    return status in DELAYABLE_STATES
