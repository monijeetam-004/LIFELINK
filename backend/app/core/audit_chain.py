"""Phase E -- tamper-EVIDENT audit chain: the pure, DB-free half.

What this is, stated precisely (see docs/LIFELINK_AUDIT_CHAIN.md):

  Every sealed `audit_logs` row stores `entry_hash = SHA-256(canonical(row,
  prev_hash))`, where `prev_hash` is the previous sealed row's
  `entry_hash`. Changing, deleting, inserting or reordering any sealed row
  therefore breaks the recomputation from that point on, and
  `verify_chain` reports exactly where.

What this is NOT:

  * Not immutable. Anyone with write access to the database can still
    UPDATE or DELETE rows. What they cannot do is do it *silently* without
    also recomputing every later hash -- and even that is detectable if a
    head hash (`chain_seq`, `entry_hash`) was recorded somewhere outside
    the database beforehand ("anchoring"; see the doc). Without an
    external anchor, truncating the newest rows and leaving a shorter,
    internally consistent chain is NOT detectable by this mechanism alone.
  * Not a blockchain, not a signature: the per-row hash has no secret key.
    Production hardening adds HMAC-signed checkpoints of the head, stored
    outside the database (app.services.audit_checkpoint_service), which
    detect a recomputed or truncated chain, and an append-only trigger
    guard (app.core.audit_guard) that the table owner can still disable.

Canonical form (hash version 1): compact JSON, keys sorted, UTF-8,
`ensure_ascii=True`; UUIDs as lowercase hyphenated strings; timestamps as
UTC ISO-8601 with microseconds; `meta` exactly as decoded from JSONB.
Changing ANY of this is a new `HASH_VERSION` -- verifiers dispatch on the
per-row `hash_version`, so rows sealed under an old version stay
verifiable.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

HASH_VERSION = 1
GENESIS_HASH = "0" * 64

#: Fixed key for `pg_advisory_xact_lock`, held only by the short sealing
#: transaction (never by a business transaction) -- see
#: app.services.audit_chain_service. Arbitrary but stable; ASCII "LLAUDIT".
ADVISORY_LOCK_KEY = 0x4C4C4155444954


@dataclass(frozen=True)
class ChainFields:
    """The exact row fields an entry hash covers (version 1)."""

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: uuid.UUID | None
    ip: str | None
    user_agent: str | None
    meta: dict[str, Any]
    created_at: datetime


def _uuid(v: uuid.UUID | None) -> str | None:
    return str(v) if v is not None else None


def _ts(v: datetime) -> str:
    if v.tzinfo is None:
        # Every audit timestamp is timezone-aware (timestamptz); a naive one
        # here means a caller bypassed the model -- refuse rather than guess.
        raise ValueError("audit created_at must be timezone-aware")
    return v.astimezone(UTC).isoformat(timespec="microseconds")


def canonical_bytes(fields: ChainFields, *, seq: int, prev_hash: str) -> bytes:
    payload = {
        "v": HASH_VERSION,
        "seq": seq,
        "prev_hash": prev_hash,
        "id": _uuid(fields.id),
        "actor_user_id": _uuid(fields.actor_user_id),
        "action": fields.action,
        "target_type": fields.target_type,
        "target_id": _uuid(fields.target_id),
        "ip": fields.ip,
        "user_agent": fields.user_agent,
        "meta": fields.meta,
        "created_at": _ts(fields.created_at),
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def entry_hash(fields: ChainFields, *, seq: int, prev_hash: str) -> str:
    return hashlib.sha256(canonical_bytes(fields, seq=seq, prev_hash=prev_hash)).hexdigest()


def normalize_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Round-trip `meta` through JSON so what is stored in JSONB is exactly
    what will later be hashed (UUIDs/dates/Decimals become strings once,
    here, instead of depending on the driver's serializer)."""
    return json.loads(json.dumps(meta or {}, default=str))


@dataclass(frozen=True)
class ChainBreak:
    seq: int | None
    audit_id: str | None
    reason: str


@dataclass
class VerifyResult:
    ok: bool
    checked: int
    head_seq: int | None
    head_hash: str | None
    unsealed: int
    first_break: ChainBreak | None = None


def check_link(
    *, expected_seq: int, expected_prev: str,
    seq: int, prev_hash: str | None, stored_hash: str | None, hash_version: int | None,
    fields: ChainFields,
) -> str | None:
    """Return a human-readable reason if this row does not continue the
    chain correctly, else None."""
    if seq != expected_seq:
        return (f"sequence gap: expected seq {expected_seq}, found {seq} "
                "(row(s) deleted or inserted)")
    if hash_version != HASH_VERSION:
        return f"unknown hash_version {hash_version}"
    if prev_hash != expected_prev:
        return "prev_hash does not match the previous entry's hash (chain re-linked or row removed)"
    actual = entry_hash(fields, seq=seq, prev_hash=prev_hash)
    if actual != stored_hash:
        return "entry_hash mismatch: row content changed after sealing"
    return None
