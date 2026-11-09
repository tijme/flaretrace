"""Classify a request's user agent as a likely bot, crawler, or link preview."""

from __future__ import annotations

import re

#: Signature shared by the persisted classification and the visit-detail display hint.
BOT_PATTERN = re.compile(
    r"bot\b|crawler|spider|preview|facebookexternalhit|headless", re.I
)


def looks_like_bot(user_agent):
    """Check whether a user agent matches known bot, crawler, or preview signatures.

    Args:
        user_agent: Raw request or browser-reported User-Agent string; absent values
            and non-matching text are both handled without raising.

    Returns:
        True when the heuristic signature matches; this never proves or disproves
        automation, since both the string and its absence can be spoofed.
    """
    return bool(BOT_PATTERN.search(user_agent or ""))
