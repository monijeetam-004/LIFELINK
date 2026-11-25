"""Phase 10 -- the blood request lifecycle, formalized.

`BloodRequestStatus` (app/models/enums.py) already had exactly the five
states this phase's spec suggests -- PENDING, MATCHING, FULFILLED,
CANCELLED, EXPIRED -- so no enum change was needed. What was missing was an
explicit, centralized transition table (transitions were previously just
scattered `if status == X: status = Y` checks duplicated across
`blood_requests.py` and `hospitals.py`), a working EXPIRED path (the status
existed but nothing ever set it), and one place both routes and tests can
ask "what's actually legal from here" instead of re-deriving it.

This module is pure and DB-free, in the same style as
`app.core.blood_compatibility` / `app.core.matching_engine` /
`app.core.eligibility` -- it only knows about states and the table between
them. The DB-touching half (locking the row, writing the audit trail,
setting timestamp columns) is `app.services.blood_request_lifecycle`.
"""
from __future__ import annotations

from app.models.enums import BloodRequestStatus as S

# Explicit transition table. Every entry here was already real, working
# behavior before Phase 10 except the EXPIRED transitions (previously
# unreachable -- the status existed in the enum but nothing ever set it) --
# see LIFELINK_PHASE_10_REPORT.md sec 2 for the audit that traced each one
# to its pre-existing call site:
#   PENDING -> MATCHING    : a donor accepts (blood_requests.py::respond)
#   PENDING -> FULFILLED   : a hospital fulfills directly from inventory
#                            before any donor ever accepted (already
#                            possible and tested pre-Phase-10 -- see
#                            test_hospital_allocate_and_fulfill_request)
#   PENDING -> CANCELLED   : requester cancels before any donor responded
#   MATCHING -> FULFILLED  : the normal donor-matched completion path
#   MATCHING -> CANCELLED  : requester cancels after a donor accepted
#   MATCHING -> PENDING    : every accepted donor withdrew -- the request
#                            "reopens" rather than getting stuck MATCHING
#                            with zero accepted donors (already existing,
#                            tested behavior -- see the "reopened" event in
#                            blood_requests.py::respond_to_request)
#   PENDING -> EXPIRED     : new. `needed_by` passed with the request still
#                            open (see blood_request_lifecycle.expire_if_stale)
#   MATCHING -> EXPIRED    : new, same mechanism
#
#   Phase E additions -- PARTIALLY_FULFILLED, driven entirely by real
#   quantity, never set directly by a caller (see
#   app.api.routes.hospitals.allocate_to_request /
#   app.core.request_fulfillment):
#   PENDING/MATCHING -> PARTIALLY_FULFILLED : the first allocation that
#                            covers part, but not all, of units_needed.
#   PARTIALLY_FULFILLED -> FULFILLED        : a later allocation (or an
#                            explicit hospital/donor-fulfil call) brings
#                            fulfilled_units up to units_needed.
#   PARTIALLY_FULFILLED -> CANCELLED/EXPIRED: the requester can still
#                            cancel, or the request can still expire, with
#                            some real stock already committed to it --
#                            see Section 4/8's "cancellation after partial
#                            fulfilment" case. The already-ALLOCATED units
#                            are NOT auto-released (same disclosed-limitation
#                            philosophy as Shipment FAILED -- see
#                            LIFELINK_FULFILLMENT_LIFECYCLE.md).
#   PARTIALLY_FULFILLED does NOT transition back to PENDING/MATCHING --
#   unlike MATCHING -> PENDING's "every donor withdrew" case (which has no
#   inventory consequence), real stock has already been allocated once
#   PARTIALLY_FULFILLED is reached, so there is nothing to honestly
#   "unwind" back to an earlier, pre-commitment state.
TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING: frozenset(
        {S.MATCHING, S.PARTIALLY_FULFILLED, S.FULFILLED, S.CANCELLED, S.EXPIRED}
    ),
    S.MATCHING: frozenset(
        {S.PENDING, S.PARTIALLY_FULFILLED, S.FULFILLED, S.CANCELLED, S.EXPIRED}
    ),
    S.PARTIALLY_FULFILLED: frozenset({S.FULFILLED, S.CANCELLED, S.EXPIRED}),
    S.FULFILLED: frozenset(),
    S.CANCELLED: frozenset(),
    S.EXPIRED: frozenset(),
}

#: States in which the request is still actionable (donors can respond,
#: a hospital can allocate/fulfill, the requester can cancel).
OPEN_STATUSES: frozenset[S] = frozenset({S.PENDING, S.MATCHING, S.PARTIALLY_FULFILLED})

#: States with no outgoing transition -- once here, the request's lifecycle
#: is over.
TERMINAL_STATUSES: frozenset[S] = frozenset({S.FULFILLED, S.CANCELLED, S.EXPIRED})


class InvalidTransitionError(Exception):
    """Raised for any transition not present in `TRANSITIONS`. Routes catch
    this and translate it to HTTP 409, listing the actually-allowed next
    states rather than a bare "no"."""

    def __init__(self, current: S, target: S) -> None:
        self.current = current
        self.target = target
        self.allowed = allowed_transitions(current)
        super().__init__(
            f"Cannot transition a {current.value} request to {target.value} "
            f"(allowed: {', '.join(s.value for s in self.allowed) or 'none -- terminal state'})"
        )


def allowed_transitions(current: S) -> list[S]:
    """Every state reachable directly from `current`, in a stable order.
    This is state-machine truth only -- it does not know which *actor* is
    allowed to trigger which of these (that's enforced per-endpoint; see
    LIFELINK_PHASE_10_REPORT.md's authorization matrix), so it's the same
    list regardless of who's asking."""
    return sorted(TRANSITIONS.get(current, frozenset()), key=lambda s: s.value)


def validate_transition(current: S, target: S) -> None:
    """Raise `InvalidTransitionError` unless `current -> target` is in the
    table. A same-state "transition" (current == target) is always
    rejected too -- there is no reason to no-op through this path, and
    every real caller in this codebase gates on `status in OPEN_STATUSES`
    or similar before ever reaching here, so a same-state call always
    indicates a stale client, not an intentional idempotent replay."""
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(current, target)


def is_terminal(current: S) -> bool:
    return current in TERMINAL_STATUSES


def is_open(current: S) -> bool:
    return current in OPEN_STATUSES
