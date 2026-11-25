"""MSG91 transactional SMS provider (India-focused).

Uses MSG91's Flow API v5 (``POST /api/v5/flow/``), which sends a message
built from a pre-approved DLT template ("Flow") plus a set of named
variables -- this is the API MSG91 documents for DLT-compliant Indian
transactional SMS, as opposed to their older plain-text send endpoint,
which DLT rules make unusable for unregistered content in India.

Nothing in this module is reachable unless ``settings.msg91_enabled`` and
the auth key / flow id / sender id are all set (see
``Settings.sms_provider_configured``, app/core/config.py) -- constructing an
``MSG91Provider`` with missing configuration raises immediately rather than
deferring the failure to the first send attempt.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.models.enums import SmsFailureCategory, SmsTemplateKey
from app.services.sms.base import SmsProvider, SmsProviderUnavailableError, SmsSendResult
from app.services.sms.templates import flow_id_for, validate_variables

logger = logging.getLogger("lifelink.sms.msg91")

_API_URL = "https://control.msg91.com/api/v5/flow/"


class MSG91Provider(SmsProvider):
    name = "MSG91"

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        """`transport` is a testing-only seam (tests/test_sms.py uses
        `httpx.MockTransport` to fake MSG91's HTTP responses without ever
        making a real network call) -- production code never passes it, so
        `httpx.Client` uses its normal real transport."""
        if not settings.sms_provider_configured:
            raise SmsProviderUnavailableError(
                "MSG91 is not configured (MSG91_ENABLED / MSG91_AUTH_KEY / "
                "MSG91_FLOW_ID / MSG91_SENDER_ID)"
            )
        # Type checker: sms_provider_configured already guarantees these.
        assert settings.msg91_auth_key and settings.msg91_sender_id
        self._transport = transport

    def send_sms(
        self,
        *,
        to_e164: str,
        template_key: SmsTemplateKey,
        variables: dict[str, str],
    ) -> SmsSendResult:
        var_error = validate_variables(template_key, variables)
        if var_error:
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.INVALID_TEMPLATE,
                failure_detail=var_error,
            )

        flow_id = flow_id_for(template_key)
        if not flow_id:
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.INVALID_TEMPLATE,
                failure_detail=(
                    f"no MSG91 Flow ID configured for template {template_key.value} "
                    "(set MSG91_FLOW_ID or add it to MSG91_TEMPLATE_FLOW_MAP)"
                ),
            )

        # MSG91 expects a bare mobile number (country code, no leading '+').
        mobile = to_e164.lstrip("+")
        recipient: dict[str, str] = {"mobiles": mobile, **variables}
        payload = {
            "template_id": flow_id,
            "short_url": "0",
            "realTimeResponse": "1",
            "sender": settings.msg91_sender_id,
            "recipients": [recipient],
        }
        headers = {
            "authkey": settings.msg91_auth_key or "",
            "content-type": "application/json",
            "accept": "application/json",
        }

        try:
            with httpx.Client(
                timeout=settings.msg91_timeout_seconds, transport=self._transport
            ) as client:
                resp = client.post(_API_URL, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            logger.warning("MSG91 send timed out: %s", type(exc).__name__)
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.TIMEOUT,
                failure_detail="request to MSG91 timed out",
            )
        except httpx.HTTPError as exc:
            # Network-level failure (DNS, connection refused, TLS, ...) --
            # transient by nature, worth retrying like a timeout.
            logger.warning("MSG91 send failed: %s", type(exc).__name__)
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.TIMEOUT,
                failure_detail=f"network error contacting MSG91: {type(exc).__name__}",
            )

        return self._classify_response(resp)

    @staticmethod
    def _classify_response(resp: httpx.Response) -> SmsSendResult:
        try:
            body = resp.json()
        except ValueError:
            body = {}

        if resp.status_code == 401 or resp.status_code == 403:
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.AUTH_FAILURE,
                failure_detail="MSG91 rejected the auth key",
            )
        if resp.status_code == 429:
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.RATE_LIMITED,
                failure_detail="MSG91 rate-limited this request",
            )
        if resp.status_code >= 500:
            return SmsSendResult(
                accepted=False,
                failure_category=SmsFailureCategory.PROVIDER_5XX,
                failure_detail=f"MSG91 returned HTTP {resp.status_code}",
            )
        if resp.status_code >= 400:
            # 4xx that isn't auth/rate-limit -- typically an invalid mobile
            # number or an invalid/unapproved template on MSG91's side.
            message = str(body.get("message", "")).lower()
            if "mobile" in message or "recipient" in message or "number" in message:
                category = SmsFailureCategory.INVALID_RECIPIENT
            elif "template" in message or "flow" in message or "dlt" in message:
                category = SmsFailureCategory.INVALID_TEMPLATE
            else:
                category = SmsFailureCategory.INVALID_RECIPIENT
            detail = f"MSG91 returned HTTP {resp.status_code}: {body.get('message', '')}"
            return SmsSendResult(
                accepted=False,
                failure_category=category,
                failure_detail=detail[:300],
            )

        # 2xx: MSG91's Flow API still reports application-level failure via
        # `type` in the JSON body even on a 200, so that has to be checked too.
        if str(body.get("type", "")).lower() == "error":
            message = str(body.get("message", "unknown MSG91 error"))
            lowered = message.lower()
            if "mobile" in lowered or "number" in lowered:
                category = SmsFailureCategory.INVALID_RECIPIENT
            elif "template" in lowered or "flow" in lowered or "dlt" in lowered:
                category = SmsFailureCategory.INVALID_TEMPLATE
            else:
                category = SmsFailureCategory.UNKNOWN
            return SmsSendResult(
                accepted=False, failure_category=category, failure_detail=message[:300]
            )

        message_id = None
        raw_message = body.get("message")
        if isinstance(raw_message, str):
            message_id = raw_message
        elif isinstance(raw_message, list) and raw_message:
            first = raw_message[0]
            if isinstance(first, dict):
                message_id = str(first.get("request_id") or first.get("id") or "") or None
        return SmsSendResult(accepted=True, provider_message_id=message_id)
