"""Scripted end-to-end demonstration against a RUNNING LifeLink API
(2026-10-31). Every step goes through the public HTTP API, exactly as the
web app does; nothing is written to the database directly.

    DEMO_MODE=true must be set on the API (the response simulator is
    refused otherwise, and always refused in production).

    python -m scripts.demo_walkthrough --api http://localhost:8000 \
        --origin http://localhost:5173 [--only A,B,C,D,E,F,G] [--pause]

--pause stops before each step and prints the page to open in the browser
(signed in as the scenario's hospital), so a presenter can show the web app
updating while this script plays the synthetic donors' answers.

Prerequisites: scripts/seed_demo_data.py and scripts/seed_shortlist_demo.py
have been run against the same (development/demo) database.

Scenarios
  A  enough donors: 3 needed, 3 synthetic donors accept, coordinator confirms
  B  honest partial: 15 eligible, only 2 accept -> 1 still short, escalation
  C  nobody eligible -> NO_ELIGIBLE with the reasons, zero confirmed
  D  ranking is not confirmation: the #1-ranked donor never answers
  E  distance limit: a donor 80 km away is neither shortlisted nor alerted
  F  route failure on a reserved plan -> secured failover (or explicit
     RECOVERY_REQUIRED / unreserved state, never a false "secured")
  G  plan -> shipment hand-off keeps the reservation (no release window)

Synthetic acceptance demonstrates the software workflow only -- not real
donor availability or clinical efficacy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

PASSWORD = "LifeLink-Demo-2026!"  # noqa: S105 - documented demo-only password
HOSP = "shortlist.hospital.{k}@lifelink-demo.example.com"
SYN = "synthetic.lifelink.example"


PAUSE = False
FRONTEND = "http://localhost:5173"
TIMINGS_MS: list[float] = []


def pause(msg: str) -> None:
    print(f"  >>> {msg}")
    if PAUSE:
        input("      press Enter to continue ")


class Api:
    def __init__(self, base: str, origin: str, email: str):
        self.c = httpx.Client(base_url=base, headers={"Origin": origin}, timeout=60)
        r = self.c.post("/auth/login", json={"email": email, "password": PASSWORD})
        if r.status_code != 200:
            raise SystemExit(f"login failed for {email}: {r.status_code} {r.text[:200]}")
        self.email = email

    def get(self, path, **kw):
        t = time.perf_counter()
        r = self.c.get(path, **kw)
        if path.endswith("/shortlist"):
            TIMINGS_MS.append((time.perf_counter() - t) * 1000)
        r.raise_for_status()
        return r.json()

    def post(self, path, body=None, ok=(200, 201)):
        r = self.c.post(path, json=body if body is not None else {})
        if r.status_code not in ok:
            raise SystemExit(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()


def banner(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def show(s: dict) -> None:
    c = s["counts"]
    print(f"  state={s['state']}  summary: {s['summary']}")
    print(f"  eligible={c['eligible_candidates']} contacted={c['contacted']} "
          f"awaiting={c['awaiting_response']} accepted={c['accepted']} "
          f"confirmed={c['confirmed_for_coordination']} declined={c['declined']} "
          f"timed_out={c['timed_out']} still_needed={s['remaining_unmet']}")
    if s["excluded"]:
        print(f"  excluded: {s['excluded']}")
    for e in s["escalation"]:
        print(f"  escalation: {e}")
    for ct in s["contacts"]:
        print(f"    #{ct['rank_at_contact']:>2} {ct['donor_name'] or ct['donor_ref']:<34} "
              f"{ct['status']:<28}{' (simulated)' if ct['simulated'] else ''}")


def cancel_previous(h: Api) -> None:
    for r in h.get("/blood-requests"):
        if r["status"] in ("PENDING", "MATCHING", "PARTIALLY_FULFILLED") and r["requester_user_id"]:
            if (r.get("note") or "").startswith("[DEMO]"):
                h.post(f"/blood-requests/{r['id']}/cancel", {"reason": "demo re-run"})


def shortlist_scenario(api, origin, admin: Api, key: str, units: int, title: str) -> dict:
    banner(title)
    h = Api(api, origin, HOSP.format(k=key.lower()))
    cancel_previous(h)
    req = h.post("/blood-requests", {"blood_type": "A_POS", "units_needed": units,
                                     "urgency": "URGENT", "note": f"[DEMO] scenario {key}"})
    rid = req["id"]
    print(f"  request {rid}: {units} unit(s) A+ at {req.get('hospital_name')}")
    pause(f"browser: sign in as {h.email} and open {FRONTEND}/hospital/requests/{rid}")
    s = h.get(f"/blood-requests/{rid}/shortlist")
    print("  -- before contacting --")
    show(s)
    if s["state"] == "NO_ELIGIBLE":
        return s
    pause("next: contact the first ranked batch (same as the 'Contact next ranked donors' button)")
    s = h.get(f"/blood-requests/{rid}/shortlist")
    if s["counts"]["contacted"] == 0:  # the presenter may already have clicked the button
        s = h.post(f"/blood-requests/{rid}/shortlist/contact-next")
    print("  -- after contacting the first ranked batch --")
    show(s)
    pause("next: the synthetic donors answer (DEMO_MODE simulator; simulated, not real)")
    for _ in range(8):
        if s["counts"]["awaiting_response"] == 0:
            break
        out = admin.post(f"/demo/shortlist/BLOOD_REQUEST/{rid}/simulate", {"force_timeouts": True})
        print(f"  simulator: {out['simulated']}")
        s = out["shortlist"]
    pause("next: the hospital confirms each accepted donor after the eligibility re-check")
    for ct in s["contacts"]:
        if ct["status"] == "ACCEPTED":
            s = h.post(f"/blood-requests/{rid}/shortlist/contacts/{ct['id']}/confirm")
    print("  -- final --")
    show(s)
    return s


def scenario_e(api, origin) -> None:
    banner("E  Distance limit: the 80 km donor is neither shortlisted nor alerted")
    far = Api(api, origin, f"sim-accept-c01@{SYN}")
    h = Api(api, origin, HOSP.format(k="c"))
    req = h.post("/blood-requests", {"blood_type": "A_POS", "units_needed": 1,
                                     "urgency": "CRITICAL", "note": "[DEMO] scenario E"})
    s = h.get(f"/blood-requests/{req['id']}/shortlist")
    print(f"  excluded beyond the limit: {s['excluded'].get('beyond_distance_limit', 0)}")
    got = [n for n in far.get("/notifications")
           if (n.get("data") or {}).get("request_id") == req["id"]]
    print(f"  notifications the 80 km donor received for this request: {len(got)} (expected 0)")
    assert not got, "distance limit violated"


def scenario_fg(api, origin, admin: Api) -> None:
    banner("F  Route failure on a reserved plan  +  G  plan -> shipment hand-off")
    hosp = Api(api, origin, "demo.hospital@lifelink-demo.example.com")
    patient = Api(api, origin, "demo.patient@lifelink-demo.example.com")
    me = hosp.get("/hospitals/me")
    local = sum(i["available_units"] for i in hosp.get("/hospitals/me/inventory")
                if i["blood_type"] == "O_NEG"
                and i.get("component", "WHOLE_BLOOD") == "WHOLE_BLOOD")
    units = local + 2  # more than the destination holds, so other facilities must deliver
    print(f"  destination {me['name']} holds {local} O- unit(s); emergency needs {units}")
    em = patient.post("/emergencies", {
        "blood_type": "O_NEG", "units_needed": units, "target_facility_id": me["id"],
        "latitude": me["latitude"], "longitude": me["longitude"], "city": me["city"],
        "deadline": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
        "location_text": "[DEMO] failover + hand-off"})
    eid = em["id"]
    # Recorded routes from the destination to two verified facilities in the
    # same city (the base seed records routes between some facilities only).
    import math

    partners = [f for f in admin.get("/admin/facilities", params={
        "query": me["city"], "verified": True, "page_size": 100})["items"]
        if f["id"] != me["id"] and f["latitude"] is not None and f["total_units"] > 0][:2]
    for p in partners:
        km = 6371 * math.dist((math.radians(me["latitude"]), 0), (math.radians(p["latitude"]), 0))
        km = max(3.0, round(1.3 * max(km, 2.0), 1))
        r = admin.c.post("/admin/transport-routes", json={
            "origin_facility_id": me["id"], "destination_facility_id": p["id"],
            "distance_km": km, "transit_minutes": max(8, round(km / 40 * 60))})
        print(f"  route to {p['name']}: {r.status_code}"
              + (" (already recorded)" if r.status_code == 409 else ""))
    pause(f"browser: sign in as {hosp.email}, open {FRONTEND}/decision-transparency "
          f"and pick emergency {eid[:8]} ([DEMO] failover + hand-off)")
    hosp.post(f"/emergencies/{eid}/pathway", {})
    plans = hosp.get(f"/emergencies/{eid}/pathways")
    active = next(p for p in plans if p["status"] == "ACTIVE")
    print(f"  plan v{active['version']}: {active['feasibility']}, ETA {active['eta_minutes']} min, "
          f"{len([p for p in plans if p['status'] == 'BACKUP'])} backup(s)")
    delivery = [leg for leg in active["legs"] if leg["steps"]]
    if not delivery:
        print("  the destination holds enough stock itself; no inter-facility leg to disrupt")
        return
    r = hosp.post(f"/emergencies/{eid}/pathway/reserve")
    print(f"  reserved {r['reserved_units']} unit(s); reservation_status={r['reservation_status']}")
    route = delivery[0]["steps"][0]["route_id"]
    pause("next: an administrator disrupts a route on the reserved plan")
    d = admin.post(f"/admin/transport-routes/{route}/disrupt", {"reason": "[DEMO] road closed"})
    out = next(o for o in d["affected_emergencies"] if o["emergency_id"] == eid)
    plans = hosp.get(f"/emergencies/{eid}/pathways")
    active = next(p for p in plans if p["status"] == "ACTIVE")
    print(f"  route {route[:8]} disrupted -> {out['kind']}; now v{active['version']} "
          f"reservation_status={active['reservation_status']} reserved={active['reserved_units']}"
          + (f" reason={active['recovery_required_reason']}"
             if active["reservation_status"] == "RECOVERY_REQUIRED" else ""))
    admin.post(f"/admin/transport-routes/{route}/recover", {"reason": "[DEMO] reopened"})
    if active["reservation_status"] != "RESERVED":
        print("  plan is not secured -> hand-off correctly refused:",
              hosp.c.post(f"/emergencies/{eid}/pathway/handoff").status_code)
        return
    pause("next: hand the reserved plan to shipments (same as the 'Hand off to shipment' button)")
    ho = hosp.post(f"/emergencies/{eid}/pathway/handoff")
    print(f"  hand-off -> reservation_status={ho['reservation_status']}; shipments: "
          + ", ".join(f"{s['shipment_code']} ({s['units']} units, {s['status']})"
                      for s in ho["shipments"]))
    again = hosp.post(f"/emergencies/{eid}/pathway/handoff")
    print(f"  repeated hand-off is idempotent: same shipments = "
          f"{[s['id'] for s in again['shipments']] == [s['id'] for s in ho['shipments']]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--origin", default="http://localhost:5173")
    ap.add_argument("--only", default="A,B,C,D,E,F,G")
    ap.add_argument("--json", default=None, help="write the final shortlists here")
    ap.add_argument("--frontend", default=None, help="web app URL printed with --pause")
    ap.add_argument("--pause", action="store_true", help="stop before each step")
    a = ap.parse_args(argv)
    global PAUSE, FRONTEND
    PAUSE = a.pause
    FRONTEND = a.frontend or a.origin
    only = set(a.only.upper().split(","))
    admin = Api(a.api, a.origin, "demo.admin@lifelink-demo.example.com")
    results = {}
    t0 = time.time()
    if "A" in only:
        results["A"] = shortlist_scenario(a.api, a.origin, admin, "A", 3,
                                          "A  Sufficient confirmed donors (3 needed)")
    if "B" in only:
        results["B"] = shortlist_scenario(a.api, a.origin, admin, "B", 3,
                                          "B  Many eligible, only two accept")
    if "C" in only:
        results["C"] = shortlist_scenario(a.api, a.origin, admin, "C", 1,
                                          "C  No eligible donors")
    if "D" in only:
        results["D"] = shortlist_scenario(a.api, a.origin, admin, "D", 1,
                                          "D  The top-ranked donor does not answer")
    if "E" in only:
        scenario_e(a.api, a.origin)
    if "F" in only or "G" in only:
        scenario_fg(a.api, a.origin, admin)
    print(f"\nDone in {time.time() - t0:.1f} s. Synthetic answers demonstrate the workflow, "
          "not real donor availability.")
    if TIMINGS_MS:
        t = sorted(TIMINGS_MS)
        results["_shortlist_get_ms"] = {"n": len(t), "median": t[len(t) // 2], "max": t[-1]}
        print(f"GET .../shortlist latency over {len(t)} calls: median {t[len(t) // 2]:.0f} ms, "
              f"max {t[-1]:.0f} ms (end to end through HTTP on this machine; not a benchmark)")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(results, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
