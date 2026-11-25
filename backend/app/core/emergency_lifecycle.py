"""Phase 12 -- the emergency/SOS lifecycle, formalized.

`EmergencyStatus` (app/models/enums.py) already had exactly the six states
the spec asks for -- ACTIVE, ACKNOWLEDGED, MATCHING, ASSISTANCE_FOUND,
RESOLVED, CANCELLED -- so no enum change was needed. What was missing was an
explicit, centralized transition table: pre-Phase-12,
`app/api/routes/emergencies.py` enforced transitions with five different,
independently-written inline checks (one per route function), each of which
happened to agree with the others but none of which any test or caller could
ask "what's actually legal from here" without re-deriving it by reading the
whole file.

This module is pure and DB-free, in the same style as
`app.core.request_lifecycle` (the identical exercise Phase 10 did for
`BloodRequestStatus`) -- it only knows about states and the table between
them. The DB-touching half (locking the row, writing the audit trail) is
`app.services.emergency_lifecycle`.

Every entry in `TRANSITIONS` below was already real, working behavior
before Phase 12 -- traced to its exact pre-existing call site:
  ACTIVE -> ACKNOWLEDGED     : a donor/hospital/admin acknowledges
                               (emergencies.py::acknowledge_emergency)
  ACTIVE -> MATCHING         : a responder accepts while still ACTIVE
                               (emergencies.py::respond_to_emergency)
  ACKNOWLEDGED -> MATCHING   : a responder accepts after acknowledgement
  MATCHING -> ACKNOWLEDGED   : every accepted responder withdrew -- the
                               emergency "reopens" rather than getting
                               stuck MATCHING with zero accepted responders
                               (respond_to_emergency's "reopened" event)
  ACTIVE/ACKNOWLEDGED/MATCHING -> ASSISTANCE_FOUND
                               : the creator, or an accepted hospital
                               responder, confirms help is secured
                               (emergencies.py::assistance_found)
  ACTIVE/ACKNOWLEDGED/MATCHING/ASSISTANCE_FOUND -> CANCELLED
                               : the creator/patient/linked family member
                               cancels from any still-open state
                               (emergencies.py::cancel_emergency)
  ACTIVE/ACKNOWLEDGED/MATCHING/ASSISTANCE_FOUND -> RESOLVED
                               : same managers, marking it resolved
                               (emergencies.py::resolve_emergency)
RESOLVED and CANCELLED are terminal -- nothing transitions out of either,
matching the spec's "terminal emergencies must not be silently moved back
into active states."
"""
from __future__ import annotations

from app.models.enums import EmergencyStatus as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.ACTIVE: frozenset({S.ACKNOWLEDGED, S.MATCHING, S.ASSISTANCE_FOUND, S.CANCELLED, S.RESOLVED}),
    S.ACKNOWLEDGED: frozenset({S.MATCHING, S.ASSISTANCE_FOUND, S.CANCELLED, S.RESOLVED}),
    S.MATCHING: frozenset({S.ACKNOWLEDGED, S.ASSISTANCE_FOUND, S.CANCELLED, S.RESOLVED}),
    S.ASSISTANCE_FOUND: frozenset({S.CANCELLED, S.RESOLVED}),
    S.RESOLVED: frozenset(),
    S.CANCELLED: frozenset(),
}

#: States in which the emergency is still actionable (responders can join,
#: the creator/patient/family can cancel or resolve). Same set as the
#: pre-existing `EMERGENCY_OPEN_STATUSES` in app/models/enums.py -- kept as
#: a separate name here, matching `app.core.request_lifecycle.OPEN_STATUSES`,
#: so this module is self-contained and importable without a circular
#: dependency back on the models enum module's own re-export.
OPEN_STATUSES: frozenset[S] = frozenset(
    {S.ACTIVE, S.ACKNOWLEDGED, S.MATCHING, S.ASSISTANCE_FOUND}
)

#: States with no outgoing transition -- once here, the emergency's
#: lifecycle is over.
TERMINAL_STATUSES: frozenset[S] = frozenset({S.RESOLVED, S.CANCELLED})


class InvalidTransitionError(Exception):
    """Raised for any transition not present in `TRANSITIONS`. Routes catch
    this and translate it to HTTP 409, listing the actually-allowed next
    states rather than a bare "no"."""

    def __init__(self, current: S, target: S) -> None:
        self.current = current
        self.target = target
        self.allowed = allowed_transitions(current)
        super().__init__(
            f"Cannot transition a {current.value} emergency to {target.value} "
            f"(allowed: {', '.join(s.value for s in self.allowed) or 'none -- terminal state'})"
        )


def allowed_transitions(current: S) -> list[S]:
    """Every state reachable directly from `current`, in a stable order.
    This is state-machine truth only -- it does not know which *actor* is
    allowed to trigger which of these (that's enforced per-endpoint, same
    split as app.core.request_lifecycle), so it's the same list regardless
    of who's asking."""
    return sorted(TRANSITIONS.get(current, frozenset()), key=lambda s: s.value)


def validate_transition(current: S, target: S) -> None:
    """Raise `InvalidTransitionError` unless `current -> target` is in the
    table. A same-state "transition" is always rejected too -- every real
    caller in this codebase gates on `status in OPEN_STATUSES` (or a
    narrower per-endpoint set) before ever reaching here, so a same-state
    call always indicates a stale client, not an intentional idempotent
    replay (emergency *creation* has its own, separate idempotency
    mechanism -- see `Emergency.idempotency_key` -- this is not it)."""
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(current, target)


def is_terminal(current: S) -> bool:
    return current in TERMINAL_STATUSES


def is_open(current: S) -> bool:
    return current in OPEN_STATUSES
