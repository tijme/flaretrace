"""Routes links operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, url_for

from flaretrace.utilities import now, valid_destination, UTC
from flaretrace.database.connection import get_database
from flaretrace.visits.groups import list_groups
from flaretrace.security import admin_only
from flaretrace.links.repository import get_link, valid_slug, public_url
from flaretrace.visits.records import remove_visits


@admin_only
def dashboard():
    """Render research links with recent grouped-click activity.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    links = (
        get_database()
        .execute(
            "SELECT l.*, COUNT(v.id) AS visits, COALESCE(SUM(v.interacted),0) AS interactions\n            FROM links l LEFT JOIN visits v ON v.link_id=l.id GROUP BY l.id ORDER BY l.created_at DESC,l.rowid DESC"
        )
        .fetchall()
    )
    count = get_database().execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    return render_template(
        "dashboard.html", links=links, visit_count=count, current_time=now()
    )


@admin_only
def new_link():
    """Validate submitted link settings and create an available research link.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:100]
        destination = request.form.get("destination", "").strip()
        expiry = request.form.get("expiry", "7")
        if "template_html" in request.form:
            abort(400, "Select a saved template instead of posting HTML.")
        template_id = request.form.get("template_id", "").strip()
        selected = (
            get_database()
            .execute("SELECT * FROM template_library WHERE id=?", (template_id,))
            .fetchone()
            if template_id
            else None
        )
        if template_id and (not selected):
            abort(400, "Template not found.")
        template_html = selected["template_html"] if selected else ""
        if (
            not name
            or not valid_destination(destination)
            or expiry not in {"1", "7", "30", "never"}
        ):
            flash(
                "Enter a name and a valid HTTP(S) destination, or leave the destination empty.",
                "error",
            )
            return (render_template("new_link.html"), 400)
        expires = (
            None
            if expiry == "never"
            else (datetime.now(UTC) + timedelta(days=int(expiry))).isoformat(
                timespec="seconds"
            )
        )
        slug = secrets.token_urlsafe(9)
        public_slug = request.form.get("public_slug", "").strip() or slug
        if not valid_slug(public_slug):
            flash(
                "Link segment must be unique and contain 1–80 letters, digits, underscores or hyphens.",
                "error",
            )
            return (render_template("new_link.html"), 400)
        get_database().execute(
            "INSERT INTO links(id,name,destination,created_at,expires_at,template_html,public_slug,template_id) VALUES(?,?,?,?,?,?,?,?)",
            (
                slug,
                name,
                destination,
                now(),
                expires,
                template_html,
                public_slug,
                template_id or None,
            ),
        )
        get_database().commit()
        flash("Link created. Copy it below to share.", "success")
        return redirect(url_for("link_detail", slug=slug))
    return render_template("new_link.html")


@admin_only
def link_detail(slug):
    """Render one research link and its grouped-click history.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    link = get_link(slug)
    return render_template(
        "link_detail.html",
        link=link,
        groups=list_groups(get_database(), slug),
        public_url=public_url(slug),
        current_time=now(),
    )


@admin_only
def toggle_link(slug):
    """Switch link availability without deleting its collected evidence.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    link = get_link(slug)
    get_database().execute(
        "UPDATE links SET active=? WHERE id=?", (not link["active"], slug)
    )
    get_database().commit()
    return redirect(url_for("link_detail", slug=slug))


@admin_only
def delete_link(slug):
    """Delete a research link together with its visits and media.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    get_link(slug)
    remove_visits("link_id=?", (slug,), delete_slug=slug)
    flash("Link and its records deleted.", "success")
    return redirect(url_for("dashboard"))


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/admin", "dashboard", ["GET"]],
    ["/admin/links/new", "new_link", ["GET", "POST"]],
    ["/admin/links/<slug>", "link_detail", ["GET"]],
    ["/admin/links/<slug>/toggle", "toggle_link", ["POST"]],
    ["/admin/links/<slug>/delete", "delete_link", ["POST"]],
]
