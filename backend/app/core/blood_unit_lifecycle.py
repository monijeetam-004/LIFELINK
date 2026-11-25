"""Phase 34 -- the blood-unit lifecycle, formalized.

Pure and DB-free, in the same style as `app.core.request_lifecycle` /
`app.core.emergency_lifecycle` / `app.core.blood_compatibility`: this module
only knows about states and the transition relation between them. The
DB-touching half (locking rows, mutating `HospitalInventory` and `BloodUnit`
together, writing the audit trail) lives in `app.services.inventory_service`.

Why only four states -- AVAILABLE / RESERVED / ALLOCATED / EXPIRED -- and not
the illustrative COLLECTED -> TESTING -> CLEARED -> ... -> TRANSFUSED pipeline
some blood-bank-lifecycle writeups sketch: every state and transition below
maps to a real, already-supported capability of this product. States with no
supporting workflow anywhere in the existing codebase are deliberately NOT
implemented (see docs/LIFELINK_PHASE_34_BLOOD_UNIT_LIFECYCLE_REPORT.md
section 4 for the full per-state justification):

  * COLLECTED/TESTING/CLEARED/FAILED -- no lab-testing workflow exists
    anywhere in this application. A unit becomes AVAILABLE the moment a
    hospital runs the existing ADD action; there is no intermediate
    "awaiting clearance" state for software to track honestly.
  * DISPATCHED/RECEIVED/TRANSFUSED -- ALLOCATE already means "physically
    dispatched" per `inventory_service.allocate_stock`'s own docstring, and
    there is no shipment-level receiving confirmation or transfusion
    record anywhere in the product. Claiming these states would mean
    inventing a workflow state no UI, route, or data model backs.
  * CANCELLED/LOST/DAMAGED -- there is no un-allocate capability in the
    existing product (ALLOCATED has always had zero outgoing transitions),
    so these have no real entry point to implement honestly this phase.
  * RELEASED -- modeled as a RESERVED -> AVAILABLE transition, not a
    separate state. The existing `HospitalInventoryAction.RELEASE` label
    already carries this distinction in the audit log; adding a dedicated
    BloodUnitState for it would just duplicate that without adding
    information.

TRANSITIONS below is the actual, implemented relation -- not the above
illustrative sketch.
"""
from __future__ import annotations

from app.models.enums import BLOOD_UNIT_TERMINAL_STATES
from app.models.enums import BloodUnitState as S

# AVAILABLE -> RESERVED   : the RESERVE action earmarks on-hand stock.
# AVAILABLE -> ALLOCATED  : a direct allocation with no prior reservation --
#                           already real, existing behavior
#                           (`allocate_stock` works "whether or not a
#                           RESERVE happened first", per its own docstring).
#                           See report section 11 for why this phase keeps
#                           that existing path rather than forcing
#                           AVAILABLE -> RESERVED -> ALLOCATED.
# AVAILABLE -> EXPIRED    : natural shelf-life expiry, or a manual EXPIRE
#                           on currently-unreserved stock.
# RESERVED  -> AVAILABLE  : the RELEASE action gives up a reservation
#                           without dispatching.
# RESERVED  -> ALLOCATED  : the normal allocate-after-reserve path.
# RESERVED  -> EXPIRED    : a reserved unit can still reach the end of its
#                           shelf life before being allocated -- a real,
#                           physically possible event this lazy sweep must
#                           not silently ignore (see report section 6).
#                           NOT reachable via the manual EXPIRE action
#                           (which only ever targets unreserved stock, by
#                           existing, preserved design) -- only via the
#                           lazy date-based sweep.
# ALLOCATED -> (none)     : terminal -- see module docstring.
# EXPIRED   -> (none)     : terminal -- see module docstring.
# Gap elimination additions:
# AVAILABLE/RESERVED -> QUARANTINED : held out of use pending review (e.g. a
#                           cold-chain excursion); a RESERVED unit loses its
#                           reservation context when quarantined.
# AVAILABLE/RESERVED -> DISCARDED   : destroyed as unusable (damage, a
#                           thawed-plasma unit discarded by staff).
# QUARANTINED -> AVAILABLE : review passed, unit released back to stock.
# QUARANTINED -> EXPIRED   : shelf life (or post-thaw window) ran out while held.
# QUARANTINED -> DISCARDED : review failed.
# DISCARDED   -> (none)    : terminal.
TRANSITIONS: dict[S, frozenset[S]] = {
    S.AVAILABLE: frozenset({S.RESERVED, S.ALLOCATED, S.EXPIRED, S.QUARANTINED, S.DISCARDED}),
    S.RESERVED: frozenset({S.AVAILABLE, S.ALLOCATED, S.EXPIRED, S.QUARANTINED, S.DISCARDED}),
    S.QUARANTINED: frozenset({S.AVAILABLE, S.EXPIRED, S.DISCARDED}),
    S.ALLOCATED: frozenset(),
    S.EXPIRED: frozenset(),
    S.DISCARDED: frozenset(),
}

#: States in which a unit is on hand AND counted in the HospitalInventory
#: aggregate (usable stock). A QUARANTINED unit is physically present but
#: deliberately not counted -- it must not be offered or reserved.
ON_HAND_STATES: frozenset[S] = frozenset({S.AVAILABLE, S.RESERVED})

#: States with no outgoing transition -- once here, a unit's lifecycle is
#: over and it can never re-enter the matching pool (Invariant I7).
TERMINAL_STATES: frozenset[S] = BLOOD_UNIT_TERMINAL_STATES


class InvalidBloodUnitTransitionError(Exception):
    """Raised for any transition not present in `TRANSITIONS`. Services
    catch this and translate it to HTTP 409 (via `InventoryActionError`),
    never partially mutating state and never writing a success audit
    event for a transition that didn't happen."""

    def __init__(self, current: S, target: S) -> None:
        self.current = current
        self.target = target
        self.allowed = allowed_transitions(current)
        super().__init__(
            f"Cannot transition a blood unit from {current.value} to {target.value} "
            f"(allowed: {', '.join(s.value for s in self.allowed) or 'none -- terminal state'})"
        )


def allowed_transitions(current: S) -> list[S]:
    """Every state reachable directly from `current`, in a stable order.
    State-machine truth only -- it does not know which *actor* may trigger
    which of these (enforced per-route; see the Phase 34 report's
    authorization section), so the list is the same regardless of who's
    asking."""
    return sorted(TRANSITIONS.get(current, frozenset()), key=lambda s: s.value)


def validate_transition(current: S, target: S) -> None:
    """Raise `InvalidBloodUnitTransitionError` unless `current -> target`
    is in the table. A same-state "transition" is always rejected -- every
    real caller in this codebase gates on the unit's actual current state
    before ever reaching here, so a same-state call indicates a stale
    read, not an intentional idempotent replay."""
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidBloodUnitTransitionError(current, target)


def is_terminal(current: S) -> bool:
    return current in TERMINAL_STATES


def is_on_hand(current: S) -> bool:
    return current in ON_HAND_STATES
