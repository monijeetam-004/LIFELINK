"""Export the synthetic LifeLink dataset to CSV files.

    DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/lifelink_dataset \
        python -m scripts.export_dataset OUT_DIR

Run it on a database freshly seeded with `python -m scripts.seed_demo_data`
(fixed random seed 1337). Every row is SYNTHETIC: no real person, hospital
or blood unit is described.

Columns that would never be shared from a real deployment are left out even
though the data are synthetic: password hashes, phone numbers, medical notes,
licence numbers and idempotency keys. User accounts are exported by UUID and
role only (no e-mail address), and facilities keep their synthetic names.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import settings

# file name -> SQL. Each query names its columns explicitly so that a later
# schema change can never leak a new (possibly sensitive) column into a CSV.
QUERIES: dict[str, str] = {
    "facilities.csv": """
        SELECT id AS facility_id, name, facility_type, city, latitude, longitude,
               verified
        FROM hospital_profiles ORDER BY city, name""",
    "transport_routes.csv": """
        SELECT r.id AS route_id,
               r.origin_facility_id, o.name AS origin_name, o.city AS origin_city,
               r.destination_facility_id, d.name AS destination_name,
               d.city AS destination_city,
               r.distance_km, r.transit_minutes, r.status, r.disrupted_reason
        FROM transport_routes r
        JOIN hospital_profiles o ON o.id = r.origin_facility_id
        JOIN hospital_profiles d ON d.id = r.destination_facility_id
        ORDER BY origin_city, origin_name, destination_name""",
    "donors.csv": """
        SELECT dp.user_id AS donor_id, dp.blood_type, dp.city,
               CASE WHEN dp.share_location THEN dp.latitude END AS latitude,
               CASE WHEN dp.share_location THEN dp.longitude END AS longitude,
               dp.share_location, dp.is_available, dp.availability_state,
               dp.last_donation_date, dp.identity_verified, dp.weight_kg,
               dp.alert_min_urgency, dp.alert_radius_km,
               dp.updated_at::date AS profile_updated_on
        FROM donor_profiles dp ORDER BY dp.city, dp.user_id""",
    "blood_units.csv": """
        SELECT u.id AS unit_id, u.unit_code, u.hospital_id AS facility_id,
               h.name AS facility_name, h.city, u.blood_type, u.component,
               u.state, u.provenance, u.received_at, u.expires_at,
               u.expiry_rule_ref
        FROM blood_units u JOIN hospital_profiles h ON h.id = u.hospital_id
        ORDER BY h.city, h.name, u.blood_type, u.component, u.expires_at""",
    "facility_inventory.csv": """
        SELECT i.hospital_id AS facility_id, h.name AS facility_name, h.city,
               i.blood_type, i.component, i.units, i.reserved_units
        FROM hospital_inventory i JOIN hospital_profiles h ON h.id = i.hospital_id
        ORDER BY h.city, h.name, i.blood_type, i.component""",
    "donations.csv": """
        SELECT id AS donation_id, donor_user_id AS donor_id, hospital_id AS facility_id,
               hospital_name AS facility_name, units, donated_on, verification_status
        FROM donations ORDER BY donated_on, id""",
    "users_by_role.csv": """
        SELECT role, count(*) AS accounts FROM users GROUP BY role ORDER BY role""",
}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        for name, sql in QUERIES.items():
            result = conn.execute(text(sql))
            rows = result.fetchall()
            with (out / name).open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(result.keys())
                writer.writerows(rows)
            print(f"{name}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
