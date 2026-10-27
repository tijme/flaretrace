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
        not link["active"] or (link["expires_at"] and link["expires_at"] <= now())
    ):
        abort(410, "This link has expired or has been disabled.")
    return link


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
