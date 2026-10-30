"""Group links into campaigns for aggregate reporting, independent of click grouping."""

from __future__ import annotations

import secrets

from flask import abort

from flaretrace.utilities import now


def valid_campaign_name(name):
    """Check a campaign name is present and within the stored length limit.

    Args:
        name: Submitted campaign name.

    Returns:
        True when the stripped name is 1-100 characters long.
    """
    return bool(name.strip()) and len(name.strip()) <= 100


def list_campaigns(connection):
    """Read every campaign with its aggregate link and visit counts.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.

    Returns:
        Campaign rows ordered newest-first, each with link_count and visit_count.
    """
    return connection.execute(
        """
        SELECT c.*,
          (SELECT COUNT(*) FROM links l WHERE l.campaign_id=c.id) AS link_count,
          (SELECT COUNT(*) FROM visits v JOIN links l ON l.id=v.link_id
             WHERE l.campaign_id=c.id) AS visit_count
        FROM campaigns c ORDER BY c.created_at DESC,c.rowid DESC
        """
    ).fetchall()


def get_campaign(connection, campaign_id):
    """Load one campaign or reject an unknown identifier.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
        campaign_id: Stored campaign identifier.

    Returns:
        Matching campaign row; unknown identifiers abort with HTTP 404.
    """
    campaign = connection.execute(
        "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
    ).fetchone()
    if not campaign:
        abort(404, "This campaign does not exist.")
    return campaign


def create_campaign(connection, name):
    """Insert a new campaign and return its generated identifier.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
        name: Validated, non-blank campaign name.

    Returns:
        Newly generated campaign identifier.
    """
    campaign_id = secrets.token_hex(8)
    connection.execute(
        "INSERT INTO campaigns(id,name,created_at) VALUES(?,?,?)",
        (campaign_id, name.strip()[:100], now()),
    )
    return campaign_id


def delete_campaign(connection, campaign_id):
    """Unassign every link, then delete the campaign itself.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
        campaign_id: Stored campaign identifier.
    """
    connection.execute(
        "UPDATE links SET campaign_id=NULL WHERE campaign_id=?", (campaign_id,)
    )
    connection.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
