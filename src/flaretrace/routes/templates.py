"""Routes templates operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import secrets

from flask import (
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from flaretrace.database.connection import get_database
from flaretrace.links.templates import (
    DEFAULT_TEMPLATE,
    validate_template,
    template_document,
)
from flaretrace.security import admin_only
from flaretrace.links.repository import get_link, get_public_link


@admin_only
def templates_page():
    """List saved landing templates and their current research-link usage counts.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    rows = (
        get_database()
        .execute(
            "SELECT t.*, (SELECT COUNT(*) FROM links l WHERE l.template_id=t.id) AS link_count FROM template_library t ORDER BY t.rowid DESC"
        )
        .fetchall()
    )
    return render_template("templates.html", templates=rows)


@admin_only
def edit_saved_template(template_id=None):
    """Render or update a saved template after validating its HTML.

    Args:
        template_id: Identifier of the reusable template in the template library.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.

    Raises:
        ValueError: A missing name is handled locally and rendered as an HTTP 400 form error.
    """
    row = (
        get_database()
        .execute("SELECT * FROM template_library WHERE id=?", (template_id,))
        .fetchone()
        if template_id
        else None
    )
    if template_id and (not row):
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:100]
        try:
            if not name:
                raise ValueError("Template name is required.")
            source = validate_template(request.form.get("template_html", ""))
        except ValueError as error:
            flash(str(error), "error")
            return (render_template("saved_template.html", link=row), 400)
        identifier = template_id or secrets.token_hex(8)
        get_database().execute(
            "INSERT INTO template_library VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,template_html=excluded.template_html",
            (identifier, name, source),
        )
        get_database().execute(
            "UPDATE links SET template_html=? WHERE template_id=?", (source, identifier)
        )
        get_database().commit()
        flash("Template saved. Links using it now use this version.", "success")
        return redirect(url_for("edit_saved_template", template_id=identifier))
    return render_template("saved_template.html", link=row)


@admin_only
def delete_saved_template(template_id):
    """Remove a saved template only when no research link still references it.

    Args:
        template_id: Identifier of the reusable template in the template library.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if (
        get_database()
        .execute("SELECT 1 FROM links WHERE template_id=?", (template_id,))
        .fetchone()
    ):
        abort(
            409,
            "This template is used by a link. Delete the link first, or keep the template.",
        )
    get_database().execute("DELETE FROM template_library WHERE id=?", (template_id,))
    get_database().commit()
    return redirect(url_for("templates_page"))


@admin_only
def preview_saved_template(template_id):
    """Render a saved template with sharing controls disabled.

    Args:
        template_id: Identifier of the reusable template in the template library.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    row = (
        get_database()
        .execute("SELECT * FROM template_library WHERE id=?", (template_id,))
        .fetchone()
    )
    if not row:
        abort(404)
    link = dict(row, destination="", public_slug=None)
    g.capture_page = True
    g.template_preview = True
    return render_template(
        "capture.html",
        link=link,
        capture_config=None,
        preview_only=True,
        template_source=validate_template(row["template_html"]) or DEFAULT_TEMPLATE,
        content_url=url_for("saved_template_content", template_id=template_id),
    )


@admin_only
def saved_template_content(template_id):
    """Serve authored template HTML in an isolated administrator preview.

    Args:
        template_id: Identifier of the reusable template in the template library.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    row = (
        get_database()
        .execute("SELECT * FROM template_library WHERE id=?", (template_id,))
        .fetchone()
    )
    if not row:
        abort(404)
    g.link_content = True
    g.template_preview = True
    return current_app.response_class(
        template_document(row["template_html"]), mimetype="text/html"
    )


@admin_only
def preview_link_template(slug):
    """Render a link’s captured template snapshot in preview mode.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    link = get_link(slug)
    g.capture_page = True
    g.template_preview = True
    return render_template(
        "capture.html",
        link=link,
        capture_config=None,
        preview_only=True,
        template_source=validate_template(link["template_html"]) or DEFAULT_TEMPLATE,
    )


def link_content(slug):
    """Serve the original public template HTML without adding capture behavior.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    link = get_public_link(slug)
    g.link_content = True
    return current_app.response_class(
        template_document(link["template_html"]),
        mimetype="text/html",
    )


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/admin/templates", "templates_page", ["GET"]],
    ["/admin/templates/new", "edit_saved_template", ["GET", "POST"]],
    ["/admin/templates/<template_id>", "edit_saved_template", ["GET", "POST"]],
    ["/admin/templates/<template_id>/delete", "delete_saved_template", ["POST"]],
    ["/admin/templates/<template_id>/preview", "preview_saved_template", ["GET"]],
    ["/admin/templates/<template_id>/content", "saved_template_content", ["GET"]],
    ["/admin/links/<slug>/template/preview", "preview_link_template", ["GET"]],
    ["/l/<slug>/content", "link_content", ["GET"]],
    ["/admin/links/<slug>/template/content", "preview_link_content", ["GET"]],
]


@admin_only
def preview_link_content(slug):
    """Serve a link’s content for authenticated administrator preview.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    link = get_link(slug)
    g.link_content = True
    g.template_preview = True
    return current_app.response_class(
        template_document(link["template_html"]),
        mimetype="text/html",
    )
