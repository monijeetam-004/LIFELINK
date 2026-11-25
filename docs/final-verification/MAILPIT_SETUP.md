# Mailpit setup (development mail catcher)

```bash
docker compose --profile mailpit up -d mailpit     # SMTP :1025, UI/API :8025
# .env (no credentials: Mailpit accepts anything)
MAIL_HOST=mailpit            # or localhost when the API runs outside Docker
MAIL_PORT=1025
MAIL_STARTTLS=false
```

Integration test: `SMTP_TEST_HOST=localhost SMTP_TEST_PORT=1025
SMTP_TEST_API=http://localhost:8025/api/v1/messages pytest
tests/test_gap_infrastructure.py::test_smtp_delivery_reaches_a_real_mail_catcher`
— sends through the application's own SMTP code and polls the catcher's
API until the message (unique subject marker) arrives.

**Status in the sandbox: PARTIAL (stand-in).** The Mailpit image/binary was
unobtainable; the same test passed against MailDev 2.2.1 (npm), an
equivalent SMTP catcher with an HTTP API.
