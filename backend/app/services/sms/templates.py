"""LifeLink's fixed SMS template registry.

LifeLink never sends arbitrary, free-text SMS -- every send goes through one
of these pre-defined templates, each with a fixed set of variables. This is
partly a safety property (no user-controlled SMS content) and partly a
regulatory one: Indian DLT rules require every SMS's exact wording to be
pre-registered and approved before it can be sent, so "arbitrary text" isn't
just discouraged here, it's not actually deliverable.

Each :class:`SmsTemplateSpec` carries the variables the template needs and a
reference copy of its approved wording. The reference copy is NOT what
actually gets sent -- the real, DLT-approved wording lives on MSG91's side,
addressed by the Flow ID that ``flow_id_for()`` resolves (see msg91.py and
LIFELINK_SMS_PROVIDER.md for how a template key maps to a Flow ID, and why
that mapping -- not this file -- is what a deploying operator must actually
configure). It exists here so ``sms_service`` can validate that the caller
supplied every variable the template needs *before* making a network call,
and so this file doubles as living documentation of what LifeLink can
possibly text someone.

Only categories with a real, already-existing backing event in this
codebase are implemented (see EVENT_TYPE_TEMPLATES below and
LIFELINK_SMS_PROVIDER.md for what was deliberately left out and why).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.config import settings
from app.models.enums import SmsTemplateKey


@dataclass(frozen=True)
class SmsTemplateSpec:
    key: SmsTemplateKey
    description: str
    required_vars: tuple[str, ...]
    # Reference wording only -- see module docstring. {var} placeholders
    # match `required_vars`. Kept short: Indian DLT transactional templates
    # are typically capped well under standard single-segment SMS length.
    reference_text: str


TEMPLATE_REGISTRY: dict[SmsTemplateKey, SmsTemplateSpec] = {
    SmsTemplateKey.EMERGENCY_CREATED: SmsTemplateSpec(
        key=SmsTemplateKey.EMERGENCY_CREATED,
        description=(
            "A new emergency SOS was broadcast and this recipient is a compatible "
            "donor or nearby hospital."
        ),
        required_vars=("blood_type", "location"),
        reference_text=(
            "LifeLink EMERGENCY: {blood_type} blood needed urgently near {location}. "
            "Open the app to respond."
        ),
    ),
    SmsTemplateKey.EMERGENCY_STATUS_CHANGED: SmsTemplateSpec(
        key=SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
        description=(
            "An emergency this recipient created or responded to changed status "
            "(acknowledged, matched, assisted, resolved, or cancelled)."
        ),
        required_vars=("status_label",),
        reference_text=(
            "LifeLink: your emergency update - {status_label}. Open the app for details."
        ),
    ),
    SmsTemplateKey.DONOR_MATCH_ALERT: SmsTemplateSpec(
        key=SmsTemplateKey.DONOR_MATCH_ALERT,
        description=(
            "A non-emergency blood request matches this donor's blood type "
            "and alert preferences."
        ),
        required_vars=("blood_type", "urgency"),
        reference_text=(
            "LifeLink: a patient needs {blood_type} blood ({urgency}). "
            "Open the app if you can help."
        ),
    ),
    SmsTemplateKey.HOSPITAL_REQUEST_UPDATE: SmsTemplateSpec(
        key=SmsTemplateKey.HOSPITAL_REQUEST_UPDATE,
        description=(
            "A blood request update relevant to a hospital "
            "(new incoming request in their area)."
        ),
        required_vars=("blood_type", "urgency"),
        reference_text=(
            "LifeLink: incoming blood request in your area - {blood_type}, {urgency}. "
            "Open the app."
        ),
    ),
    SmsTemplateKey.FAMILY_FILED_REQUEST: SmsTemplateSpec(
        key=SmsTemplateKey.FAMILY_FILED_REQUEST,
        description="A family member filed a blood request on this patient's behalf.",
        required_vars=("requester_name",),
        reference_text=(
            "LifeLink: {requester_name} filed a blood request on your behalf. "
            "Open the app for details."
        ),
    ),
    SmsTemplateKey.BLOOD_REQUEST_FULFILLED: SmsTemplateSpec(
        key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED,
        description=(
            "A blood request this donor helped with was marked fulfilled "
            "(their donation was recorded)."
        ),
        required_vars=(),
        reference_text=(
            "LifeLink: thank you. A request you helped with is fulfilled "
            "and your donation was recorded."
        ),
    ),
    SmsTemplateKey.BLOOD_REQUEST_CANCELLED: SmsTemplateSpec(
        key=SmsTemplateKey.BLOOD_REQUEST_CANCELLED,
        description="A blood request this donor had accepted was cancelled by the requester.",
        required_vars=(),
        reference_text="LifeLink: a blood request you accepted was cancelled by the requester.",
    ),
}

# In-app Notification.type -> SMS template key, for the event types that
# actually get an SMS. Every type string here is a real value already
# passed to notification_service.notify()/notify_matching_donors()
# elsewhere in the codebase (app/api/routes/blood_requests.py,
# app/api/routes/emergencies.py) -- this file does not invent new events,
# it only decides which of the real ones also go out over SMS.
#
# Deliberately NOT wired to SMS: self-echo confirmations the recipient is
# already looking at in-app when they happen (blood_request.you_accepted,
# emergency.you_responded), and every non-blood-donation event type
# (family.*, certificate.ready, appointment.*, verification.*,
# account.password_changed) -- real events, but out of scope for this SMS
# integration, which targets the emergency/blood-matching critical path
# the spec's example categories were about.
EVENT_TYPE_TEMPLATES: dict[str, SmsTemplateKey] = {
    "emergency.new": SmsTemplateKey.EMERGENCY_CREATED,
    "emergency.acknowledged": SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
    "emergency.responder_accepted": SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
    "emergency.responder_withdrew": SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
    "emergency.assistance_found": SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
    "emergency.resolved": SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
    "emergency.cancelled": SmsTemplateKey.EMERGENCY_STATUS_CHANGED,
    "blood_request.new": SmsTemplateKey.DONOR_MATCH_ALERT,
    "blood_request.incoming": SmsTemplateKey.HOSPITAL_REQUEST_UPDATE,
    "blood_request.filed_for_you": SmsTemplateKey.FAMILY_FILED_REQUEST,
    "blood_request.fulfilled": SmsTemplateKey.BLOOD_REQUEST_FULFILLED,
    "blood_request.cancelled": SmsTemplateKey.BLOOD_REQUEST_CANCELLED,
}


def template_for_event(event_type: str) -> SmsTemplateKey | None:
    return EVENT_TYPE_TEMPLATES.get(event_type)


def spec_for(template_key: SmsTemplateKey) -> SmsTemplateSpec:
    return TEMPLATE_REGISTRY[template_key]


def validate_variables(template_key: SmsTemplateKey, variables: dict[str, str]) -> str | None:
    """Return an error string if `variables` doesn't satisfy the template's
    required_vars, else None. Checked before ever calling the provider."""
    spec = TEMPLATE_REGISTRY[template_key]
    missing = [v for v in spec.required_vars if not variables.get(v)]
    if missing:
        return f"missing required template variable(s): {', '.join(missing)}"
    return None


def flow_id_for(template_key: SmsTemplateKey) -> str | None:
    """Resolve the MSG91 Flow ID to use for `template_key`.

    Real Indian DLT setups register one approved template (and therefore
    one Flow) per distinct wording, so a production deployment sending all
    seven of these templates needs MSG91_TEMPLATE_FLOW_MAP set with one
    entry per key. MSG91_FLOW_ID alone is a valid, simpler configuration
    for a deployment that only needs one of these templates live (e.g.
    while the rest are still pending DLT approval) -- every other template
    key just isn't sendable until either its own flow is configured or it's
    added to the map, and send_sms reports that honestly
    (INVALID_TEMPLATE) rather than sending the wrong wording under someone
    else's Flow ID.
    """
    overrides = _template_flow_overrides()
    return overrides.get(template_key.value) or settings.msg91_flow_id


def _template_flow_overrides() -> dict[str, str]:
    raw = settings.msg91_template_flow_map
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if v}
