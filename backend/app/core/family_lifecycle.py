"""Phase 13 -- the family-link relationship lifecycle, formalized.

`FamilyLinkStatus` (app/models/enums.py) already has five states --
PENDING, ACCEPTED, REJECTED, REVOKED, EXPIRED -- which is the *same* set the
Phase 13 spec asks for (it names the accepted state "APPROVED"; this
codebase already calls it ACCEPTED throughout every model, schema, route,
test and frontend screen, so ACCEPTED is kept rather than renamed -- see
LIFELINK_PHASE_13_REPORT.md section 5 for the full mapping/rationale).

Pre-Phase-13, `app/api/routes/family.py` enforced transitions with four
independent inline checks (one per route function: accept, reject, revoke,
plus the re-request branch in request_link), each correct on its own but,
exactly like Emergency before Phase 12, not centrally expressible or
independently testable. This module is the single source of truth for
"what's legal from here", in the same pure/DB-free style as
`app.core.emergency_lifecycle` / `app.core.request_lifecycle`.

Every entry in `TRANSITIONS` was already real, working behavior before
Phase 13 -- traced to its exact pre-existing call site:

  PENDING  -> ACCEPTED   : the target patient accepts (family.py::accept_link)
  PENDING  -> REJECTED   : the target patient declines (family.py::reject_link)
  PENDING  -> REVOKED    : either party cancels the still-unanswered request
                           (family.py::revoke_link)
  PENDING  -> EXPIRED    : lazily, past `expires_at`, on the next read or
                           action (family.py::_expire_if_stale) -- the only
                           transition with no human actor
  ACCEPTED -> REVOKED    : either party ends a live relationship
                           (family.py::revoke_link)
  REJECTED -> PENDING    : re-requesting the same (family, patient) pair
  REVOKED  -> PENDING    : re-requesting after an ended relationship
  EXPIRED  -> PENDING    : re-requesting after an ignored one

The REJECTED/REVOKED/EXPIRED -> PENDING transition is real, but reachable
*only* through `request_link` (a brand new request) -- never through
accept/reject/revoke, which all require the link to currently be PENDING
(or ACCEPTED, for revoke). `REOPENABLE_FROM` below is what `request_link`
checks before reusing a row; `TRANSITIONS` is not, by itself, an
authorization system -- exactly like `app.core.emergency_lifecycle`, this
table is state-machine truth only, and each endpoint still separately
enforces *who* may trigger *which* legal transition (only the patient may
accept/reject; either party may revoke; only the system, with no user
actor, may expire).

Nothing here is actually "terminal" in the sense Emergency's RESOLVED/
CANCELLED are: every non-PENDING state can always be re-requested into a new
PENDING cycle. What IS enforced is that this reopening only ever happens
through a fresh `request_link` call mutating the same row forward (the
existing, pre-Phase-13 design -- see report section 5 for why this row-reuse
approach, backed by a real `UniqueConstraint(family_user_id, patient_user_id)`,
is kept rather than replaced with a new row per request cycle) -- it is
never a side effect of calling accept/reject/revoke on an already-decided
link.
"""
from __future__ import annotations

from app.models.enums import FamilyLinkStatus as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.ACCEPTED, S.REJECTED, S.REVOKED, S.EXPIRED}),
    S.ACCEPTED: frozenset({S.REVOKED}),
    S.REJECTED: frozenset({S.PENDING}),
    S.REVOKED: frozenset({S.PENDING}),
    S.EXPIRED: frozenset({S.PENDING}),
}

#: States from which `request_link` may reopen the same row as a fresh
#: PENDING request. Kept as its own name (rather than just "not PENDING and
#: not ACCEPTED") so the "which old states are reopenable" business rule has
#: exactly one place it's spelled out.
REOPENABLE_FROM: frozenset[S] = frozenset({S.REJECTED, S.REVOKED, S.EXPIRED})

#: States in which a real, currently-live relationship exists -- a second
#: `request_link` for the same pair while in one of these is a genuine
#: duplicate and must be rejected outright, never silently reused.
LIVE_STATUSES: frozenset[S] = frozenset({S.PENDING, S.ACCEPTED})


class InvalidTransitionError(Exception):
    """Raised for any transition not present in `TRANSITIONS`. Routes catch
    this and translate it to HTTP 409, listing the actually-allowed next
    states rather than a bare "no"."""

    def __init__(self, current: S, target: S) -> None:
        self.current = current
        self.target = target
        self.allowed = allowed_transitions(current)
        super().__init__(
            f"Cannot transition a {current.value} family link to {target.value} "
            f"(allowed: {', '.join(s.value for s in self.allowed) or 'none'})"
        )


def allowed_transitions(current: S) -> list[S]:
    """Every state reachable directly from `current`, in a stable order.
    Pure state-machine truth -- does not encode who may trigger which."""
    return sorted(TRANSITIONS.get(current, frozenset()), key=lambda s: s.value)


def validate_transition(current: S, target: S) -> None:
    """Raise `InvalidTransitionError` unless `current -> target` is in the
    table. A same-state call is always rejected -- every real caller in this
    codebase gates on the current status before ever reaching here, so a
    same-state call always indicates a stale client or a genuine race lost
    to a concurrent request (see `app.services.family_lifecycle`), never an
    intentional idempotent replay."""
    if target not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(current, target)
