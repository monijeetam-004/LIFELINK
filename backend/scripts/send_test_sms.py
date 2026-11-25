"""Send ONE controlled test SMS through the configured real provider (MSG91).

    python -m scripts.send_test_sms +919876543210

This is the SMS counterpart of scripts/send_test_email.py. It is meant to be run
by hand once, after a MSG91 account, a DLT-approved template and its Flow ID
exist. It uses exactly the provider code the application uses
(app.services.sms.get_sms_provider); there is no mock and no fallback.

* It refuses to run unless SMS_PROVIDER=msg91 and MSG91 is configured
  (MSG91_ENABLED=true, MSG91_AUTH_KEY, MSG91_SENDER_ID, plus MSG91_FLOW_ID or a
  MSG91_TEMPLATE_FLOW_MAP entry for EMERGENCY_CREATED). The console provider
  is refused, because it would only log the message.
* It sends the EMERGENCY_CREATED template with fixed, clearly-marked test
  variables ("LIFELINK-TEST"), to the one number given.
* It prints the provider's verdict and message id, and the number only in
  masked form. It never prints the auth key.

Exit codes: 0 if the provider accepted the message, 1 if it was rejected,
2 for configuration or usage errors. "Accepted" means MSG91 took the request;
handset delivery must be confirmed on the phone or in the MSG91 delivery report.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    from app.core.config import settings
    from app.core.phone import mask_phone, normalize_phone_e164
    from app.models.enums import SmsTemplateKey
    from app.services.sms import SmsProviderUnavailableError, get_sms_provider

    if settings.sms_provider.strip().lower() != "msg91":
        current = settings.sms_provider
        print(f"refusing: SMS_PROVIDER={current!r}; a real test needs SMS_PROVIDER=msg91")
        return 2
    try:
        provider = get_sms_provider()
    except SmsProviderUnavailableError as exc:
        print(f"refusing: {exc}")
        return 2
    try:
        to = normalize_phone_e164(argv[0])
    except ValueError as exc:
        print(f"refusing: not a valid phone number ({exc})")
        return 2
    if to is None:
        print("refusing: empty phone number")
        return 2
    print(f"provider={provider.name} to={mask_phone(to)} template=EMERGENCY_CREATED")
    result = provider.send_sms(
        to_e164=to,
        template_key=SmsTemplateKey.EMERGENCY_CREATED,
        variables={"blood_type": "LIFELINK-TEST", "location": "LIFELINK-TEST"},
    )
    if result.accepted:
        print(f"ACCEPTED by the provider; provider_message_id={result.provider_message_id}")
        print("Now confirm delivery on the handset or in the MSG91 delivery report.")
        return 0
    print(f"REJECTED: {result.failure_category} {result.failure_detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
