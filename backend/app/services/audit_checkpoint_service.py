"""Hardening -- HMAC-signed audit-chain checkpoints kept OUTSIDE the database.

The hash chain (app.core.audit_chain) detects edits, deletions, insertions
and reordering of sealed audit rows. On its own it cannot detect two
things, because it has no secret: an attacker with database write access
who recomputes every later hash, and truncation of the newest rows. A
checkpoint closes both gaps:

    checkpoint = {v, key_id, seq, entry_hash, created_at, prev_mac}
    mac        = HMAC-SHA256(key[key_id], canonical_json(checkpoint))

  * `seq`/`entry_hash` is the sealed head at the time of the checkpoint.
    If the database is later recomputed, the row at `seq` no longer has
    `entry_hash`; if it is truncated below `seq`, the row is missing.
    Either fails verification.
  * The MAC needs a key the database never sees, so a database-only
    attacker cannot forge a checkpoint that matches a recomputed chain.
  * `prev_mac` chains the checkpoints themselves: deleting, editing or
    reordering a line in the middle of the checkpoint log is detected.

Storage: one JSON object per line appended (O_APPEND + fsync) to
AUDIT_CHECKPOINT_PATH. That path must be on storage the database
credentials cannot write -- a separate volume or host, ideally append-only
or object-locked. What remains undetectable, stated plainly:

  * Removing the newest lines of the checkpoint log itself (tail
    truncation of the log) by someone who can write that storage. Use
    append-only/WORM storage, or copy checkpoints to a second system.
  * Anything done by someone holding BOTH the database owner role AND the
    checkpoint key.
  * Rows changed before the first checkpoint was taken AND recomputed
    consistently (the oldest checkpoint is the earliest attestation).

This is tamper EVIDENCE. Nothing here makes the audit log immutable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.models.audit_log import AuditLog

log = logging.getLogger("lifelink.audit_checkpoint")

VERSION = 1
GENESIS_MAC = "0" * 64


class CheckpointsDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class Checkpoint:
    key_id: str
    seq: int
    entry_hash: str
    created_at: str
    prev_mac: str
    mac: str


@dataclass
class CheckpointVerifyResult:
    ok: bool
    checked: int
    latest_seq: int | None
    latest_hash: str | None
    reason: str | None = None
    line: int | None = None


def _keys() -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    for part in (settings.audit_checkpoint_retired_keys or "").split(";"):
        part = part.strip()
        if not part:
            continue
        kid, _, key = part.partition("=")
        if kid and key:
            keys[kid.strip()] = key.strip().encode()
    if settings.audit_checkpoint_key:
        keys[settings.audit_checkpoint_key_id] = settings.audit_checkpoint_key.encode()
    return keys


def _canonical(key_id: str, seq: int, entry_hash: str, created_at: str, prev_mac: str) -> bytes:
    return json.dumps(
        {"v": VERSION, "key_id": key_id, "seq": seq, "entry_hash": entry_hash,
         "created_at": created_at, "prev_mac": prev_mac},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _mac(key: bytes, payload: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _path() -> Path:
    if not settings.audit_checkpoints_enabled:
        raise CheckpointsDisabled(
            "signed audit checkpoints are not configured "
            "(set AUDIT_CHECKPOINT_KEY and AUDIT_CHECKPOINT_PATH)"
        )
    return Path(settings.audit_checkpoint_path)


def read_lines() -> list[str]:
    path = _path()
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="ascii").splitlines() if ln.strip()]


def verify_log(lines: list[str] | None = None) -> CheckpointVerifyResult:
    """Verify every checkpoint's MAC and the prev_mac chain between them.
    Does not touch the database."""
    lines = read_lines() if lines is None else lines
    keys = _keys()
    prev_mac, prev_seq = GENESIS_MAC, 0
    last: Checkpoint | None = None
    for i, raw in enumerate(lines, start=1):
        try:
            d = json.loads(raw)
            cp = Checkpoint(
                key_id=str(d["key_id"]), seq=int(d["seq"]), entry_hash=str(d["entry_hash"]),
                created_at=str(d["created_at"]), prev_mac=str(d["prev_mac"]), mac=str(d["mac"]),
            )
            if int(d.get("v", -1)) != VERSION:
                raise ValueError("unknown version")
        except (ValueError, KeyError, TypeError):
            return CheckpointVerifyResult(False, i - 1, *_latest(last), "malformed checkpoint", i)
        key = keys.get(cp.key_id)
        if key is None:
            return CheckpointVerifyResult(
                False, i - 1, *_latest(last), f"unknown checkpoint key id {cp.key_id!r}", i
            )
        payload = _canonical(cp.key_id, cp.seq, cp.entry_hash, cp.created_at, cp.prev_mac)
        expected = _mac(key, payload)
        if not hmac.compare_digest(expected, cp.mac):
            return CheckpointVerifyResult(
                False, i - 1, *_latest(last), "checkpoint signature invalid (forged or edited)", i
            )
        if cp.prev_mac != prev_mac:
            return CheckpointVerifyResult(
                False, i - 1, *_latest(last),
                "checkpoint log broken: prev_mac does not match the previous checkpoint "
                "(a checkpoint was removed, inserted or reordered)", i,
            )
        if cp.seq < prev_seq:
            return CheckpointVerifyResult(
                False, i - 1, *_latest(last), "checkpoint seq went backwards", i
            )
        prev_mac, prev_seq, last = cp.mac, cp.seq, cp
    return CheckpointVerifyResult(True, len(lines), *_latest(last))


def _latest(cp: Checkpoint | None) -> tuple[int | None, str | None]:
    return (cp.seq, cp.entry_hash) if cp else (None, None)


def parsed(lines: list[str] | None = None) -> list[Checkpoint]:
    lines = read_lines() if lines is None else lines
    out = []
    for raw in lines:
        d = json.loads(raw)
        out.append(Checkpoint(d["key_id"], int(d["seq"]), d["entry_hash"], d["created_at"],
                              d["prev_mac"], d["mac"]))
    return out


def last_checkpoint_time():
    """created_at of the newest checkpoint line (None if there is none)."""
    from datetime import datetime

    lines = read_lines()
    if not lines:
        return None
    return datetime.fromisoformat(json.loads(lines[-1])["created_at"])


def status() -> dict:
    """Operational status for /health and the admin UI -- never the key."""
    if not settings.audit_checkpoints_enabled:
        return {"enabled": False, "required": settings.audit_checkpoint_required,
                "warning": "Audit checkpoints are DISABLED: a database owner who recomputes "
                           "the whole hash chain, or truncates its newest rows, would not be "
                           "detected. Set AUDIT_CHECKPOINT_KEY and AUDIT_CHECKPOINT_PATH."}
    lines = read_lines()
    state = verify_log(lines)
    return {"enabled": True, "required": settings.audit_checkpoint_required,
            "key_id": settings.audit_checkpoint_key_id, "checkpoints": len(lines),
            "log_verifies": state.ok, "latest_seq": state.latest_seq,
            "warning": None if state.ok else f"checkpoint log does not verify: {state.reason}"}


def write_checkpoint(db: Session) -> Checkpoint | None:
    """Append a signed checkpoint of the current sealed head. Refuses (raises)
    if the existing log does not verify -- a broken log is never extended
    as though it were fine. Returns None if nothing is sealed yet."""
    path = _path()
    lines = read_lines()
    state = verify_log(lines)
    if not state.ok:
        raise RuntimeError(
            f"refusing to extend a checkpoint log that does not verify: {state.reason}"
        )
    head = db.execute(
        select(AuditLog.chain_seq, AuditLog.entry_hash)
        .where(AuditLog.chain_seq.is_not(None))
        .order_by(AuditLog.chain_seq.desc()).limit(1)
    ).first()
    if head is None:
        return None
    prev_mac = json.loads(lines[-1])["mac"] if lines else GENESIS_MAC
    key_id = settings.audit_checkpoint_key_id
    created_at = utcnow().isoformat(timespec="microseconds")
    mac = _mac(
        settings.audit_checkpoint_key.encode(),
        _canonical(key_id, head.chain_seq, head.entry_hash, created_at, prev_mac),
    )
    cp = Checkpoint(key_id, head.chain_seq, head.entry_hash, created_at, prev_mac, mac)
    line = json.dumps(
        {"v": VERSION, "key_id": key_id, "seq": cp.seq, "entry_hash": cp.entry_hash,
         "created_at": created_at, "prev_mac": prev_mac, "mac": mac},
        sort_keys=True, separators=(",", ":"),
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, line.encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    log.info("audit checkpoint written", extra={"seq": cp.seq})
    return cp


def verify_against_db(db: Session) -> CheckpointVerifyResult:
    """Verify the checkpoint log, then that every checkpointed (seq, hash) is
    still present, unchanged, in the database. Combined with the chain
    verification this detects a recomputed chain and truncation below the
    latest checkpoint."""
    lines = read_lines()
    state = verify_log(lines)
    if not state.ok:
        return state
    cps = parsed(lines)
    wanted = {cp.seq for cp in cps}
    stored: dict[int, str] = {}
    seqs = sorted(wanted)
    for i in range(0, len(seqs), 1000):
        chunk = seqs[i:i + 1000]
        for seq, h in db.execute(
            select(AuditLog.chain_seq, AuditLog.entry_hash).where(AuditLog.chain_seq.in_(chunk))
        ).all():
            stored[seq] = h
    for n, cp in enumerate(cps, start=1):
        if cp.seq not in stored:
            return CheckpointVerifyResult(
                False, n - 1, state.latest_seq, state.latest_hash,
                f"audit entry {cp.seq} named by signed checkpoint {n} is missing "
                "(log truncated or entries deleted after the checkpoint)", n,
            )
        if stored[cp.seq] != cp.entry_hash:
            return CheckpointVerifyResult(
                False, n - 1, state.latest_seq, state.latest_hash,
                f"audit entry {cp.seq} no longer has the hash in signed checkpoint {n} "
                "(chain was rewritten/recomputed after the checkpoint)", n,
            )
    return state


def latest_verified() -> Checkpoint | None:
    """The newest checkpoint of a log that verifies (for incremental chain
    verification), or None."""
    if not settings.audit_checkpoints_enabled:
        return None
    lines = read_lines()
    if not lines or not verify_log(lines).ok:
        return None
    return parsed(lines)[-1]
