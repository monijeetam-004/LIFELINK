"""Provider-agnostic SMS abstraction.

``app/services/sms_service.py`` (the orchestration layer -- idempotency,
retries, audit trail) talks only to this interface, never to MSG91 directly.
That's the whole point of the abstraction: swapping providers, or adding a
second one, means writing one new class here and pointing
``get_sms_provider()`` at it -- nothing else in the codebase changes.

    NotificationService
           |
           +---- EmailProvider (app/services/email_service.py, pre-existing)
           |
           +---- SMSProvider (this module)
                      |
                      +---- MSG91Provider (app/services/sms/msg91.py)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.enums import SmsFailureCategory, SmsTemplateKey


@dataclass(frozen=True)
class SmsSendResult:
    """The outcome of one send attempt against a real provider.

    Exactly one of (accepted=True) or (accepted=False, failure_category=...)
    holds. ``provider_message_id`` is the provider's own reference for a
    successfully-accepted send (useful for later delivery-status lookups /
    support tickets); providers that don't return one leave it ``None``.
    """

    accepted: bool
    provider_message_id: str | None = None
    failure_category: SmsFailureCategory | None = None
    failure_detail: str | None = None
    # True only for the development console provider: the message was
    # logged, not sent. Recorded as LOGGED_ONLY, never SENT.
    logged_only: bool = False


class SmsProviderUnavailableError(RuntimeError):
    """Raised by get_sms_provider() when no provider is configured.

    Mirrors GraphUnavailableError (app/core/graph.py) and the S3-vs-local
    storage_backend() pattern: an unconfigured SMS provider is a normal,
    expected state (a fresh dev environment, or a deployment that hasn't
    filed for DLT approval yet), not a bug -- callers must treat it as an
    honest "can't send" outcome, never fabricate a fake success.
    """


class SmsProvider(ABC):
    """One transactional-SMS provider."""

    #: Short provider name recorded on every SmsNotification audit row and
    #: shown (never with credentials) in admin health / logs.
    name: str

    @abstractmethod
    def send_sms(
        self,
        *,
        to_e164: str,
        template_key: SmsTemplateKey,
        variables: dict[str, str],
    ) -> SmsSendResult:
        """Send one templated SMS. Never raises for an ordinary delivery
        failure (timeout, 4xx/5xx, rate limit, bad recipient/template) --
        those come back as ``SmsSendResult(accepted=False, ...)`` so the
        caller can classify and decide whether to retry. May raise for a
        genuine programming error (e.g. an unknown template_key), which is
        not something a retry could ever fix.
        """
