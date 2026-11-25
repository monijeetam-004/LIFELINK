from __future__ import annotations

from app.core.config import settings
from app.services.sms.base import SmsProvider, SmsProviderUnavailableError, SmsSendResult

__all__ = [
    "SmsProvider",
    "SmsProviderUnavailableError",
    "SmsSendResult",
    "get_sms_provider",
    "sms_provider_configured",
]


def sms_provider_configured() -> bool:
    return settings.sms_provider_configured


def get_sms_provider() -> SmsProvider:
    """Return the configured provider.

    Raises :class:`SmsProviderUnavailableError` when SMS isn't configured --
    callers (app/services/sms_service.py) must treat that as an honest,
    expected "can't send" outcome, exactly like an unconfigured Neo4j graph
    engine or unconfigured S3 storage elsewhere in this codebase.

    Only one provider exists today (MSG91). Swapping or adding another is
    entirely local to this function plus a new class under
    app/services/sms/ -- nothing else in the codebase talks to a provider
    directly.
    """
    if not sms_provider_configured():
        raise SmsProviderUnavailableError(
            "No SMS provider is configured (SMS_PROVIDER=msg91 needs MSG91_ENABLED / "
            "MSG91_AUTH_KEY / MSG91_SENDER_ID; SMS_PROVIDER=console is development-only)"
        )
    if settings.sms_provider.strip().lower() == "console":
        from app.services.sms.console import ConsoleSmsProvider

        return ConsoleSmsProvider()
    from app.services.sms.msg91 import MSG91Provider

    return MSG91Provider()
