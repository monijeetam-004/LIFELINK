"""Phase 28 -- production environment/secret fail-closed config regression.

app/core/config.py's `_validate_secret_key` and `_validate_cookie_security`
model validators were added in Phase 0/7/23 and live-tested manually during
Phase 27's app/asgi.py work (confirmed to actually raise), but had no
permanent regression test of their own before this phase -- a future change
to Settings could silently break them and nothing would fail. This file
closes that gap: every fail-closed rule that guards a production boot is
exercised directly against the real `Settings` class (no mocking -- these
are real pydantic validators), both the failure and the passing case for
each, so a regression is caught the same day it's introduced.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_VALID_PROD_KWARGS = {
    "environment": "production",
    "secret_key": "a" * 32,
    "session_cookie_secure": True,
    "session_cookie_samesite": "lax",
    # Hardening: production now also requires a malware scanner.
    "document_scanner": "clamd",
}
_VALID_PROD_KWARGS_NO_ENV = {k: v for k, v in _VALID_PROD_KWARGS.items() if k != "environment"}


def test_production_refuses_the_shipped_default_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings(**{**_VALID_PROD_KWARGS, "secret_key": Settings.DEFAULT_SECRET_KEY})


def test_development_is_unaffected_by_the_default_secret_key() -> None:
    # The dev default is deliberately usable out of the box -- only a
    # production boot is refused.
    s = Settings(environment="development", secret_key=Settings.DEFAULT_SECRET_KEY)
    assert s.secret_key == Settings.DEFAULT_SECRET_KEY


def test_production_refuses_a_too_short_secret_key() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(**{**_VALID_PROD_KWARGS, "secret_key": "short-but-not-the-default"})


def test_production_accepts_a_sufficiently_long_unique_secret_key() -> None:
    s = Settings(**_VALID_PROD_KWARGS)
    assert s.is_production
    assert len(s.secret_key) >= Settings.MIN_PRODUCTION_SECRET_KEY_LENGTH


def test_production_refuses_an_insecure_session_cookie() -> None:
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE must be true"):
        Settings(**{**_VALID_PROD_KWARGS, "session_cookie_secure": False})


def test_development_may_run_without_a_secure_cookie() -> None:
    s = Settings(environment="development", session_cookie_secure=False)
    assert s.session_cookie_secure is False


def test_samesite_none_requires_secure_regardless_of_environment() -> None:
    with pytest.raises(ValidationError, match="requires SESSION_COOKIE_SECURE"):
        Settings(
            environment="development",
            session_cookie_samesite="none",
            session_cookie_secure=False,
        )
    # The safe combination is accepted.
    s = Settings(
        environment="development", session_cookie_samesite="none", session_cookie_secure=True,
    )
    assert s.session_cookie_samesite == "none"


def test_samesite_must_be_a_recognized_value() -> None:
    with pytest.raises(ValidationError, match="must be one of lax/strict/none"):
        Settings(environment="development", session_cookie_samesite="bogus")


def test_max_accounts_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="MAX_ACCOUNTS must be a positive integer"):
        Settings(max_accounts=0)


def test_cors_origin_list_parses_comma_separated_origins_and_trims_whitespace() -> None:
    s = Settings(cors_origins="https://app.example.com, https://admin.example.com")
    assert s.cors_origin_list == ["https://app.example.com", "https://admin.example.com"]


def test_is_production_recognizes_both_spellings() -> None:
    assert Settings(environment="production", **_VALID_PROD_KWARGS_NO_ENV).is_production
    assert Settings(environment="prod", **_VALID_PROD_KWARGS_NO_ENV).is_production
    assert not Settings(environment="staging").is_production
