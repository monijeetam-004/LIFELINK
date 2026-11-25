"""Shared domain enums and blood-group compatibility (real medical data)."""
from __future__ import annotations

import enum


class BloodType(str, enum.Enum):
    A_POS = "A_POS"
    A_NEG = "A_NEG"
    B_POS = "B_POS"
    B_NEG = "B_NEG"
    AB_POS = "AB_POS"
    AB_NEG = "AB_NEG"
    O_POS = "O_POS"
    O_NEG = "O_NEG"


class Urgency(str, enum.Enum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


# Ordering for "alert me at this urgency or above" filtering -- Urgency is a
# plain str enum (its member order isn't a guaranteed comparison), so this is
# the one place rank is defined and everyone else imports it.
_URGENCY_RANK: dict[Urgency, int] = {
    Urgency.ROUTINE: 0,
    Urgency.URGENT: 1,
    Urgency.CRITICAL: 2,
}


def urgency_at_least(value: Urgency, minimum: Urgency) -> bool:
    return _URGENCY_RANK[value] >= _URGENCY_RANK[minimum]


class BloodRequestStatus(str, enum.Enum):
    """Phase E adds PARTIALLY_FULFILLED -- see app.core.request_lifecycle
    for the full transition table and app.models.blood_request's
    `fulfilled_units` for the quantity model this status now actually
    reflects (never a cosmetic label -- see LIFELINK_FULFILLMENT_LIFECYCLE.md).

    REJECTED was explicitly considered (per the Phase E master prompt's
    Section 2) and deliberately NOT added: nothing in this codebase has
    ever had an "admin/hospital rejects a request outright" concept
    distinct from CANCELLED (requester-initiated) or a hospital's own
    per-request DECLINED (HospitalResponseStatus.DECLINED, which already
    exists at the per-hospital level and does not close the request
    itself -- other hospitals may still respond). Adding a redundant
    REJECTED state on BloodRequest itself with no real, distinct trigger
    would be exactly the decorative-state anti-pattern this codebase has
    avoided since Phase 34.
    """

    PENDING = "PENDING"
    MATCHING = "MATCHING"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class DonorResponseStatus(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    # A donor can't help right now but isn't refusing this request outright --
    # distinct from DECLINED so the requester/analytics can tell "no" from
    # "not right now" apart, per the donor Match Response spec.
    NOT_NOW = "NOT_NOW"


class ShortlistSubject(str, enum.Enum):
    """What a confidence shortlist is for (app/services/donor_shortlist_service.py)."""

    BLOOD_REQUEST = "BLOOD_REQUEST"
    EMERGENCY = "EMERGENCY"


class DonorContactStatus(str, enum.Enum):
    """Lifecycle of one shortlisted donor's contact for one request/emergency.

    A ranked donor is only a candidate (not persisted). Once contacted:
    QUEUED -> AWAITING_RESPONSE -> ACCEPTED -> CONFIRMED_FOR_COORDINATION,
    or DECLINED / UNAVAILABLE (answered "not now") / TIMED_OUT /
    NOTIFICATION_FAILED / ELIGIBILITY_RECHECK_REQUIRED / WITHDRAWN.
    Sent does not mean delivered, delivered does not mean read, read does
    not mean accepted, and CONFIRMED_FOR_COORDINATION still needs the blood
    centre's own screening -- it is never a guarantee of donation.
    """

    QUEUED = "QUEUED"
    AWAITING_RESPONSE = "AWAITING_RESPONSE"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMED_OUT = "TIMED_OUT"
    ELIGIBILITY_RECHECK_REQUIRED = "ELIGIBILITY_RECHECK_REQUIRED"
    CONFIRMED_FOR_COORDINATION = "CONFIRMED_FOR_COORDINATION"
    WITHDRAWN = "WITHDRAWN"


class DonorAvailabilityState(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    # Temporarily can't donate/respond (e.g. traveling, mild illness) without
    # committing to the longer-term UNAVAILABLE state. Functionally excludes
    # the donor from matching/notifications exactly like UNAVAILABLE does --
    # the distinction is informational (shown back to the donor and anyone
    # who can see their profile), not a different matching rule.
    NOT_NOW = "NOT_NOW"


class EmergencyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    MATCHING = "MATCHING"
    ASSISTANCE_FOUND = "ASSISTANCE_FOUND"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


# Emergency states that are still open (responders may act, creator may close).
EMERGENCY_OPEN_STATUSES: frozenset[EmergencyStatus] = frozenset(
    {
        EmergencyStatus.ACTIVE,
        EmergencyStatus.ACKNOWLEDGED,
        EmergencyStatus.MATCHING,
        EmergencyStatus.ASSISTANCE_FOUND,
    }
)


class FamilyLinkStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class HospitalResponseStatus(str, enum.Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PREPARING = "PREPARING"
    DISPATCHED = "DISPATCHED"
    DECLINED = "DECLINED"
    # ALLOCATED/FULFILLED are set only by the dedicated
    # POST /hospitals/me/requests/{id}/allocate and .../fulfill endpoints
    # (app/api/routes/hospitals.py), never accepted from the generic
    # /respond endpoint -- both have real side effects (inventory
    # deduction, and for FULFILLED, transitioning the shared
    # BloodRequestStatus itself) that a free-form status write must not
    # trigger implicitly.
    ALLOCATED = "ALLOCATED"
    FULFILLED = "FULFILLED"


class HospitalInventoryAction(str, enum.Enum):
    """One audited inventory mutation kind (app/services/inventory_service.py).

    ADD: new stock received. RESERVE: units earmarked for a specific
    request without physically leaving inventory yet. ALLOCATE: units
    actually dispatched -- physically removed, and releases any matching
    reservation. RELEASE: a reservation given up without dispatching
    (request cancelled/reassigned). EXPIRE: units removed as unusable.
    RECONCILE: an absolute stock-take correction (what the old bulk
    PUT /hospitals/me/inventory already did) rather than a relative delta.
    """

    ADD = "ADD"
    RESERVE = "RESERVE"
    ALLOCATE = "ALLOCATE"
    RELEASE = "RELEASE"
    EXPIRE = "EXPIRE"
    RECONCILE = "RECONCILE"
    # Gap elimination: per-unit lifecycle actions (one unit each).
    THAW = "THAW"                          # frozen plasma thawed; post-thaw clock starts
    QUARANTINE = "QUARANTINE"              # unit held back from use pending review
    RELEASE_QUARANTINE = "RELEASE_QUARANTINE"  # quarantined unit cleared, back in stock
    DISCARD = "DISCARD"                    # unit destroyed / written off
    # Audit C21 (2026-10-31): a reservation moved from an emergency pathway to
    # the shipment that carries it; units stay RESERVED (no release window).
    TRANSFER = "TRANSFER"


class VerificationKind(str, enum.Enum):
    DONOR_IDENTITY = "DONOR_IDENTITY"
    HOSPITAL_FACILITY = "HOSPITAL_FACILITY"


class VerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class NotificationDigestFrequency(str, enum.Enum):
    REALTIME = "REALTIME"
    DAILY = "DAILY"
    OFF = "OFF"


class SmsStatus(str, enum.Enum):
    """Lifecycle of one SmsNotification audit row.

    PENDING covers both "not attempted yet" and "attempted, retryable
    failure, waiting to retry" -- retry_count/next attempt scheduling on the
    row itself (app/models/sms_notification.py) is what distinguishes those,
    not a separate status. SENT and FAILED are both terminal.
    """

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    # Gap elimination: the development CONSOLE provider wrote the message to
    # the log. Nothing was sent to any phone -- never reported as SENT.
    LOGGED_ONLY = "LOGGED_ONLY"


class SmsFailureCategory(str, enum.Enum):
    """Why an SMS send attempt failed, per the provider-failure-handling
    requirements in LIFELINK_SMS_PROVIDER.md. RETRYABLE categories are
    re-queued (up to settings.sms_max_retries); everything else is
    permanent and is not retried."""

    TIMEOUT = "TIMEOUT"
    PROVIDER_5XX = "PROVIDER_5XX"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_4XX = "PROVIDER_4XX"
    AUTH_FAILURE = "AUTH_FAILURE"
    INVALID_RECIPIENT = "INVALID_RECIPIENT"
    INVALID_TEMPLATE = "INVALID_TEMPLATE"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    NO_PHONE_ON_FILE = "NO_PHONE_ON_FILE"
    UNKNOWN = "UNKNOWN"


# Categories worth retrying -- transient conditions that may clear up on
# their own. Everything else (bad recipient, bad template, auth/config
# problems, no phone on file) is permanent: retrying can't fix it.
SMS_RETRYABLE_CATEGORIES: frozenset[SmsFailureCategory] = frozenset(
    {SmsFailureCategory.TIMEOUT, SmsFailureCategory.PROVIDER_5XX, SmsFailureCategory.RATE_LIMITED}
)


class SmsTemplateKey(str, enum.Enum):
    """Internal SMS template keys. Each maps (app/services/sms/templates.py)
    to a DLT-approved MSG91 Flow ID and a fixed, pre-approved message
    structure -- LifeLink never sends arbitrary free-text SMS content (see
    the "no arbitrary user-generated SMS text" requirement).

    Deliberately NOT a 1:1 copy of every example category in the original
    spec: only categories with a real, already-existing backing event in
    this codebase are implemented. See LIFELINK_SMS_PROVIDER.md for which
    example categories (escalation timers, SMS OTP) have no such event and
    were left out rather than wired to something fabricated.
    """

    EMERGENCY_CREATED = "EMERGENCY_CREATED"
    EMERGENCY_STATUS_CHANGED = "EMERGENCY_STATUS_CHANGED"
    DONOR_MATCH_ALERT = "DONOR_MATCH_ALERT"
    HOSPITAL_REQUEST_UPDATE = "HOSPITAL_REQUEST_UPDATE"
    FAMILY_FILED_REQUEST = "FAMILY_FILED_REQUEST"
    BLOOD_REQUEST_FULFILLED = "BLOOD_REQUEST_FULFILLED"
    BLOOD_REQUEST_CANCELLED = "BLOOD_REQUEST_CANCELLED"


class AppointmentStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class FacilityType(str, enum.Enum):
    """Kind of facility a HospitalProfile row represents.

    Existing rows predate this column (server_default keeps them valid);
    it distinguishes the two facility kinds the graph engine models as
    distinct node roles (a blood bank does not admit emergency patients).
    """

    HOSPITAL = "HOSPITAL"
    BLOOD_BANK = "BLOOD_BANK"


class TransportRouteStatus(str, enum.Enum):
    OPERATIONAL = "OPERATIONAL"
    DISRUPTED = "DISRUPTED"


class ShipmentStatus(str, enum.Enum):
    """Lifecycle of one `Shipment` row (app/models/shipment.py) -- a real,
    unit-level-traceable movement of already-ALLOCATED blood stock from one
    facility to another. Phase D.

    Deliberately NOT the full CREATED/READY_FOR_DISPATCH/.../COMPLETED
    sketch a shipment-tracking system could have: two states from that
    sketch are collapsed here because, in THIS implementation, they would
    have had no distinct backing capability (the same "no decorative
    states" discipline Phase 34 already applied to BloodUnitState):

      * READY_FOR_DISPATCH is not separate from CREATED -- this system
        allocates (reserves) a shipment's units atomically at creation
        time (`shipment_service.create_shipment`), so nothing distinguishes
        "created" from "ready" the way it would in a system where items
        are added incrementally after an empty draft is opened.
      * COMPLETED is not separate from RECEIVED -- RECEIVED is already the
        moment the real inventory-ownership transfer happens
        (`shipment_service.receive_shipment` creates the destination-side
        BloodUnit rows); nothing further actually happens afterward in
        this product, so a trailing COMPLETED would be a status with no
        real transition into it.

    States:
      CREATED     -- shipment exists; its units are RESERVED (not yet
                     ALLOCATED) at the origin hospital, earmarked for this
                     shipment specifically (`BloodUnit.reserved_for_shipment_id`).
      DISPATCHED  -- physically left the origin. Units are now ALLOCATED
                     at the origin (terminal there, per BloodUnitState --
                     this system never un-allocates).
      IN_TRANSIT  -- transport underway confirmed (a separate, cheap-to-
                     support confirmation step; may follow DISPATCHED
                     immediately in practice for a small deployment with
                     no live fleet tracking -- see LIFELINK_SHIPMENT_LIFECYCLE.md).
      ARRIVED     -- physically at the destination's dock, not yet
                     formally received/counted into destination inventory.
      RECEIVED    -- terminal, happy path. Destination has taken real
                     custody; destination-side BloodUnit rows now exist.
      CANCELLED   -- terminal. Only legal from CREATED (before physical
                     dispatch) -- cancelling releases the RESERVED units
                     back to AVAILABLE at the origin, which is only ever
                     safe before ALLOCATED (terminal) is reached.
      FAILED      -- terminal. Legal from DISPATCHED/IN_TRANSIT/ARRIVED --
                     something went wrong after physical departure. Does
                     NOT release/return the origin's ALLOCATED units (see
                     ShipmentStatus transition table module's docstring --
                     Section 14's "never silently return blood to
                     AVAILABLE"); this is a disclosed, real limitation, not
                     an oversight.

    DELAYED is deliberately not a state here -- it is an orthogonal
    annotation (`Shipment.delayed_at`/`delay_reason`, plus a
    SHIPMENT_DELAYED `ShipmentEvent` each time it's reported) settable
    while status is DISPATCHED or IN_TRANSIT, mirroring how TransportRoute
    disruption is *separate* from a shipment's own lifecycle (Section 16).
    A delayed shipment is still, truthfully, "DISPATCHED" or "IN_TRANSIT" --
    it just hasn't arrived on the schedule anyone hoped for.
    """

    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


#: Terminal shipment states -- once here, nothing further ever changes it.
SHIPMENT_TERMINAL_STATES: frozenset[ShipmentStatus] = frozenset(
    {ShipmentStatus.RECEIVED, ShipmentStatus.CANCELLED, ShipmentStatus.FAILED}
)


class ShipmentReconciliationStatus(str, enum.Enum):
    """Phase E -- the explicit, authorized, auditable path Phase D's own
    report disclosed was missing: what actually happens to a FAILED
    shipment's now-ALLOCATED-but-stranded origin units. This is a
    SEPARATE axis from `ShipmentStatus` (set only while
    `Shipment.status == FAILED`, never a `ShipmentStatus` value itself)
    specifically so FAILED's own terminal transition table does not need
    to be reopened -- see `LIFELINK_SHIPMENT_LIFECYCLE.md` and
    `docs/LIFELINK_PHASE_E_DOMAIN_INTEGRITY_REPORT.md` section 8.

    REQUIRED       -- default the moment a shipment reaches FAILED; no
                      reconciliation decision has been made yet.
    RETURNED_TO_ORIGIN -- a human confirmed the units physically never
                      left usable custody (or were brought back) and the
                      origin hospital re-added them via the existing,
                      ordinary ADD inventory action with a note
                      referencing this reconciliation -- never automatic.
    REDIRECTED     -- the units were confirmed to have been physically
                      delivered somewhere else entirely (e.g. a manual
                      diversion to a different facility during an
                      in-transit failure); recorded for the record, but
                      this system does not itself move them to a new
                      Shipment (it has no way to verify where they
                      actually ended up).
    QUARANTINED    -- the units are confirmed lost, destroyed, or
                      otherwise permanently unusable; they remain
                      ALLOCATED forever (truthful -- they were, in fact,
                      allocated and dispatched) and are never returned to
                      any hospital's available stock.
    """

    REQUIRED = "REQUIRED"
    RETURNED_TO_ORIGIN = "RETURNED_TO_ORIGIN"
    REDIRECTED = "REDIRECTED"
    QUARANTINED = "QUARANTINED"


class DonationVerificationStatus(str, enum.Enum):
    """Phase E -- whether a self-reported `Donation` row's facts have been
    confirmed by anyone with real authority over them, versus merely
    stated by the donor. See `docs/LIFELINK_VERIFICATION_LIFECYCLE.md`
    section on donations for the full policy, including why this system
    has no `CLINICALLY_VERIFIED` tier: nothing in this product's actual
    architecture gives any actor clinical/laboratory authority (no lab
    results, no clinical staff role distinct from HOSPITAL) -- claiming a
    'clinically verified' tier would assert an authority this system does
    not have, so verification here means organizational/administrative
    confirmation ("this facility confirms this donation happened"), never
    a clinical judgment.

    DONOR_REPORTED -- the default, at logging time. The donor's own
                      unconfirmed statement. This already matches 100% of
                      this system's pre-Phase-E behavior -- no historical
                      donation's meaning changes.
    VERIFIED       -- a hospital tied to this donation (`Donation.hospital_id`)
                      or an admin has confirmed it actually happened.
                      Required before a `Certificate` can be issued for
                      this donation (see app.api.routes.certificates) --
                      the one place in this codebase where a self-reported
                      fact was being turned into an artifact (a PDF with a
                      public verification URL) that a reader would
                      reasonably treat as officially confirmed.
    REJECTED       -- a hospital/admin disputes the donation happened as
                      described. Not terminal -- see TRANSITIONS; an
                      honest correction in either direction (a mistaken
                      REJECTED, or a mistaken VERIFIED) must remain
                      possible, each one a fresh, audited, actor-attributed
                      decision, never a silent overwrite.
    """

    DONOR_REPORTED = "DONOR_REPORTED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class EmergencyPathwayStatus(str, enum.Enum):
    """Lifecycle of one `EmergencyPathway` row (app/models/emergency_pathway.py).

    ACTIVE is the emergency's current, in-force resource plan -- at most one
    per emergency at a time. SUPERSEDED means a later version replaced it
    (e.g. after a transport disruption forced re-optimization); superseded
    rows are never deleted or overwritten, only relabelled, so the full
    v1 -> v2 -> v3 history stays queryable.
    """

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    # Gap elimination: a stored, still-valid alternative to the ACTIVE
    # pathway of the same plan round, ready to be promoted without a new
    # search (`backup_rank` orders them).
    BACKUP = "BACKUP"
    # A pathway (ACTIVE or BACKUP) that can no longer be used -- one of its
    # routes failed, its source facility lost verification, or its stock is
    # gone. Kept for audit, never reused.
    INVALIDATED = "INVALIDATED"


class PathwayFeasibility(str, enum.Enum):
    """How well a planned pathway meets the request (pathway_planner).

    VIABLE         full quantity, arrives with at least the configured
                   risk margin before the deadline (or no deadline set).
    DEADLINE_RISK  full quantity, but arrives with less than the risk
                   margin to spare, or its travel time is only a
                   straight-line estimate (no recorded route).
    PARTIAL        can deliver some but not all requested units in time.
    IMPOSSIBLE     cannot deliver any compatible unit before the deadline
                   (or the deadline has already passed, or nothing
                   compatible is reachable).
    """

    VIABLE = "VIABLE"
    DEADLINE_RISK = "DEADLINE_RISK"
    PARTIAL = "PARTIAL"
    IMPOSSIBLE = "IMPOSSIBLE"


class RouteBasis(str, enum.Enum):
    """How a pathway leg's travel was determined."""

    LOCAL = "LOCAL"  # the destination facility's own stock -- no transport
    ROUTED = "ROUTED"  # an ordered list of recorded transport routes
    # No recorded route connects the facilities; travel time is estimated
    # from straight-line distance at the assumed road speed. Never VIABLE
    # when a deadline exists.
    STRAIGHT_LINE_ESTIMATE = "STRAIGHT_LINE_ESTIMATE"


class EmergencyOverrideKind(str, enum.Enum):
    """Audited, ADMIN-only exceptions to an emergency's normal policy."""

    DONOR_DISTANCE = "DONOR_DISTANCE"  # rank donors beyond MAX_DONOR_DISTANCE_KM
    UNVERIFIED_FACILITY = "UNVERIFIED_FACILITY"  # allow unverified facilities


class GeocodeSource(str, enum.Enum):
    """Where an emergency's coordinates came from."""

    USER_INPUT = "USER_INPUT"  # entered/pinned by the reporter (or device GPS)
    FACILITY = "FACILITY"  # copied from the target facility's coordinates
    GAZETTEER = "GAZETTEER"  # approximate centroid of a known city
    GEOCODER = "GEOCODER"  # an external geocoding service (configurable)


class GraphPathwaySource(str, enum.Enum):
    """Which engine actually produced a resource-pathway result.

    Every pathway API response carries this so callers always know
    honestly whether the ranking came from a real Neo4j graph traversal
    or the haversine-distance fallback used when Neo4j is unreachable.
    """

    GRAPH = "GRAPH"
    # Legacy label for rows written by the pre-gap-elimination fallback
    # (straight-line ranking that ignored routes). No longer produced.
    HAVERSINE_FALLBACK = "HAVERSINE_FALLBACK"
    # The same bounded shortest-path question answered over the
    # TransportRoute table in PostgreSQL (app.core.route_graph) -- used when
    # Neo4j is not configured or not reachable.
    RELATIONAL_ROUTES = "RELATIONAL_ROUTES"


class BloodUnitState(str, enum.Enum):
    """Lifecycle of one physical BloodUnit row (app/models/blood_unit.py).

    Phase 34: a bounded, real state machine -- deliberately NOT the full
    collection/lab-testing/dispatch/transfusion pipeline sometimes sketched
    for blood-bank software in general. This application has no lab-
    testing workflow and no transfusion-confirmation step anywhere in its
    existing product surface (see docs/LIFELINK_PHASE_34_BLOOD_UNIT_LIFECYCLE_REPORT.md
    section 4) -- so only the four states this codebase can actually
    enforce end-to-end, server-side, are implemented:

    Phase D (shipment lifecycle) update: this still does NOT grow a fifth
    state. A `Shipment`/`ShipmentItem` (app/models/shipment.py) is a
    tracking/custody layer on top of already-ALLOCATED units, not a new
    BloodUnit state -- see that module's docstring for why ALLOCATED
    staying terminal here is a deliberate design choice, not an oversight
    this phase forgot to close.

      AVAILABLE  -- on hand, unreserved, selectable for a new reservation
                    or a direct allocation.
      RESERVED   -- on hand, earmarked for one specific request/emergency,
                    not yet physically dispatched. Can return to AVAILABLE
                    (the RELEASE action) or proceed to ALLOCATED.
      ALLOCATED  -- physically dispatched. Terminal in this application --
                    matches the pre-existing ALLOCATE semantics in
                    app/services/inventory_service.py, which already
                    treats allocation as "left the building"; there is no
                    un-allocate capability anywhere in the existing
                    product.
      EXPIRED    -- permanently removed from the usable pool. Covers
                    natural shelf-life expiry, a manual EXPIRE action, and
                    a downward RECONCILE -- one "no longer usable" state,
                    distinguished by the associated HospitalInventoryLog
                    action/note rather than by additional states (see the
                    Phase 34 report for the justification).

    See app/core/blood_unit_lifecycle.py for the transition table.
    """

    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    ALLOCATED = "ALLOCATED"
    EXPIRED = "EXPIRED"
    # Gap elimination: physically on hand but held out of use pending
    # review (e.g. received after a cold-chain excursion). Not counted as
    # available; can be released back to AVAILABLE or discarded.
    QUARANTINED = "QUARANTINED"
    # Destroyed as unusable (damaged, spoiled, thawed plasma past its
    # post-thaw window, failed quarantine review). Terminal.
    DISCARDED = "DISCARDED"


# Terminal blood-unit states -- once here, a unit can never re-enter the
# matching pool through any transition this codebase implements.
BLOOD_UNIT_TERMINAL_STATES: frozenset[BloodUnitState] = frozenset(
    {BloodUnitState.ALLOCATED, BloodUnitState.EXPIRED, BloodUnitState.DISCARDED}
)


class ShipmentItemOutcome(str, enum.Enum):
    """Per-unit reconciliation outcome of one ShipmentItem."""

    PENDING = "PENDING"  # not yet reconciled
    RECEIVED_OK = "RECEIVED_OK"  # received in usable condition
    QUARANTINED = "QUARANTINED"  # received, held for review
    DAMAGED = "DAMAGED"  # physically damaged (bag broken, leaking) -> discarded
    SPOILED = "SPOILED"  # biologically unusable (temperature, haemolysis) -> discarded
    LOST = "LOST"  # never arrived; no destination unit is created
    RETURNED = "RETURNED"  # failed shipment: unit returned to origin stock
    REDIRECTED = "REDIRECTED"  # failed shipment: delivered to another facility
    EXPIRED_ON_ARRIVAL = "EXPIRED_ON_ARRIVAL"  # past shelf life on receipt (automatic)


class TelemetrySource(str, enum.Enum):
    """Where a cold-chain temperature reading came from. SIMULATED readings
    come from the development simulator and are always labelled so."""

    SENSOR = "SENSOR"
    MANUAL = "MANUAL"
    SIMULATED = "SIMULATED"


class ColdChainStatus(str, enum.Enum):
    """Evaluation of a shipment's temperature readings against the
    acceptable range for the components it carries."""

    NO_DATA = "NO_DATA"
    WITHIN_RANGE = "WITHIN_RANGE"
    EXCURSION = "EXCURSION"  # exactly one out-of-range episode
    REPEATED_EXCURSIONS = "REPEATED_EXCURSIONS"  # two or more episodes
    DATA_GAP = "DATA_GAP"  # readings missing for longer than allowed
    SENSOR_FAILURE = "SENSOR_FAILURE"  # error-flagged or physically implausible readings


class BloodUnitProvenance(str, enum.Enum):
    """Honesty marker distinguishing a unit created by a real ADD action
    from one created by the Phase 34 migration's backfill of pre-existing
    HospitalInventory aggregate counts, whose true individual collection
    dates are unknowable (see the Phase 34 migration and report sections
    18/23 -- "do not invent false history"). REAL units always carry a
    genuine `received_at` (the moment the real ADD action ran).
    LEGACY_BACKFILL units have `received_at` set to the migration's own
    run time -- documented as such, never fabricated as an earlier date --
    and `expires_at` left NULL, since no shelf-life clock can honestly be
    started from an unknown collection date.
    """

    REAL = "REAL"
    LEGACY_BACKFILL = "LEGACY_BACKFILL"
    # Phase D: created at a DESTINATION hospital by `receive_shipment` --
    # the honest counterpart to REAL for a unit this hospital didn't
    # collect itself but took real custody of via a tracked Shipment.
    # `received_at` is the real receipt timestamp; `expires_at` is COPIED
    # from the original (origin-side) unit's own `expires_at`, never
    # recomputed from this later received_at -- changing custody does not
    # reset a physical unit's real shelf life. See BloodUnit.source_unit_id
    # / source_shipment_id.
    TRANSFERRED_IN = "TRANSFERRED_IN"


class BloodComponent(str, enum.Enum):
    """Which component of a donation a `BloodUnit` row represents.

    Phase 35: every `BloodUnit` before this phase was implicitly whole
    blood (the only thing this system ever tracked) -- this enum makes
    that explicit rather than changing meaning retroactively.
    WHOLE_BLOOD is both the default for new units added without a
    specified component AND the honest backfill value for every unit
    that existed before this column did (see the Phase 35 migration --
    not a guess, since whole blood is genuinely the only thing a
    pre-Phase-35 ADD action could have meant).

    Shelf life per component is NOT hardcoded on this enum -- see
    `app.core.clinical_sources.COMPONENT_SHELF_LIFE`, which cites a named
    source document for each figure, per the Phase 35 master prompt's
    explicit prohibition on inventing clinical numbers.

    Compatibility rules differ by component (red cells vs. plasma are
    OPPOSITE in which type is the "universal" donor) -- see
    `app.core.component_compatibility`, not this module.
    """

    WHOLE_BLOOD = "WHOLE_BLOOD"
    RED_BLOOD_CELLS = "RED_BLOOD_CELLS"
    PLASMA = "PLASMA"
    PLATELETS = "PLATELETS"


# NOTE: the ABO/Rh red-cell compatibility matrix (`can_donate_to`,
# `compatible_donor_types`) used to live here. Phase 9 moved it to
# `app.core.blood_compatibility` -- the dedicated, documented "one
# authoritative domain implementation" the compatibility engine spec
# calls for -- so it's no longer buried in this grab-bag enums module.
# Import it from there; nothing in this file re-exports it, so an
# accidental new duplicate here would show up as an unused-import lint
# failure rather than silently shadowing the real thing.


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)
