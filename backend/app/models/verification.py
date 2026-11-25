from __future__ import annotations

import uuid

from sqlalchemy import JSON, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import VerificationKind, VerificationStatus


class VerificationRequest(TimestampMixin, Base):
    """A donor-identity or hospital-facility verification submission.

    Documents are stored in object storage (see app/core/storage.py); this row
    only holds the storage keys and metadata, never the file bytes. Approving a
    request is the only thing that ever sets DonorProfile.identity_verified or
    HospitalProfile.verified — see app/services/verification_service.py.
    """

    __tablename__ = "verification_requests"
    __table_args__ = (
        # Phase 31 domain-correctness fix: app.api.routes.verifications.
        # submit_verification already had a pre-flight "does a PENDING
        # request of this kind already exist for this user" check, but
        # nothing enforced it at the database level -- a genuine
        # check-then-insert TOCTOU race (two concurrent submissions of the
        # same kind could both pass the pre-flight SELECT and both INSERT).
        # A partial unique index -- the same pattern already used for
        # `uq_emergency_pathway_one_active` -- makes "at most one PENDING
        # request per (user, kind)" a real invariant instead of a
        # best-effort check: APPROVED/REJECTED rows for the same user/kind
        # must still be allowed to accumulate freely (resubmission after a
        # rejection, or a fresh submission after an old approval), so this
        # can't be a plain UniqueConstraint on (user_id, kind).
        Index(
            "uq_verification_one_pending_per_kind",
            "user_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        # Phase E: document versioning. Every submission of a given kind by
        # a given user gets the next integer version (1, 2, 3, ...); this
        # makes "version N of this user's DONOR_IDENTITY documents" a real,
        # unique identity rather than a display convention. Two concurrent
        # submissions computing the same next version can never both
        # persist -- the loser hits this constraint (or the PENDING partial
        # index above, whichever fires first) inside the route's SAVEPOINT
        # and gets the same honest 409.
        UniqueConstraint("user_id", "kind", "version", name="uq_verification_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[VerificationKind] = mapped_column(
        Enum(VerificationKind, name="verification_kind"), nullable=False, index=True
    )
    status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"),
        nullable=False,
        server_default=VerificationStatus.PENDING.value,
        index=True,
    )
    # List of {"key": str, "filename": str, "content_type": str, "size": int}
    documents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Phase E -- versioning. Submissions are append-only: there is no
    # replace/delete endpoint (proven by tests/test_phase20_secure_file_storage.py),
    # every document lives under a fresh uuid-prefixed storage key, and a
    # resubmission is a NEW row whose `supersedes_request_id` points at the
    # previous submission of the same (user, kind). Nothing an earlier
    # version recorded -- its documents, checksums, decision, reviewer --
    # is ever rewritten by a later one. `version` defaults to 1 so every
    # pre-Phase-E row reads as "the first version" (the migration backfills
    # real per-(user, kind) ordinals by created_at).
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    supersedes_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_requests.id", ondelete="SET NULL"), nullable=True
    )
