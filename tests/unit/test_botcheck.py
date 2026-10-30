"""Exercise the shared bot/crawler/preview user-agent heuristic."""

import pytest

from flaretrace.visits.botcheck import looks_like_bot


@pytest.mark.parametrize(
    "agent",
    [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Slackbot-LinkExpanding 1.0",
        "facebookexternalhit/1.1",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HeadlessChrome/120.0.0.0",
        "python-requests crawler/1.0",
        "Twitterbot/1.0",
        "WhatsApp/2.23 Preview",
        "SPIDER-scan/1.0",
    ],
)
def test_known_automation_signatures_are_detected(agent):
    """Flag common bot, crawler, headless, and link-preview user agents.

    Args:
        agent: Known automated-client user-agent string expected to match.
    """
    assert looks_like_bot(agent) is True


@pytest.mark.parametrize(
    "agent",
    [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
        "",
        None,
    ],
)
def test_ordinary_and_absent_user_agents_are_not_flagged(agent):
    """Never flag a real mobile/desktop browser, an empty string, or a missing agent.

    Args:
        agent: Genuine browser user-agent string, empty string, or None.
    """
    assert looks_like_bot(agent) is False


def test_match_is_case_insensitive():
    """Match regardless of how the client capitalizes its user agent."""
    assert looks_like_bot("MOZILLA/5.0 GOOGLEBOT/2.1") is True
