"""Bounded five-minute click groups; every underlying visit remains intact."""

from datetime import datetime, timedelta
import ipaddress

#: Maximum interval from a group’s first click to another matching click.
GROUP_SECONDS = 300
#: Number of individual clicks returned by one group-history page.
PAGE_SIZE = 50


def normalized_ip(value):
    """Normalize IPv4-mapped IPv6 addresses without rejecting unknown input.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        Canonical IP text, or the original input when it is not a valid address.
    """
    try:
        address = ipaddress.ip_address(value)
        return str(getattr(address, "ipv4_mapped", None) or address)
    except ValueError:
        return value


def assign_group(connection, visit_id, link_id, ip, created_at):
    """Caller holds a write transaction, including the visit insert.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
        visit_id: Stored visit identifier used to join events, groups, and media.
        link_id: Research-link identifier, or None when querying all links.
        ip: Recorded visitor IP address used to match bounded click groups.
        created_at: Visit creation timestamp in ISO 8601 UTC format.

    Returns:
        Identifier of the existing or newly created click group.
    """
    address = normalized_ip(ip)
    instant = datetime.fromisoformat(created_at)
    earliest = (instant - timedelta(seconds=GROUP_SECONDS)).isoformat(
        timespec="seconds"
    )
    group = (
        connection.execute(
            """SELECT id FROM visit_groups WHERE link_id=? AND ip=?
           AND started_at>=? AND started_at<=?
           ORDER BY started_at DESC,rowid DESC LIMIT 1""",
            (link_id, address, earliest, created_at),
        ).fetchone()
        if address
        else None
    )
    identifier = group["id"] if group else visit_id
    if group:
        connection.execute(
            "UPDATE visit_groups SET last_at=MAX(last_at,?) WHERE id=?",
            (created_at, identifier),
        )
    else:
        connection.execute(
            "INSERT INTO visit_groups(id,link_id,ip,started_at,last_at) VALUES(?,?,?,?,?)",
            (identifier, link_id, address, created_at, created_at),
        )
    connection.execute(
        "UPDATE visits SET group_id=? WHERE id=?", (identifier, visit_id)
    )
    return identifier


def migrate_groups(connection):
    """One-time, resumable migration. A partial index makes later starts cheap.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
    """
    while True:
        if not connection.execute(
            "SELECT 1 FROM visits WHERE group_id IS NULL LIMIT 1"
        ).fetchone():
            return
        # Every batch commits complete groups/membership so restart is safe.
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                """SELECT id,link_id,ip,created_at FROM visits WHERE group_id IS NULL
                   ORDER BY created_at,rowid LIMIT 1000"""
            ).fetchall()
            for row in rows:
                assign_group(
                    connection, row["id"], row["link_id"], row["ip"], row["created_at"]
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def list_groups(connection, link_id=None, limit=250, hide_bots=False):
    """Read recent click groups and their evidence counts for the administrator.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
        link_id: Research-link identifier, or None when querying all links.
        limit: Maximum number of records or requests accepted for this operation.
        hide_bots: Whether to exclude groups whose every click was flagged as automated.

    Returns:
        Recent SQLite rows with link names and aggregate evidence counts, including
        each group's bot_count alongside its other evidence counts.
    """
    conditions, parameters = (["g.link_id=?"], [link_id]) if link_id else ([], [])
    if hide_bots:
        conditions.append(
            "EXISTS (SELECT 1 FROM visits v WHERE v.group_id=g.id AND v.is_bot=0)"
        )
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    parameters.append(limit)
    return connection.execute(
        f"""
        WITH recent AS (
            SELECT g.*,g.rowid AS sequence FROM visit_groups g {where}
            ORDER BY g.last_at DESC,g.rowid DESC LIMIT ?
        )
        SELECT g.*,l.name AS link_name,
          (SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id) AS visit_count,
          (SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id AND v.interacted=1) AS sharing_count,
          (SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id AND v.finished=1) AS closed_count,
          (SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id AND v.location IS NOT NULL) AS location_count,
          (SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id AND v.is_bot=1) AS bot_count,
          (SELECT COUNT(*) FROM media m JOIN visits v ON v.id=m.visit_id WHERE v.group_id=g.id) AS media_count,
          (SELECT v.id FROM visits v WHERE v.group_id=g.id ORDER BY v.created_at DESC,v.rowid DESC LIMIT 1) AS latest_visit_id
        FROM recent g JOIN links l ON l.id=g.link_id
        ORDER BY g.last_at DESC,g.sequence DESC
    """,
        parameters,
    ).fetchall()


def group_visits(connection, group_id, before=None):
    """Read one cursor-paginated page of clicks belonging to a single group.

    Args:
        connection: Open SQLite connection with row access; the caller owns its lifetime.
        group_id: Identifier of the bounded five-minute click group.
        before: Optional visit identifier used as an exclusive pagination cursor.

    Returns:
        Pair of visit rows and a boolean indicating whether another page exists.

    Raises:
        ValueError: The supplied cursor is missing or belongs to a different click group.
    """
    parameters = [group_id]
    condition = ""
    if before:
        cursor = connection.execute(
            "SELECT created_at,rowid FROM visits WHERE id=? AND group_id=?",
            (before, group_id),
        ).fetchone()
        if cursor is None:
            raise ValueError("Click cursor does not belong to this group.")
        condition = "AND (v.created_at,v.rowid)<(?,?)"
        parameters.extend((cursor["created_at"], cursor["rowid"]))
    parameters.append(PAGE_SIZE + 1)
    rows = connection.execute(
        f"""
        SELECT v.*,(SELECT COUNT(*) FROM media m WHERE m.visit_id=v.id) AS media_count
        FROM visits v WHERE group_id=? {condition}
        ORDER BY v.created_at DESC,v.rowid DESC LIMIT ?
    """,
        parameters,
    ).fetchall()
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE
