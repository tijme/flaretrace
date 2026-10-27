"""URL validation must reject malformed destinations and redact referrer secrets."""

import pytest

from flaretrace.app import safe_referrer, valid_destination
from flaretrace.config import runtime_options


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            "https://[2001:db8::1]:8443/path?secret=1#fragment",
            "https://[2001:db8::1]:8443/path",
        ),
        ("https://example.com:bad/path", ""),
        ("javascript:alert(1)", ""),
        ("https:///missing-host", ""),
        ("http://example.com:8080/path", "http://example.com:8080/path"),
    ],
)
def test_referrer_normalization(value, expected):
    """Verify referrer normalization.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        expected: Expected normalized value or public response for this parametrized case.
    """
    assert safe_referrer(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "https:///missing",
        "https://example.com:bad",
        "https://example.com\\bad",
        "https://example.com/\nsecret",
        "https://user:password@example.com",
        "https://example.com/" + "x" * 2000,
    ],
)
def test_invalid_destinations(value):
    """Verify invalid destinations.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
    """
    assert not valid_destination(value)


@pytest.mark.parametrize(
    "settings",
    [
        {"DOMAIN": "éxample.com"},
        {"DOMAIN": "x" * 254},
        {"SITE_ADDRESS": "https://example.com:bad"},
        {"SITE_ADDRESS": "https://user:pass@example.com"},
        {"SITE_ADDRESS": "https://example.com/path"},
        {"SITE_ADDRESS": "ftp://example.com"},
    ],
)
def test_invalid_public_host_configuration(settings):
    """Verify invalid public host configuration.

    Args:
        settings: Overrides applied to the link, application, or isolated test configuration.
    """
    with pytest.raises(ValueError):
        runtime_options(settings)
