"""Routes capture operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

import json
import re
import secrets

from flask import abort, current_app, g, render_template, request

from flaretrace.utilities import now, safe_referrer
from flaretrace.database.connection import get_database
from flaretrace.links.templates import DEFAULT_TEMPLATE, validate_template
from flaretrace.visits.groups import assign_group
from flaretrace.state import authentication, media_directory
from flaretrace.limits import limited
from flaretrace.security import visitor
from flaretrace.links.repository import get_public_link
from flaretrace.visits.records import add_event
from flaretrace.visits.events import event_envelope, validate_event
from flaretrace.visits.uploads import save_media


def capture(slug):
    """Create a visit record and render its signed, permission-based capture page.

    Args:
        slug: Public research-link identifier stored in the links table.

    Returns:
        Flask-compatible page, redirect, file response, or validation error for this route.
    """
    limited("landing", 40)
    link = get_public_link(slug)
    visit_id = secrets.token_hex(12)
    headers = {}
    for key, value in list(request.headers.items())[:60]:
        if re.search(
            "auth|cookie|token|secret|password|api.?key|credential", key, re.I
        ):
            continue
        headers[key] = (
            safe_referrer(value) if key.lower() == "referer" else value[:1024]
        )
    campaign = {
        key: request.args[key][:200]
        for key in (
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
            "utm_term",
        )
        if key in request.args
    }
    peer = request.environ.get("werkzeug.proxy_fix.orig", {}).get(
        "REMOTE_ADDR", request.remote_addr or ""
    )
    headers["Transport-Peer-IP"] = peer
    created_at = now()
    get_database().execute("BEGIN IMMEDIATE")
    get_database().execute(
        "INSERT INTO visits(id,link_id,created_at,ip,method,user_agent,referrer,headers,campaign)\n            VALUES(?,?,?,?,?,?,?,?,?)",
        (
            visit_id,
            link["id"],
            created_at,
            request.remote_addr or "",
            request.method,
            request.user_agent.string[:1000],
            safe_referrer(request.referrer or ""),
            json.dumps(headers),
            json.dumps(campaign),
        ),
    )
    assign_group(
        get_database(), visit_id, link["id"], request.remote_addr or "", created_at
    )
    get_database().commit()
    config = {
        "id": visit_id,
        "token": authentication().signer.dumps(visit_id),
        "duration": 5,
        "destination": link["destination"],
        "retention": current_app.config["RETENTION_DAYS"],
    }
    g.capture_page = True
    return render_template(
        "capture.html",
        link=link,
        capture_config=config,
        template_source=validate_template(link["template_html"]) or DEFAULT_TEMPLATE,
    )


def receive_event(visit_id):
    """Validate, store, and acknowledge one signed visitor event.

    Args:
        visit_id: Visit identifier bound to the supplied X-Visit-Token header.

    Returns:
        JSON-compatible success mapping after a committed database write.
    """
    visitor(visit_id)
    if request.content_length is None or request.content_length > 24000:
        abort(413)
    kind, payload = event_envelope(request.get_json())
    validated = validate_event(kind, payload)
    connection = get_database()
    if kind == "browser":
        connection.execute(
            "UPDATE visits SET browser=?,interacted=1 WHERE id=?",
            (json.dumps(validated), visit_id),
        )
    elif kind == "location":
        connection.execute(
            "UPDATE visits SET location=? WHERE id=?", (json.dumps(validated), visit_id)
        )
    elif kind == "finished":
        connection.execute("UPDATE visits SET finished=1 WHERE id=?", (visit_id,))
    add_event(visit_id, kind, validated)
    connection.commit()
    return {"ok": True}


def receive_media(visit_id):
    """Authenticate a capture submission and delegate atomic file persistence.

    Args:
        visit_id: Visit identifier bound to the supplied signed visitor token.

    Returns:
        JSON-compatible success mapping and HTTP 201 after storage commits.
    """
    visitor(visit_id)
    media_id = save_media(
        get_database(),
        media_directory(),
        visit_id,
        request.args.get("kind"),
        request.args.get("facing"),
        request.mimetype,
        request.get_data(),
    )
    return {"ok": True, "id": media_id}, 201


#: Paths, stable endpoint names, and accepted HTTP methods for this feature.
ROUTES = [
    ["/l/<slug>", "capture", ["GET"]],
    ["/api/visits/<visit_id>/events", "receive_event", ["POST"]],
    ["/api/visits/<visit_id>/media", "receive_media", ["POST"]],
]
