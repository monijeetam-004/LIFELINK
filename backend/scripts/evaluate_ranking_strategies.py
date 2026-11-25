"""Offline comparison of the shortlist ranking modes on SYNTHETIC data (2026-10-31).

    python -m scripts.evaluate_ranking_strategies --out results.json [--units 2]

Compares UNIFIED_PRIORITY with the four unchanged lexicographic strategies
(NEAREST, TIME_AWARE, TIME_RESILIENCE, EVENT_DRIVEN) on the seeded network:
one URGENT blood request per (seed city x recipient group), placed at a
verified hospital of that city. Everything runs inside ONE transaction that
is rolled back: nothing is written, nobody is contacted.

For each request and mode, the eligible shortlist is ordered and contacting
is simulated in the shortlist's own batch size (``SHORTLIST_CONTACT_MULTIPLIER``
x donors still needed, capped at ``SHORTLIST_MAX_BATCH``), round by round,
until the requested number of donors has accepted or nobody is left.

Two outcome models, both synthetic -- neither is real donor behaviour:

* ``persona``  -- each synthetic donor answers as its seeded persona does
  (accept; slow = accepts one round late; decline / not-now; silent). The
  seed ALSO generated each donor's response history from the same persona,
  so a mode that uses response history is favoured BY CONSTRUCTION. This
  model checks that the plumbing uses the evidence; it cannot show that the
  model would work on real donors.
* ``independent`` -- every contacted donor accepts with the same probability
  (default 0.35), drawn from a fixed seed, independent of any evidence. A
  sound comparison should show no meaningful difference between modes here;
  a large difference would point at a bug, not at a better model.

* ``latent`` (added 2026-11-03, release gate F; the primary model) -- removes
  the leakage. Each donor gets a latent answer propensity and a latent
  accept-when-answering propensity from its own seeded stream (independent
  of the seeded personas). From those, a fresh response HISTORY is sampled
  on one stream (30 % of donors have none) and the FUTURE answers on
  another, so history and outcome are separate draws from the same trait --
  the usual assumption behind any history-based ranking, and the one this
  model tests. The unified score is recomputed from the sampled history (the
  four strategies do not read history); each request's outcome for a donor
  is the same whichever mode contacts them. Repeated over several seeds.

Also reported: eligibility violations in the returned rankings (every
returned donor is re-checked against the interval and the distance policy),
evidence coverage of the unified score, and its sensitivity when the
history-based weights (response, acceptance) are set to zero.

Only donors of the synthetic shortlist dataset (seed 20261031) take part;
the base seed's donors carry no persona and are excluded from the count of
outcomes (they are still ranked, and skipped as "no answer" if contacted).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import zlib
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select

from app.core import distance_policy, unified_priority
from app.core.config import settings
from app.core.eligibility import active_policy, is_eligible
from app.db.session import SessionLocal
from app.models.blood_request import BloodRequest
from app.models.enums import BloodRequestStatus, BloodType, ShortlistSubject, Urgency
from app.models.hospital import HospitalProfile
from app.models.profile import DonorProfile
from app.models.user import User
from app.services import donor_shortlist_service as svc
from app.services.demo_simulator import persona_of

RS = unified_priority.RankingStrategy
MODES = [RS.UNIFIED_PRIORITY, RS.NEAREST, RS.TIME_AWARE, RS.TIME_RESILIENCE, RS.EVENT_DRIVEN]
GROUPS = [BloodType.O_POS, BloodType.A_POS, BloodType.B_POS, BloodType.AB_POS]


def simulate(order, emails, needed, model, rng_seed, p_accept):
    """Contacts needed to reach `needed` acceptances, rounds to the first
    acceptance, and whether demand was met. Deterministic for a seed."""
    rng = random.Random(rng_seed)
    batch_cap = settings.shortlist_max_batch
    mult = settings.shortlist_contact_multiplier
    accepted = 0
    contacted = 0
    first_round = None
    pending_slow = 0
    i = 0
    rnd = 0
    while accepted < needed and (i < len(order) or pending_slow):
        rnd += 1
        accepted += pending_slow
        if pending_slow and first_round is None:
            first_round = rnd
        pending_slow = 0
        if accepted >= needed:
            break
        size = min(batch_cap, (needed - accepted) * mult)
        batch = order[i:i + size]
        i += len(batch)
        contacted += len(batch)
        for c in batch:
            if model == "independent":
                yes = rng.random() < p_accept
                slow = False
            else:
                p = persona_of(emails[c.donor_user_id]) if emails[c.donor_user_id].endswith(
                    "@" + svc.SYNTHETIC_EMAIL_DOMAIN) else None
                yes = p in ("accept", "slow")
                slow = p == "slow"
            if yes and slow:
                pending_slow += 1
            elif yes:
                accepted += 1
                if first_round is None:
                    first_round = rnd
        if not batch and not pending_slow:
            break
    return {"contacts": contacted, "rounds_to_first": first_round,
            "met": accepted >= needed, "accepted": min(accepted, needed)}


def _seeded(*parts) -> random.Random:
    return random.Random(zlib.crc32("|".join(str(p) for p in parts).encode()))


def latent_traits(seed, donor_id):
    r = _seeded(seed, "latent", donor_id)
    return r.betavariate(2, 2), r.betavariate(2, 3)  # P(answer), P(accept | answer)


def latent_history(seed, donor_id, traits):
    r = _seeded(seed, "history", donor_id)
    if r.random() < 0.3:
        return 0, 0, 0
    invited = r.randint(1, 8)
    answered = sum(r.random() < traits[0] for _ in range(invited))
    accepted = sum(r.random() < traits[1] for _ in range(answered))
    return invited, answered, accepted


def latent_order(cands, seed, subject, now, cfg):
    """Unified order with the history replaced by a sample from the latent
    traits (everything else -- distance, availability, verification -- as
    stored)."""
    rescored = []
    for c in cands:
        inv, ans, acc = latent_history(seed, c.donor_user_id, latent_traits(seed, c.donor_user_id))
        x = replace(c.inputs, invited=inv, answered=ans, accepted=acc)
        rescored.append(replace(c, inputs=x, priority=unified_priority.compute(x, cfg, now=now)))
    return svc.order_candidates(rescored, RS.UNIFIED_PRIORITY, subject.urgency, now)


def simulate_latent(order, needed, seed, request_key):
    """Same contact rounds as `simulate`; a donor accepts with
    P(answer) x P(accept | answer), drawn once per (request, donor)."""
    batch_cap = settings.shortlist_max_batch
    mult = settings.shortlist_contact_multiplier
    accepted = contacted = rnd = i = 0
    first_round = None
    while accepted < needed and i < len(order):
        rnd += 1
        size = min(batch_cap, (needed - accepted) * mult)
        batch = order[i:i + size]
        i += len(batch)
        contacted += len(batch)
        for c in batch:
            pa, pc = latent_traits(seed, c.donor_user_id)
            if _seeded(seed, "outcome", request_key, c.donor_user_id).random() < pa * pc:
                accepted += 1
                if first_round is None:
                    first_round = rnd
    return {"contacts": contacted, "rounds_to_first": first_round,
            "met": accepted >= needed, "accepted": min(accepted, needed)}


def violations(db, subject, cands) -> int:
    bad = 0
    ids = [c.donor_user_id for c in cands]
    profiles = {p.user_id: p for p in db.scalars(select(DonorProfile).where(
        DonorProfile.user_id.in_(ids)))} if ids else {}
    for c in cands:
        dp = profiles[c.donor_user_id]
        ok = dp.is_available and is_eligible(dp.last_donation_date)
        a = distance_policy.assess(
            origin_lat=subject.lat, origin_lng=subject.lng, origin_city=subject.city,
            donor_lat=dp.latitude, donor_lng=dp.longitude, donor_shares_location=dp.share_location,
            donor_city=dp.city, donor_alert_radius_km=dp.alert_radius_km,
            limit_km=settings.max_donor_distance_km)
        ok = ok and distance_policy.alert_skip_reason(a) is None
        bad += 0 if ok else 1
    return bad


def summarise(rows):
    out = {}
    for mode in [m.value for m in MODES]:
        r = [x for x in rows if x["mode"] == mode]
        met = [x for x in r if x["met"]]
        firsts = [x["rounds_to_first"] for x in r if x["rounds_to_first"] is not None]
        out[mode] = {
            "requests": len(r),
            "demand_met": len(met),
            "unmet_escalations": len(r) - len(met),
            "median_contacts_when_met": statistics.median([x["contacts"] for x in met]) if met
            else None,
            "mean_contacts_when_met": round(statistics.mean([x["contacts"] for x in met]), 2)
            if met else None,
            "median_rounds_to_first_acceptance": statistics.median(firsts) if firsts else None,
            "precision_among_contacted": round(sum(x["accepted"] for x in r)
                                               / max(1, sum(x["contacts"] for x in r)), 3),
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--units", type=int, default=2)
    ap.add_argument("--p-accept", type=float, default=0.35)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--latent-seeds", type=int, default=5)
    a = ap.parse_args(argv)
    if settings.is_production:
        print("refusing to run against ENVIRONMENT=production", file=sys.stderr)
        return 2
    db = SessionLocal()
    now = datetime.now(UTC)
    rows: list[dict] = []
    coverage: list[float] = []
    unscored = 0
    viol = {m.value: 0 for m in MODES}
    eligible_sizes: list[int] = []
    sensitivity = {"same_top_k": 0, "requests": 0}
    timings = {m.value: [] for m in MODES}
    base_cfg = unified_priority.config_from_settings()
    blind = replace(base_cfg, weights=unified_priority.validate_weights(
        {**base_cfg.weights, "response": 0.0, "acceptance": 0.0}))
    try:
        hospitals = db.scalars(select(HospitalProfile).where(
            HospitalProfile.verified.is_(True), HospitalProfile.latitude.is_not(None),
            HospitalProfile.city.is_not(None)).order_by(HospitalProfile.city,
                                                        HospitalProfile.name)).all()
        per_city = {}
        for h in hospitals:
            if not h.city.startswith("Demopur"):
                per_city.setdefault(h.city, h)
        emails = dict(db.execute(select(User.id, User.email)).all())
        for city, h in sorted(per_city.items()):
            for g in GROUPS:
                req = BloodRequest(requester_user_id=h.user_id, patient_user_id=h.user_id,
                                   blood_type=g, units_needed=a.units, urgency=Urgency.URGENT,
                                   status=BloodRequestStatus.PENDING, hospital_name=h.name,
                                   city=h.city, latitude=h.latitude, longitude=h.longitude,
                                   note="[EVALUATION] rolled back")
                db.add(req)
                db.flush()
                subject = svc.load_subject(db, ShortlistSubject.BLOOD_REQUEST, req.id)
                key = f"{city}|{g.value}"
                for mode in MODES:
                    t = time.perf_counter()
                    ev = svc.evaluate(db, subject, now=now, strategy=mode)
                    timings[mode.value].append((time.perf_counter() - t) * 1000)
                    viol[mode.value] += violations(db, subject, ev.candidates)
                    if mode is RS.UNIFIED_PRIORITY:
                        eligible_sizes.append(len(ev.candidates))
                        for c in ev.candidates:
                            coverage.append(c.priority.coverage)
                            unscored += 0 if c.priority.score_available else 1
                        # sensitivity: history weights off -> how often the
                        # top-k (k = contact batch) is the same set
                        k = min(settings.shortlist_max_batch,
                                a.units * settings.shortlist_contact_multiplier)
                        top = [c.donor_user_id for c in ev.candidates[:k]]
                        rescored = sorted(ev.candidates, key=lambda c: (
                            -unified_priority.compute(c.inputs, blind,
                                                      now=now).score, str(c.donor_user_id)))
                        sensitivity["requests"] += 1
                        sensitivity["same_top_k"] += int(
                            set(top) == {c.donor_user_id for c in rescored[:k]})
                    order = ev.candidates
                    for ls in range(1, a.latent_seeds + 1):
                        lorder = (latent_order(order, ls, subject, now, base_cfg)
                                  if mode is RS.UNIFIED_PRIORITY else order)
                        r = simulate_latent(lorder, a.units, ls, key)
                        rows.append({"request": key, "mode": mode.value, "model": "latent",
                                     "latent_seed": ls, "eligible": len(order), **r})
                    for model in ("persona", "independent"):
                        r = simulate(order, emails, a.units, model,
                                     zlib.crc32(f"{a.seed}|{key}|{model}".encode()), a.p_accept)
                        rows.append({"request": key, "mode": mode.value, "model": model,
                                     "eligible": len(order), **r})
    finally:
        db.rollback()
        db.close()
    result = {
        "generated_at": now.isoformat(), "dataset": "SYNTHETIC (seed 1337 network + seed "
        "20261031 shortlist donors)", "eligibility_policy": active_policy().code,
        "scoring_model": base_cfg.model_tag(), "units_per_request": a.units,
        "requests": len({r["request"] for r in rows}),
        "eligible_candidates_per_request": {
            "median": statistics.median(eligible_sizes) if eligible_sizes else 0,
            "min": min(eligible_sizes, default=0), "max": max(eligible_sizes, default=0)},
        "eligibility_violations": viol,
        "unified_evidence_coverage": {
            "candidates": len(coverage),
            "median": statistics.median(coverage) if coverage else None,
            "below_min_coverage_unscored": unscored},
        "sensitivity_history_weights_zero_same_first_batch": sensitivity,
        "evaluate_ms_median": {m: round(statistics.median(v), 1) for m, v in timings.items() if v},
        "persona_outcomes": summarise([r for r in rows if r["model"] == "persona"]),
        "independent_outcomes": summarise([r for r in rows if r["model"] == "independent"]),
        "latent_outcomes_pooled": summarise([r for r in rows if r["model"] == "latent"]),
        "latent_outcomes_by_seed": {
            ls: summarise([r for r in rows if r["model"] == "latent" and r["latent_seed"] == ls])
            for ls in range(1, a.latent_seeds + 1)},
        "caveat": "Synthetic. Persona outcomes and the seeded response history come from the "
                  "same personas, so history-based ranking is favoured by construction (leakage). "
                  "The latent model draws history and outcome independently from one latent trait "
                  "per donor; it tests the assumption that past answering predicts future "
                  "answering, it does not show that real donors behave that way. Not evidence of "
                  "real-world performance.",
        "rows": rows,
    }
    with open(a.out, "w") as fh:
        json.dump(result, fh, indent=1, default=str)
    show = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(show, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
