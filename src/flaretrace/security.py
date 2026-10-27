"""Security operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import hmac
import secrets
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired

from flaretrace.database.connection import get_database
from flaretrace.links.bundled import BUNDLED_TEMPLATE_ID
from flaretrace.links.templates import (
    DEFAULT_TEMPLATE,
    template_controls,
    render_control,
)
from flaretrace.state import authentication
from flaretrace.limits import limited
from flaretrace.links.repository import get_link


def check_csrf():
    """Reject administrator form submissions without the session-bound CSRF token."""
    if request.path.startswith("/admin") and request.method == "POST":
        expected = session.get("csrf", "")
        supplied = request.form.get("csrf", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(400, "This form expired. Reload the page and try again.")


def security_headers(response):
    """Apply private caching, browser isolation, and page-specific permission policies.

    Args:
        response: Flask response whose browser-security headers will be updated.

    Returns:
        The original response with security headers applied.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = (
        "camera=(self), geolocation=(self), microphone=(), display-capture=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if getattr(g, "capture_page", False):
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    if getattr(g, "link_content", False):
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), display-capture=()"
        )
    if getattr(g, "template_preview", False):
        response.headers["Content-Security-Policy"] = (
            "sandbox allow-scripts allow-forms allow-popups; frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(), display-capture=()"
        )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def template_context():
    """Supply CSRF protection and saved-template choices to Jinja pages.

    Returns:
        Mapping of shared CSRF, retention, and template-library values.
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    templates = (
        get_database()
        .execute("SELECT id,name FROM template_library ORDER BY name")
        .fetchall()
    )
    return {
        "csrf": session["csrf"],
        "retention_days": current_app.config["RETENTION_DAYS"],
        "default_template": DEFAULT_TEMPLATE,
        "template_controls": template_controls,
        "render_control": render_control,
        "available_templates": templates,
        "default_template_id": next(
            (
                template["id"]
                for template in templates
                if template["id"] == BUNDLED_TEMPLATE_ID
            ),
            "",
        ),
    }


def admin_only(view_function):
    """Protect a route with current credentials and initial-setup requirements.

    Args:
        view_function: Flask route callable protected by administrator authentication.

    Returns:
        Wrapped route that checks authentication before calling the original view.
    """

    @wraps(view_function)
    def wrapper(*args, **kwargs):
        """Validate the administrator session before invoking the protected route.

        Args:
            args: Positional values bound to the SQL predicate or wrapped callable.
            kwargs: Keyword arguments forwarded unchanged to the wrapped callable.

        Returns:
            Original view result, or a redirect to login or required setup.
        """
        version = session.get("credential_version", "")
        if (
            not session.get("admin")
            or not isinstance(version, str)
            or (not hmac.compare_digest(version, authentication().credential_version))
        ):
            session.clear()
            return redirect(url_for("login"))
        homepage = (
            get_database()
            .execute("SELECT value FROM settings WHERE key='homepage_redirect'")
            .fetchone()
        )
        if (not homepage or not homepage["value"]) and request.endpoint not in {
            "setup_required",
            "logout",
        }:
            return redirect(url_for("setup_required"))
        return view_function(*args, **kwargs)

    return wrapper


def visitor(visit_id):
    """Authenticate a same-origin, unexpired submission for an open visit.

    Args:
        visit_id: Stored visit identifier used to join events, groups, and media.

    Returns:
        Matching visit row; invalid, expired, closed, or cross-origin requests abort.
    """
    limited("visitor-api", 180)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        abort(403, "Cross-origin submissions are not accepted.")
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)
    try:
        token = authentication().signer.loads(
            request.headers.get("X-Visit-Token", ""), max_age=1800
        )
    except (BadSignature, SignatureExpired):
        abort(403, "This capture session expired. Reopen the link to start again.")
    if token != visit_id:
        abort(403)
    visit = (
        get_database()
        .execute("SELECT * FROM visits WHERE id=?", (visit_id,))
        .fetchone()
    )
    if not visit:
        abort(404)
    get_link(visit["link_id"], available=True)
    if visit["finished"]:
        abort(409, "This capture session is already closed.")
    return visit
