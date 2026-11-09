"""Serialize complete visit records for CSV spreadsheets and JSON evidence exports."""

import csv
import io
import json

#: Stable spreadsheet column order, including raw stored location JSON.
CSV_COLUMNS = (
    "id",
    "link_id",
    "created_at",
    "ip",
    "user_agent",
    "is_bot",
    "referrer",
    "interacted",
    "finished",
    "location",
    "note",
)


def export_filter(link_id, campaign_id, hide_bots=False):
    """Build the optional WHERE clause restricting an export to one link, campaign, or real visits.

    Args:
        link_id: Optional single research-link identifier filter.
        campaign_id: Optional campaign identifier filter; ignored when link_id is set.
        hide_bots: Whether to exclude visits flagged as automated, matching the
            same filter already available on the Visits and link-detail pages.

    Returns:
        SQL WHERE-clause suffix (possibly empty) and its bound parameters tuple.
    """
    conditions = []
    parameters = []
    if link_id:
        conditions.append("link_id=?")
        parameters.append(link_id)
    elif campaign_id:
        conditions.append("link_id IN (SELECT id FROM links WHERE campaign_id=?)")
        parameters.append(campaign_id)
    if hide_bots:
        conditions.append("is_bot=0")
    if not conditions:
        return "", ()
    return " WHERE " + " AND ".join(conditions), tuple(parameters)


def spreadsheet_safe(value):
    """Escape formula-leading CSV values so opening an export cannot execute formulas.

    Args:
        value: Stored field value; None is represented as an empty cell.

    Returns:
        Text prefixed with an apostrophe when spreadsheet formula parsing is possible.
    """
    text = str(value if value is not None else "")
    unsafe = text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(
        ("\t", "\r", "\n")
    )
    return "'" + text if unsafe else text


def csv_export(visits):
    """Serialize visit rows with stable columns and spreadsheet-injection protection.

    Args:
        visits: Iterable of SQLite rows or mappings containing CSV_COLUMNS.

    Returns:
        CSV text including its header row and every supplied visit.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    writer.writerows(
        [[spreadsheet_safe(visit[field]) for field in CSV_COLUMNS] for visit in visits]
    )
    return output.getvalue()


def json_export(connection, visits):
    """Include decoded fields, events, and media metadata for every supplied visit.

    Args:
        connection: SQLite connection used to read related event and media rows.
        visits: Iterable of visit mappings; no grouping or truncation is applied.

    Returns:
        List of JSON-compatible records; binary media remains privately downloadable.
    """
    records = []
    for visit in visits:
        record = dict(visit)
        for field in ("headers", "campaign", "browser", "location", "ip_info"):
            record[field] = json.loads(record[field]) if record[field] else None
        record["events"] = [
            {**dict(event), "payload": json.loads(event["payload"])}
            for event in connection.execute(
                "SELECT * FROM events WHERE visit_id=? ORDER BY id", (visit["id"],)
            )
        ]
        record["media"] = [
            dict(media)
            for media in connection.execute(
                "SELECT id,kind,facing,mime,size,created_at FROM media WHERE visit_id=?",
                (visit["id"],),
            )
        ]
        records.append(record)
    return records
