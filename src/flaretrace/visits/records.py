"""Visits records operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import json

from flask import abort

from flaretrace.utilities import now
from flaretrace.database.connection import get_database
from flaretrace.state import media_directory


def remove_visits(where, args, delete_slug=None):
    """Delete matching visits transactionally, then remove their media files.

    Args:
        where: Internal SQL predicate; never accept this string directly from a visitor.
        args: Positional values bound to the SQL predicate or wrapped callable.
        delete_slug: Optional research-link identifier to delete in the same transaction.

    Returns:
        Number of deleted visit rows after the database transaction commits.
    """
    connection = get_database()
    connection.execute("BEGIN IMMEDIATE")
    try:
        affected_groups = connection.execute(
            f"SELECT DISTINCT group_id FROM visits WHERE {where}", args
        ).fetchall()
        files = connection.execute(
            f"SELECT filename FROM media WHERE visit_id IN (SELECT id FROM visits WHERE {where})",
            args,
        ).fetchall()
        cursor = connection.execute(f"DELETE FROM visits WHERE {where}", args)
        if delete_slug:
            connection.execute("DELETE FROM links WHERE id=?", (delete_slug,))
        else:
            for item in affected_groups:
                if not item["group_id"]:
                    continue
                latest = connection.execute(
                    "SELECT MAX(created_at) FROM visits WHERE group_id=?",
                    (item["group_id"],),
                ).fetchone()[0]
                if latest is None:
                    connection.execute(
                        "DELETE FROM visit_groups WHERE id=?", (item["group_id"],)
                    )
                else:
                    connection.execute(
                        "UPDATE visit_groups SET last_at=? WHERE id=?",
                        (latest, item["group_id"]),
                    )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    for item in files:
        (media_directory() / item["filename"]).unlink(missing_ok=True)
    return cursor.rowcount


def add_event(visit_id, kind, payload):
    """Append validated event JSON without exceeding the per-visit event limit.

    Args:
        visit_id: Stored visit identifier used to join events, groups, and media.
        kind: Validated event category recorded alongside the JSON payload.
        payload: Validated JSON-compatible event details.
    """
    connection = get_database()
    if (
        connection.execute(
            "SELECT COUNT(*) FROM events WHERE visit_id=?", (visit_id,)
        ).fetchone()[0]
        >= 80
    ):
        abort(429, "Session event limit reached.")
    connection.execute(
        "INSERT INTO events(visit_id,created_at,kind,payload) VALUES(?,?,?,?)",
        (visit_id, now(), kind, json.dumps(payload, allow_nan=False)),
    )
