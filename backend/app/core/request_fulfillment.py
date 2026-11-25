"""Phase E -- the quantity-aware half of blood-request fulfilment.

Pure and DB-free, in the same style as `app.core.request_lifecycle` /
`app.core.shipment_lifecycle` / `app.core.blood_unit_lifecycle`: this module
only knows about numbers (requested / fulfilled / remaining) and which
`BloodRequestStatus` bucket a given fulfilled-unit count implies. It does
not lock rows, does not call `inventory_service`, and does not write
`BloodRequestEvent`/`AuditLog` -- that DB-touching half lives where every
other request mutation already lives, directly in
`app.api.routes.hospitals.allocate_to_request` /
`app.api.routes.blood_requests.fulfill_request`, exactly the way this
codebase already splits every other lifecycle (thin core module + inline
route orchestration), not a new pattern.

THE single source of truth for "how much of this request has been met" is
`BloodRequest.fulfilled_units` (an integer column, not a derived/cached
view and not re-computed by summing something else at read time). Every
mutation of it happens under the request's own `SELECT ... FOR UPDATE`
lock (`blood_request_lifecycle.get_for_update`), in the same transaction as
the `BloodRequestStatus` bucket recomputation this module provides --
never as two separate, potentially-inconsistent writes.
"""
from __future__ import annotations

from app.models.enums import BloodRequestStatus as S

#: The two statuses this module ever recommends moving *into* -- it never
#: proposes CANCELLED/EXPIRED/PENDING/MATCHING; those have their own
#: explicit, non-quantity-driven triggers elsewhere (requester cancels,
#: `needed_by` passes, a donor accepts/withdraws).
_FULFILLMENT_BUCKETS = (S.PARTIALLY_FULFILLED, S.FULFILLED)


class OverAllocationError(ValueError):
    """Raised when an allocation would push `fulfilled_units` above
    `units_needed`. Callers turn this into an HTTP 409 -- the same
    "second caller gets a clean error, nothing partially applies" rule
    this codebase uses for every other quantity/state guard."""

    def __init__(self, requested: int, already_fulfilled: int, attempted: int) -> None:
        self.requested = requested
        self.already_fulfilled = already_fulfilled
        self.attempted = attempted
        remaining = requested - already_fulfilled
        super().__init__(
            f"This request needs {requested} unit(s) total; {already_fulfilled} "
            f"already fulfilled, {remaining} remaining -- cannot allocate "
            f"{attempted} more."
        )


def validate_allocation(requested: int, already_fulfilled: int, units: int) -> None:
    """Raise `OverAllocationError` unless `already_fulfilled + units` stays
    within `[0, requested]`. Called BEFORE any inventory is actually
    deducted (Section 33: never trust a quantity without checking it
    first) -- so a rejected allocation never reaches `inventory_service`
    at all, exactly like `allocate_to_request`'s pre-existing blood-type
    mismatch check already short-circuits before touching inventory."""
    if units <= 0:
        raise ValueError("units must be positive")
    if already_fulfilled < 0 or already_fulfilled > requested:
        # Defensive -- would indicate the single source of truth is
        # already corrupt before this call; never silently proceed.
        raise ValueError(
            f"Invariant violated before this call: fulfilled_units="
            f"{already_fulfilled} is outside [0, {requested}]"
        )
    if already_fulfilled + units > requested:
        raise OverAllocationError(requested, already_fulfilled, units)


def remaining_units(requested: int, fulfilled: int) -> int:
    return max(requested - fulfilled, 0)


def bucket_for(requested: int, fulfilled: int) -> S:
    """Which of the two fulfilment buckets `fulfilled` units against
    `requested` implies -- FULFILLED once `remaining_units` hits zero,
    PARTIALLY_FULFILLED for any positive amount short of that. Does not
    handle the `fulfilled == 0` case (that's "not yet fulfilled at all",
    i.e. stay PENDING/MATCHING -- this module is never consulted then;
    see `target_status_after_allocation`)."""
    return S.FULFILLED if remaining_units(requested, fulfilled) == 0 else S.PARTIALLY_FULFILLED


def target_status_after_allocation(
    current: S, requested: int, fulfilled: int
) -> S | None:
    """What `BloodRequest.status` should become after `fulfilled_units` is
    updated to its new value, or None if the status bucket hasn't actually
    changed (the caller must NOT call `blood_request_lifecycle.transition`
    in that case -- a same-state call is always rejected as a stale read,
    per every other lifecycle module in this codebase; a second allocation
    that keeps a request in PARTIALLY_FULFILLED is a real event worth its
    own `BloodRequestEvent`, just not a *transition*).

    `fulfilled == 0` never calls into `bucket_for` -- a request cannot be
    pushed back out of PARTIALLY_FULFILLED by this module (there is no
    "un-allocate" here); this only ever models forward progress.
    """
    if fulfilled <= 0:
        return None
    target = bucket_for(requested, fulfilled)
    return target if target != current else None
