"""Phase E -- cross-domain invariant checks, run as plain SQL against
whatever database the session points at.

Each check returns the rows that VIOLATE it (empty == holds). Used by
tests/test_phase_e_invariants.py after real end-to-end scenarios, and by
scripts/check_invariants.py against a real (dev/staging) database. These
checks DETECT violations; they do not enforce anything -- enforcement lives
where each invariant's doc entry (docs/LIFELINK_INVARIANTS.md, 28-36) says.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class InvariantCheck:
    number: int
    name: str
    sql: str


CHECKS: tuple[InvariantCheck, ...] = (
    InvariantCheck(
        28, "fulfilled_units within [0, units_needed]",
        "SELECT id FROM blood_requests WHERE fulfilled_units < 0 OR fulfilled_units > units_needed",
    ),
    InvariantCheck(
        29, "status bucket agrees with quantity (open requests)",
        # PENDING/MATCHING => nothing fulfilled yet; PARTIALLY_FULFILLED =>
        # strictly between 0 and needed; FULFILLED => fully met.
        "SELECT id FROM blood_requests WHERE "
        "(status IN ('PENDING','MATCHING') AND fulfilled_units <> 0) OR "
        "(status = 'PARTIALLY_FULFILLED' AND "
        "(fulfilled_units <= 0 OR fulfilled_units >= units_needed)) OR "
        "(status = 'FULFILLED' AND fulfilled_units <> units_needed)",
    ),
    InvariantCheck(
        30, "per-hospital allocations never exceed the request total",
        "SELECT br.id FROM blood_requests br JOIN hospital_request_responses hr "
        "ON hr.request_id = br.id GROUP BY br.id, br.fulfilled_units "
        "HAVING SUM(hr.units_allocated) > br.fulfilled_units",
    ),
    InvariantCheck(
        31, "a VERIFIED/REJECTED donation names who decided and when",
        "SELECT id FROM donations WHERE verification_status <> 'DONOR_REPORTED' "
        "AND (verified_by_user_id IS NULL OR verified_at IS NULL)",
    ),
    InvariantCheck(
        32, "nobody reviewed their own donation",
        "SELECT id FROM donations WHERE verified_by_user_id = donor_user_id",
    ),
    InvariantCheck(
        33, "verification versions are contiguous per (user, kind)",
        "SELECT user_id, kind FROM verification_requests GROUP BY user_id, kind "
        "HAVING MIN(version) <> 1 OR MAX(version) <> COUNT(*)",
    ),
    InvariantCheck(
        34, "reconciliation_status is set iff the shipment FAILED",
        "SELECT id FROM shipments WHERE (status = 'FAILED') <> (reconciliation_status IS NOT NULL)",
    ),
    InvariantCheck(
        35, "a returned unit points at a unit its failed shipment actually carried",
        "SELECT bu.id FROM blood_units bu JOIN shipments s ON s.id = bu.source_shipment_id "
        "WHERE s.status = 'FAILED' AND NOT EXISTS ("
        "  SELECT 1 FROM shipment_items si WHERE si.shipment_id = s.id "
        "  AND si.blood_unit_id = bu.source_unit_id)",
    ),
    InvariantCheck(
        36, "returned units only exist for RETURNED_TO_ORIGIN reconciliations, at the origin",
        "SELECT bu.id FROM blood_units bu JOIN shipments s ON s.id = bu.source_shipment_id "
        "WHERE s.status = 'FAILED' AND (s.reconciliation_status <> 'RETURNED_TO_ORIGIN' "
        "OR bu.hospital_id <> s.origin_facility_id)",
    ),
    InvariantCheck(
        37, "audit chain sequence has no gaps or duplicates",
        "SELECT 1 FROM (SELECT COUNT(*) AS n, MIN(chain_seq) AS lo, MAX(chain_seq) AS hi "
        "FROM audit_logs WHERE chain_seq IS NOT NULL) t "
        "WHERE t.n > 0 AND (t.lo <> 1 OR t.hi <> t.n)",
    ),
    InvariantCheck(
        38, "a unit with an expiry clock records which rule started it (post-Phase-E units)",
        # Units created before Phase E legitimately have no rule recorded
        # (never guessed); every unit created after the column existed must.
        "SELECT id FROM blood_units WHERE expires_at IS NOT NULL AND expiry_rule_ref IS NULL "
        "AND provenance = 'REAL' AND created_at > ("
        "  SELECT COALESCE(MIN(created_at), now()) FROM blood_units "
        "  WHERE expiry_rule_ref IS NOT NULL)",
    ),
    # ---- Gap elimination invariants ---------------------------------------
    InvariantCheck(
        39, "aggregate stock equals per-unit truth (units = AVAILABLE+RESERVED, "
            "reserved = RESERVED)",
        "SELECT hi.id FROM hospital_inventory hi LEFT JOIN ("
        "  SELECT hospital_id, blood_type, component,"
        "    count(*) FILTER (WHERE state IN ('AVAILABLE','RESERVED')) AS on_hand,"
        "    count(*) FILTER (WHERE state = 'RESERVED') AS held"
        "  FROM blood_units GROUP BY hospital_id, blood_type, component) u"
        "  ON u.hospital_id = hi.hospital_id AND u.blood_type = hi.blood_type"
        "  AND u.component = hi.component "
        "WHERE hi.units <> COALESCE(u.on_hand, 0) OR hi.reserved_units <> COALESCE(u.held, 0)",
    ),
    InvariantCheck(
        40, "at most one ACTIVE pathway per emergency",
        "SELECT emergency_id FROM emergency_pathways WHERE status = 'ACTIVE' "
        "GROUP BY emergency_id HAVING count(*) > 1",
    ),
    InvariantCheck(
        41, "active pathway reservations are backed by RESERVED units of that emergency",
        "SELECT pr.emergency_id, pr.facility_id, pr.blood_type FROM pathway_reservations pr "
        "WHERE pr.active GROUP BY pr.emergency_id, pr.facility_id, pr.blood_type, pr.component "
        "HAVING SUM(pr.units) > (SELECT count(*) FROM blood_units bu "
        "  WHERE bu.state = 'RESERVED' AND bu.reserved_for_emergency_id = pr.emergency_id "
        "  AND bu.hospital_id = pr.facility_id AND bu.blood_type = pr.blood_type "
        "  AND bu.component = pr.component)",
    ),
    InvariantCheck(
        42, "thaw clocks only on plasma, and never before the thaw",
        "SELECT id FROM blood_units WHERE (thawed_at IS NOT NULL OR thaw_expires_at IS NOT NULL) "
        "AND (component <> 'PLASMA' OR thawed_at IS NULL OR thaw_expires_at IS NULL "
        "OR thaw_expires_at < thawed_at)",
    ),
    InvariantCheck(
        43, "a decided shipment item records when, and a LOST unit has no destination unit",
        "SELECT id FROM shipment_items WHERE (outcome <> 'PENDING' AND outcome_at IS NULL) "
        "OR (outcome = 'LOST' AND resulting_unit_id IS NOT NULL)",
    ),
    InvariantCheck(
        44, "invalidated / superseded pathways record when and why",
        "SELECT id FROM emergency_pathways WHERE "
        "(status = 'INVALIDATED' AND (invalidated_at IS NULL OR invalidated_reason IS NULL)) "
        "OR (status = 'SUPERSEDED' AND superseded_at IS NULL)",
    ),
    InvariantCheck(
        45, "quarantined, discarded and expired units hold no reservation",
        "SELECT id FROM blood_units WHERE state IN ('QUARANTINED','DISCARDED','EXPIRED') "
        "AND (reserved_for_request_id IS NOT NULL OR reserved_for_emergency_id IS NOT NULL)",
    ),
    InvariantCheck(
        46, "a handed-off pathway reservation is closed and its shipment serves that emergency",
        "SELECT pr.id FROM pathway_reservations pr JOIN shipments s "
        "ON s.id = pr.handed_off_shipment_id WHERE pr.active "
        "OR s.emergency_id IS DISTINCT FROM pr.emergency_id",
    ),
    InvariantCheck(
        47, "a plan marked RECOVERY_REQUIRED holds no active reservation",
        "SELECT p.id FROM emergency_pathways p WHERE p.recovery_required_at IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM pathway_reservations pr WHERE pr.pathway_id = p.id "
        "AND pr.active)",
    ),
)


def run_all(db: Session) -> dict[int, list]:
    """{invariant number: [violating rows]} -- empty lists everywhere means
    every check holds."""
    return {c.number: list(db.execute(text(c.sql)).all()) for c in CHECKS}


def violations(db: Session) -> dict[int, list]:
    return {k: v for k, v in run_all(db).items() if v}
