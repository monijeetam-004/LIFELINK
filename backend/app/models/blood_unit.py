"""Phase 34 -- one physical blood unit's authoritative lifecycle record.

See `app/core/blood_unit_lifecycle.py` for the transition table and
`docs/LIFELINK_PHASE_34_BLOOD_UNIT_LIFECYCLE_REPORT.md` for the full design
rationale. This is the new source of truth for per-unit state; the
pre-existing `HospitalInventory.units`/`reserved_units` aggregate counters
remain the externally-observed response shape (byte-identical API contract --
see report section 8) but are now provably-consistent COUNT()s over these
rows rather than an independent number, mutated atomically in the same
transaction and under the same row lock (`app/services/inventory_service.py`).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import BloodComponent, BloodType, BloodUnitProvenance, BloodUnitState


class BloodUnit(TimestampMixin, Base):
    __tablename__ = "blood_units"
    __table_args__ = (
        # Phase 35: the hot query is now "give me N AVAILABLE units of this
        # blood type AND component at this hospital, in FEFO order" --
        # component joins the existing composite index rather than getting
        # a separate one, since every real selection query (reserve/
        # release/allocate/expire) always filters on all four columns
        # together (report section 21/26 precedent: index from the actual
        # query shape, not speculatively).
        Index(
            "ix_blood_units_hospital_type_component_state",
            "hospital_id", "blood_type", "component", "state",
        ),
        # Supports the lazy expiry sweep's "state IN (...) AND expires_at
        # <= now()" scan without a sequential scan over every unit this
        # hospital has ever had, including terminal ones.
        Index("ix_blood_units_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blood_type: Mapped[BloodType] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=False
    )
    # Phase 35: which component of a donation this row represents.
    # WHOLE_BLOOD is the honest default/backfill value -- see
    # BloodComponent's docstring; this was the only thing this system
    # ever tracked before this column existed.
    component: Mapped[BloodComponent] = mapped_column(
        Enum(BloodComponent, name="blood_component"),
        nullable=False,
        server_default=BloodComponent.WHOLE_BLOOD.value,
    )
    # Phase 35: a human-readable, globally-unique identifier safe to print
    # on a physical label or encode as a barcode/QR -- contains no donor
    # PII (see app.services.inventory_service._generate_unit_code).
    # Immutable once set: nothing in this codebase ever updates it after
    # creation.
    unit_code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    # Phase 35: optional traceability link to the Donation event a
    # hospital asserts this unit came from. Like Donation itself, this is
    # a hospital-asserted claim, not independently/cryptographically
    # verified -- see app.services.inventory_service.add_stock's docstring
    # for the honesty boundary. NULL for the overwhelming majority of
    # units (most stock is still added without a specific donation
    # reference, exactly as before this phase), and always NULL for
    # LEGACY_BACKFILL rows (no donation to honestly link pre-Phase-34
    # backfilled stock to).
    donation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("donations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    state: Mapped[BloodUnitState] = mapped_column(
        Enum(BloodUnitState, name="blood_unit_state"),
        nullable=False,
        server_default=BloodUnitState.AVAILABLE.value,
        index=True,
    )
    provenance: Mapped[BloodUnitProvenance] = mapped_column(
        Enum(BloodUnitProvenance, name="blood_unit_provenance"),
        nullable=False,
        server_default=BloodUnitProvenance.REAL.value,
    )
    # When this unit entered inventory: a real ADD action's timestamp, or
    # (for LEGACY_BACKFILL rows created by the Phase 34 migration) the
    # migration's own run time -- never a fabricated earlier date. See
    # BloodUnitProvenance's docstring.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL means "no known/enforced shelf-life clock" -- always true for
    # LEGACY_BACKFILL units (real collection date unknown) and never
    # populated with a guessed value. Set for REAL units at creation as
    # `received_at + clinical_sources.shelf_life_days(component)` (the cited
    # per-component figure -- Phase 35; this comment previously named a
    # config setting that had stopped being read, corrected in Phase E).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Phase E: which exact clinical rule started this unit's expiry clock,
    # e.g. "shelf_life.RED_BLOOD_CELLS@2026.10.05-1" (see
    # app.core.clinical_sources.rule_ref). NULL exactly when `expires_at` is
    # NULL. Lets an auditor answer "which cited figure produced this
    # expiry date" after the rule table has since changed -- the unit keeps
    # the reference it was created under, never re-derived.
    expiry_rule_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Timestamp of this unit's most recent state transition (distinct from
    # `updated_at`, which TimestampMixin bumps on *any* column change --
    # this one is specifically "when did `state` last change").
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Which open request/emergency this unit is currently earmarked or was
    # dispatched for -- set on RESERVE or a direct ALLOCATE, cleared when
    # the unit returns to AVAILABLE (RELEASE) or reaches EXPIRED. SET NULL
    # on delete rather than CASCADE: this unit's own history must survive
    # even if the request/emergency row it once served were ever removed
    # (neither model is ever hard-deleted in this app today, but SET NULL
    # is the safe, non-destructive default regardless).
    reserved_for_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reserved_for_emergency_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emergencies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Phase D: mirrors reserved_for_request_id/reserved_for_emergency_id --
    # which shipment this unit is currently earmarked for (CREATED) or was
    # physically allocated against (DISPATCHED). Cleared on CANCEL
    # (RESERVED -> AVAILABLE, same as any other release). Never cleared by
    # DISPATCH/RECEIVE -- once ALLOCATED (terminal), this stays set as part
    # of this unit's own permanent history of which shipment moved it; see
    # app.services.shipment_service.
    reserved_for_shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Phase D traceability, DESTINATION-side only (always NULL on the
    # ORIGIN-side unit that was actually shipped -- that unit's own state
    # just stays ALLOCATED, terminal, exactly as before this phase). Set
    # together, only by shipment_service.receive_shipment, on the NEW
    # BloodUnit row it creates at the destination hospital:
    #   source_shipment_id -- which Shipment delivered this unit.
    #   source_unit_id      -- which ORIGIN-side BloodUnit row (now
    #                          permanently ALLOCATED there) this one
    #                          represents the arrival of. Self-referential;
    #                          answers "where did this physically come
    #                          from" without duplicating donation/component
    #                          data -- follow source_unit_id to the origin
    #                          row, which still carries its own
    #                          donation_id if one was ever recorded.
    source_shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blood_units.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Points at this unit's most recent HospitalInventoryLog row. Phase 34
    # deliberately reuses the existing audit-log system rather than
    # building a second, separate BloodUnitTransition history table --
    # every transition this unit makes already writes a corresponding
    # HospitalInventoryLog row in the same transaction (see report
    # sections 14/20 for the full "why a second table would be redundant"
    # reasoning).
    last_log_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_inventory_logs.id", ondelete="SET NULL"), nullable=True
    )
    # ---- Gap elimination: plasma thaw lifecycle ----
    # Set once when a frozen PLASMA unit is thawed. From then on the unit's
    # usable life ends at the EARLIER of `expires_at` (frozen shelf life)
    # and `thaw_expires_at` (the cited post-thaw window, see
    # app.core.clinical_sources.THAWED_PLASMA) -- never the frozen figure alone.
    thawed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thaw_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    thaw_rule_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Why a unit is QUARANTINED / DISCARDED (plain text, also in the audit log).
    status_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
