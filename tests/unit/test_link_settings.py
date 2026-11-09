"""Exercise pure validators for optional link settings: visit caps and campaign names."""

import pytest

from flaretrace.links.campaigns import valid_campaign_name
from flaretrace.links.repository import parse_max_visits


@pytest.mark.parametrize("raw", ["", "  "])
def test_blank_visit_limit_means_unlimited(raw):
    """Treat an empty or whitespace-only visit limit as unlimited.

    Args:
        raw: Blank or whitespace-only submitted form value.
    """
    assert parse_max_visits(raw) is None


@pytest.mark.parametrize("raw, expected", [("1", 1), ("100000", 100000), (" 42 ", 42)])
def test_valid_visit_limits_parse_to_their_integer_value(raw, expected):
    """Parse a well-formed decimal visit limit, tolerating surrounding whitespace.

    Args:
        raw: Submitted visit-limit form value.
        expected: Parsed positive integer expected from that value.
    """
    assert parse_max_visits(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "100001", "1.5", "1e3", "999999999"])
def test_invalid_visit_limits_are_rejected(raw):
    """Reject zero, negative, non-integer, and out-of-range visit limits.

    Args:
        raw: Submitted visit-limit form value expected to be rejected.
    """
    with pytest.raises(ValueError):
        parse_max_visits(raw)


@pytest.mark.parametrize("name", ["Q4 awareness", "a", "x" * 100])
def test_valid_campaign_names_are_accepted(name):
    """Accept any non-blank campaign name up to the stored length limit.

    Args:
        name: Candidate campaign name expected to pass validation.
    """
    assert valid_campaign_name(name) is True


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_campaign_names_are_rejected(name):
    """Reject a blank, whitespace-only, or over-length campaign name.

    Args:
        name: Candidate campaign name expected to fail validation.
    """
    assert valid_campaign_name(name) is False
