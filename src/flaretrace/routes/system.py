"""Routes system operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations


from flask import current_app, jsonify, redirect, render_template, request

from flaretrace.database.connection import get_database


def health():
    """Check database availability for container readiness monitoring.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    get_database().execute("SELECT 1")
    return {"status": "ok"}


def index():
    """Redirect the public homepage to the administrator-configured destination.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    setting = (
        get_database()
        .execute("SELECT value FROM settings WHERE key='homepage_redirect'")
        .fetchone()
    )
    if setting and setting["value"]:
        return redirect(setting["value"], code=302)
    return current_app.response_class("", mimetype="text/html")


def service_worker():
    """Serve the no-op service worker that lets the admin workspace install as an app.

    Served at the origin root rather than under /static/ so its default scope
    covers /admin/, without needing a Service-Worker-Allowed response header.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    return current_app.send_static_file("sw.js")


def error_page(error):
    """Render a consistent error response without exposing internal exception details.

    Args:
        error: HTTP exception whose public status and description may be rendered.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if request.path.startswith("/api/"):
        return (jsonify(error=error.description), error.code)
    return (render_template("error.html", error=error), error.code)


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/healthz", "health", ["GET"]],
    ["/sw.js", "service_worker", ["GET"]],
    ["/", "index", ["GET"]],
]
