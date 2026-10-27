"""Routes visits operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import json

from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from flaretrace.database.connection import get_database
from flaretrace.visits.groups import list_groups, group_visits
from flaretrace.visits.presentation import present_visit
from flaretrace.state import media_directory
from flaretrace.limits import limited
from flaretrace.security import admin_only
from flaretrace.visits.records import remove_visits
from flaretrace.visits.enrichment import lookup_ip_information
from flaretrace.visits.exports import csv_export, json_export


@admin_only
def visits_page():
    """Render recent click groups across all research links.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    return render_template("visits.html", groups=list_groups(get_database()))


@admin_only
def visit_group(group_id):
    """Render a click-group page or an incremental pagination fragment.

    Args:
        group_id: Identifier of the bounded five-minute click group.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    group = (
        get_database()
        .execute(
            "SELECT g.*,l.name AS link_name,\n                            (SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id) AS visit_count\n                            FROM visit_groups g JOIN links l ON l.id=g.link_id WHERE g.id=?",
            (group_id,),
        )
        .fetchone()
    )
    if not group:
        abort(404)
    try:
        visits, has_more = group_visits(
            get_database(), group_id, request.args.get("before")
        )
    except ValueError:
        abort(400)
    template = (
        "_group_clicks.html"
        if request.headers.get("X-Group-Fragment") == "1"
        else "visit_group.html"
    )
    return render_template(
        template,
        visits=visits,
        group=group,
        has_more=has_more,
        before=request.args.get("before"),
    )


@admin_only
def visit_detail(visit_id):
    """Render one visit’s browser metadata, event history, and stored media.

    Args:
        visit_id: Stored visit identifier used to join events, groups, and media.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    visit = (
        get_database()
        .execute(
            "SELECT v.*, l.name AS link_name FROM visits v JOIN links l ON l.id=v.link_id WHERE v.id=?",
            (visit_id,),
        )
        .fetchone()
    )
    if not visit:
        abort(404)
    media = (
        get_database()
        .execute(
            "SELECT * FROM media WHERE visit_id=? ORDER BY created_at DESC,rowid DESC",
            (visit_id,),
        )
        .fetchall()
    )
    events = (
        get_database()
        .execute("SELECT * FROM events WHERE visit_id=? ORDER BY id DESC", (visit_id,))
        .fetchall()
    )
    details = {
        key: json.loads(visit[key]) if visit[key] else None
        for key in ("headers", "browser", "location", "campaign", "ip_info")
    }
    proxy_headers = {
        field: value
        for field, value in details["headers"].items()
        if field.lower()
        in {
            "forwarded",
            "x-forwarded-for",
            "x-real-ip",
            "cf-connecting-ip",
            "true-client-ip",
            "via",
            "x-forwarded-proto",
            "x-forwarded-host",
        }
    }
    group = (
        get_database()
        .execute(
            "SELECT g.*,(SELECT COUNT(*) FROM visits v WHERE v.group_id=g.id) AS visit_count\n                               FROM visit_groups g WHERE g.id=?",
            (visit["group_id"],),
        )
        .fetchone()
    )
    overview = present_visit(visit, details, events, media)
    return render_template(
        "visit_detail.html",
        visit=visit,
        media=media,
        events=events,
        details=details,
        proxy_headers=proxy_headers,
        overview=overview,
        group=group,
    )


@admin_only
def enrich_visit(visit_id):
    """Save an administrator-requested, optional approximate IP lookup.

    Args:
        visit_id: Stored visit whose public IP may be sent to ipwho.is.

    Returns:
        Redirect to the inspector with a success or failure flash message.
    """
    visit = (
        get_database()
        .execute("SELECT * FROM visits WHERE id=?", (visit_id,))
        .fetchone()
    )
    if not visit:
        abort(404)
    if visit["ip_info"]:
        return redirect(url_for("visit_detail", visit_id=visit_id))
    limited("ip-enrichment", 10)
    try:
        information = lookup_ip_information(get_database(), visit["ip"])
        get_database().execute(
            "UPDATE visits SET ip_info=? WHERE id=?", (information, visit_id)
        )
        get_database().commit()
        flash("IP enrichment saved.", "success")
    except Exception:
        flash(
            "IP lookup unavailable, rate limited, or not a public address. No visit data was changed.",
            "error",
        )
    return redirect(url_for("visit_detail", visit_id=visit_id))


@admin_only
def delete_visit(visit_id):
    """Remove one visit and its evidence while maintaining group counts.

    Args:
        visit_id: Stored visit identifier used to join events, groups, and media.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    remove_visits("id=?", (visit_id,))
    flash("Visit and its media deleted.", "success")
    return redirect(url_for("visits_page"))


@admin_only
def media_file(media_id):
    """Serve a stored evidence file only to an authenticated administrator.

    Args:
        media_id: Stored media identifier; the filename is resolved from the database.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    item = (
        get_database().execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    )
    if not item or not (media_directory() / item["filename"]).is_file():
        abort(404)
    return send_file(
        media_directory() / item["filename"],
        mimetype=item["mime"],
        conditional=True,
        download_name=item["filename"],
        as_attachment=request.args.get("download") == "1",
    )


@admin_only
def export():
    """Download every matching visit as a protected CSV or JSON attachment.

    Returns:
        CSV response when format=csv; otherwise JSON with events and media metadata.
        An optional link query parameter filters records without grouping them.
    """
    link_id = request.args.get("link")
    visits = (
        get_database()
        .execute(
            "SELECT * FROM visits"
            + (" WHERE link_id=?" if link_id else "")
            + " ORDER BY created_at DESC,rowid DESC",
            (link_id,) if link_id else (),
        )
        .fetchall()
    )
    if request.args.get("format") == "csv":
        return current_app.response_class(
            csv_export(visits),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=flaretrace-visits.csv"
            },
        )
    response = jsonify(json_export(get_database(), visits))
    response.headers["Content-Disposition"] = (
        "attachment; filename=flaretrace-visits.json"
    )
    return response


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/admin/visits", "visits_page", ["GET"]],
    ["/admin/visit-groups/<group_id>", "visit_group", ["GET"]],
    ["/admin/visits/<visit_id>", "visit_detail", ["GET"]],
    ["/admin/visits/<visit_id>/enrich", "enrich_visit", ["POST"]],
    ["/admin/visits/<visit_id>/delete", "delete_visit", ["POST"]],
    ["/admin/media/<media_id>", "media_file", ["GET"]],
    ["/admin/export", "export", ["GET"]],
]
