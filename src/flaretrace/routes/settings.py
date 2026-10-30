"""Routes settings operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import secrets

from flask import current_app, flash, redirect, render_template, request, url_for

from flaretrace.utilities import valid_destination
from flaretrace.database.connection import get_database
from flaretrace.security import admin_only


@admin_only
def setup_required():
    """Require a valid external homepage redirect before using the workspace.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if request.method == "POST":
        value = request.form.get("homepage_redirect", "").strip()
        if (
            not value
            or not valid_destination(value)
            or value.rstrip("/") == request.host_url.rstrip("/")
        ):
            flash(
                "A valid external HTTP(S) homepage redirect URL is required.", "error"
            )
            return (render_template("initial_setup.html"), 400)
        get_database().execute(
            "INSERT INTO settings(key,value) VALUES('homepage_redirect',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,),
        )
        get_database().commit()
        return redirect(url_for("dashboard"))
    return render_template("initial_setup.html")


@admin_only
def setup():
    """Render connection settings or update the homepage redirect.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if request.method == "POST":
        destination = request.form.get("homepage_redirect", "").strip()
        if not valid_destination(destination) or (
            destination and destination.rstrip("/") == request.host_url.rstrip("/")
        ):
            flash(
                "Enter an HTTP(S) URL other than this homepage, or leave blank.",
                "error",
            )
        else:
            get_database().execute(
                "INSERT INTO settings(key,value) VALUES('homepage_redirect',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (destination,),
            )
            get_database().commit()
            flash("Homepage setting saved.", "success")
            return redirect(url_for("setup"))
    setting = (
        get_database()
        .execute("SELECT value FROM settings WHERE key='homepage_redirect'")
        .fetchone()
    )
    webhook = {
        row["key"]: row["value"]
        for row in get_database().execute(
            "SELECT key,value FROM settings WHERE key IN ('webhook_url','webhook_secret')"
        )
    }
    return render_template(
        "setup.html",
        is_secure=request.is_secure,
        public_base=current_app.config["PUBLIC_BASE_URL"],
        homepage_redirect=setting["value"] if setting else "",
        webhook_url=webhook.get("webhook_url", ""),
        webhook_secret=webhook.get("webhook_secret", ""),
    )


@admin_only
def update_webhook():
    """Save or clear the administrator-configured webhook notification endpoint.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    url = request.form.get("webhook_url", "").strip()
    if url and not valid_destination(url):
        flash("Enter an HTTP(S) webhook URL, or leave blank to disable it.", "error")
        return redirect(url_for("setup"))
    database = get_database()
    database.execute(
        "INSERT INTO settings(key,value) VALUES('webhook_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (url,),
    )
    if url and not database.execute(
        "SELECT 1 FROM settings WHERE key='webhook_secret'"
    ).fetchone():
        database.execute(
            "INSERT INTO settings(key,value) VALUES('webhook_secret',?)",
            (secrets.token_urlsafe(32),),
        )
    database.commit()
    flash("Webhook setting saved." if url else "Webhook disabled.", "success")
    return redirect(url_for("setup"))


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/admin/initial-setup", "setup_required", ["GET", "POST"]],
    ["/admin/setup", "setup", ["GET", "POST"]],
    ["/admin/setup/webhook", "update_webhook", ["POST"]],
]
