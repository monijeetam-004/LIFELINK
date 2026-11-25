"""Donor donation-interval eligibility -- a configurable, cited policy.

A whole-blood donor must wait a fixed interval after their last donation
before donating again. The interval is jurisdiction-specific, so it comes
from ``DONOR_ELIGIBILITY_POLICY`` (audit finding D3, 2026-10-31):

``IN_DCR_2020`` (default; the project's data and motivation are Indian)
    Drugs and Cosmetics (Second Amendment) Rules, 2020, notified as
    G.S.R. 166(E), Gazette of India, 11 March 2020; Schedule F, Part XII-B,
    sub-heading H "Criteria for Blood Donation", clause 4 (Donation
    Interval): "For whole blood donation, once in three months (90 days) for
    males and four months (120 days) for females."
    LifeLink does not collect donor sex, and this module does not infer it
    from any other attribute. It therefore applies the **stricter 120-day
    interval to every donor**. That never marks an ineligible donor
    eligible; it can keep an eligible male donor out for up to 30 extra
    days. Flagged for clinical review.

``US_21CFR630``
    United States, 21 CFR 630.15(a)(1)(i): Whole Blood or a single unit of
    Red Blood Cells "no more than once in 8 weeks" (56 days). This is the
    interval LifeLink applied before 2026-10-31, without a citation.

This software check is a **pre-screen only**. It does not replace the
blood centre's donor questionnaire, haemoglobin and weight checks, deferral
registers, TTI testing, crossmatch or the responsible physician's decision.
Temporary/permanent deferrals other than the donation interval are not
modelled (DonorProfile has no structured deferral field; the free-text
medical-conditions field is never parsed into an eligibility decision).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class EligibilityPolicy:
    code: str
    interval_days: int
    jurisdiction: str
    citation: str
    note: str
    clinical_review_required: bool = True


POLICIES: dict[str, EligibilityPolicy] = {
    "IN_DCR_2020": EligibilityPolicy(
        code="IN_DCR_2020",
        interval_days=120,
        jurisdiction="India",
        citation=(
            "Drugs and Cosmetics (Second Amendment) Rules, 2020, G.S.R. 166(E), "
            "11 March 2020, Schedule F Part XII-B, H(4): whole blood once in 90 days "
            "(males) / 120 days (females)"
        ),
        note=(
            "Donor sex is not recorded, so the stricter 120-day interval is applied to "
            "every donor."
        ),
    ),
    "US_21CFR630": EligibilityPolicy(
        code="US_21CFR630",
        interval_days=56,
        jurisdiction="United States",
        citation="21 CFR 630.15(a)(1)(i): no more than once in 8 weeks",
        note="The interval LifeLink used before 2026-10-31.",
    ),
}


def active_policy() -> EligibilityPolicy:
    from app.core.config import settings

    return POLICIES[settings.donor_eligibility_policy]


def donation_interval_days() -> int:
    return active_policy().interval_days


def __getattr__(name: str):  # pragma: no cover - compatibility shim
    # Older code and tests import DONATION_INTERVAL_DAYS; it now follows the
    # configured policy instead of a hard-coded 56.
    if name == "DONATION_INTERVAL_DAYS":
        return donation_interval_days()
    raise AttributeError(name)


def eligible_on(last_donation_date: date | None) -> date | None:
    """The first date the donor may donate again, or ``None`` if never donated."""
    if last_donation_date is None:
        return None
    return last_donation_date + timedelta(days=donation_interval_days())


def is_eligible(last_donation_date: date | None, *, today: date | None = None) -> bool:
    """True when the donation interval has elapsed (or there is no donation on record)."""
    due = eligible_on(last_donation_date)
    if due is None:
        return True
    return (today or date.today()) >= due
