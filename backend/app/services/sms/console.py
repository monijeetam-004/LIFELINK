"""Development-only SMS provider: writes the templated message to the log.

It exists so the whole notification -> SMS path (consent, preferences,
templates, audit rows, retries) can be exercised without an SMS account.
It never claims delivery: results are recorded as LOGGED_ONLY, and
``Settings.sms_provider_configured`` refuses it in production.
"""
from __future__ import annotations

import logging

from app.core.phone import mask_phone
from app.models.enums import SmsTemplateKey
from app.services.sms.base import SmsProvider, SmsSendResult

logger = logging.getLogger("lifelink.sms.console")


class ConsoleSmsProvider(SmsProvider):
    name = "CONSOLE"

    def send_sms(self, *, to_e164: str, template_key: SmsTemplateKey,
                 variables: dict[str, str]) -> SmsSendResult:
        logger.info("[console SMS -- NOT SENT] to=%s template=%s vars=%s",
                    mask_phone(to_e164), template_key.value, sorted(variables))
        return SmsSendResult(accepted=False, logged_only=True,
                             failure_detail="console provider: logged, not sent")
