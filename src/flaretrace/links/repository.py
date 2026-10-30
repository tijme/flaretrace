"""Links repository operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import re

from flask import abort, current_app, request, url_for

from flaretrace.utilities import now
from flaretrace.database.connection import get_database


def get_link(slug, available=False):
    """Load a link and optionally reject disabled or expired records.

    Args:
        slug: Public research-link identifier stored in the links table.
        available: Whether disabled and expired links must be rejected.

    Returns:
        Matching link row; unavailable records abort with an HTTP error.
    """
    link = get_database().execute("SELECT * FROM links WHERE id=?", (slug,)).fetchone()
    if not link:
        abort(404, "This link does not exist.")
    if available and (
        not link["active"]
        or (link["expires_at"] and link["expires_at"] <= now())
        or visit_limit_reached(link)
    ):
        abort(410, "This link has expired or has been disabled.")
    return link


def visit_limit_reached(link):
    """Check whether a link's optional visit cap has already been met by real visitors.

    Automated bot, crawler, and link-preview requests never count toward the cap,
    so a one-time link survives an email scanner or chat-app preview fetch.

    Args:
        link: Stored link row supplying the optional max_visits cap.

    Returns:
        True when max_visits is set and at least that many non-automated visits
        are already recorded.
    """
    if link["max_visits"] is None:
        return False
    count = get_database().execute(
        "SELECT COUNT(*) FROM visits WHERE link_id=? AND is_bot=0", (link["id"],)
    ).fetchone()[0]
    return count >= link["max_visits"]


def get_public_link(slug):
    """Load an available visitor link while hiding unknown identifiers.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Available link row; unknown identifiers abort with HTTP 404.
    """
    row = (
        get_database()
        .execute("SELECT id FROM links WHERE COALESCE(public_slug,id)=?", (slug,))
        .fetchone()
    )
    if not row:
        abort(404, "This link does not exist.")
    return get_link(row["id"], available=True)


def parse_max_visits(raw):
    """Parse an optional visit cap, rejecting anything but a positive integer.

    Args:
        raw: Submitted max_visits form value; blank means unlimited.

    Returns:
        Positive integer cap, or None for unlimited.

    Raises:
        ValueError: The value is non-blank but not an integer from 1 to 100000.
    """
    value = raw.strip()
    if not value:
        return None
    if not value.isdigit() or not 1 <= int(value) <= 100000:
        raise ValueError(
            "Visit limit must be blank, or a whole number from 1 to 100000."
        )
    return int(value)


def valid_slug(slug, own_id=None):
    """Check the link identifier format and uniqueness before saving.

    Args:
        slug: Public research-link identifier stored in the links table.
        own_id: Existing link identifier to exclude from the uniqueness check, if any.

    Returns:
        True when the identifier is permitted and is not already in use.
    """
    if not re.fullmatch("[A-Za-z0-9_-]{1,80}", slug):
        return False
    row = (
        get_database()
        .execute("SELECT id FROM links WHERE COALESCE(public_slug,id)=?", (slug,))
        .fetchone()
    )
    return not row or row["id"] == own_id


def public_url(slug):
    """Build a visitor link from the configured origin or current request.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Absolute URL for the public capture page.
    """
    link = get_link(slug)
    return (
        current_app.config["PUBLIC_BASE_URL"] or request.host_url.rstrip("/")
    ) + url_for("capture", slug=link["public_slug"] or link["id"])
