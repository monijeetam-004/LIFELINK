# LifeLink — Domain Model

The master prompt for Phase 31 names this file in two places under two slightly different filenames (Section 2: `LIFELINK_PHASE_31_DOMAIN_MODEL.md`; Section 37: `LIFELINK_DOMAIN_MODEL.md`). Rather than maintain two divergent copies of the same inventory, this file is a thin pointer: the full, verified domain inventory — every entity, its model, fields, relationships, lifecycle, who creates/modifies/views it, validation, DB constraints, idempotency, concurrency protection, frontend representation, and tests — lives in **`docs/LIFELINK_PHASE_31_DOMAIN_MODEL.md`**.

See also, for material this document deliberately doesn't repeat:
- `docs/LIFELINK_INVARIANTS.md` — the formal invariants each entity actually upholds, and how each is enforced/verified.
- `docs/LIFELINK_PRIVACY_ACCESS_MATRIX.md` — the ROLE × RESOURCE × ACTION authorization matrix.
- `docs/LIFELINK_THREAT_MODEL.md` — attacker classes and mitigations per entity/surface.
- `docs/LIFELINK_PHASE_31_DOMAIN_AUDIT.md` — the full narrative audit (what was found, what was fixed, what was deliberately left alone, and why).
