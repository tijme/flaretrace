"""Routes authentication operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import hmac
import secrets

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from flaretrace.database.connection import get_database
from flaretrace.state import authentication
from flaretrace.limits import limited
from flaretrace.security import admin_only


def login():
    """Render the login form or authenticate administrator credentials.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    if request.method == "POST":
        limited("login", 10, 900)
        supplied_username = request.form.get("username", "")[:256]
        username_ok = hmac.compare_digest(
            supplied_username.encode(), authentication().username.encode()
        )
        password_ok = check_password_hash(
            authentication().password_hash, request.form.get("password", "")[:1024]
        )
        if username_ok and password_ok:
            session.clear()
            session["admin"] = True
            session["admin_username"] = authentication().username
            session["credential_version"] = authentication().credential_version
            session["csrf"] = secrets.token_urlsafe(24)
            session.permanent = True
            homepage = (
                get_database()
                .execute("SELECT value FROM settings WHERE key='homepage_redirect'")
                .fetchone()
            )
            return redirect(
                url_for(
                    "dashboard" if homepage and homepage["value"] else "setup_required"
                )
            )
        flash("Incorrect username or password. Try again.", "error")
    return render_template("login.html")


@admin_only
def logout():
    """Clear the administrator session and redirect to the login form.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    session.clear()
    return redirect(url_for("login"))


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/admin/login", "login", ["GET", "POST"]],
    ["/admin/logout", "logout", ["POST"]],
]
