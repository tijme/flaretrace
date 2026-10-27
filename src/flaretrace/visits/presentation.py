"""Readable summaries of stored visit data. No additional collection or lookups."""

from datetime import datetime, timezone
import json
import math
import re


def mapping(value):
    """Accept mapping-shaped metadata and replace unsupported shapes with an empty object.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        Original dictionary, or an empty dictionary for every other input type.
    """
    return value if isinstance(value, dict) else {}


def text(value, default="Not reported"):
    """Produce a bounded display string with a fallback for missing values.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        default: Fallback used when no usable input is supplied.

    Returns:
        Human-readable bounded text or the configured fallback.
    """
    return value.strip()[:240] if isinstance(value, str) and value.strip() else default


def format_time(value, mode="full"):
    """Format a stored timestamp as a UTC clock, date, or complete timestamp.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        mode: Requested display format for the stored timestamp.

    Returns:
        Formatted date/time text, with a safe fallback for invalid input.
    """
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo:
            instant = instant.astimezone(timezone.utc)
        pattern = (
            "%H:%M:%S"
            if mode == "clock"
            else "%d %b %Y"
            if mode == "date"
            else "%d %b %Y · %H:%M:%S UTC"
        )
        return instant.strftime(pattern)
    except (ValueError, TypeError, AttributeError):
        return "Time unavailable"


def format_bytes(value):
    """Format a byte count using compact, human-readable binary units.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        Compact size label, such as 1.0 KiB.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return "Size unavailable"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def browser_name(agent):
    """Identify common browser families from a supplied user-agent string.

    Args:
        agent: Browser user-agent text supplied with the visit.

    Returns:
        Recognized browser-family label or a generic fallback.
    """
    for label, pattern in (
        ("Edge", r"Edg(?:e|A|iOS)?/([\d.]+)"),
        ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
        ("Opera", r"OPR/([\d.]+)"),
        ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"),
        ("Safari", r"Version/([\d.]+).*Safari"),
    ):
        match = re.search(pattern, agent)
        if match:
            return f"{label} {match[1].split('.')[0]}"
    return "Unknown browser"


def device_name(agent, platform):
    """Identify the device family from browser and platform metadata.

    Args:
        agent: Browser user-agent text supplied with the visit.
        platform: Optional browser-reported operating system or platform label.

    Returns:
        Recognized device or platform label or a generic fallback.
    """
    for marker, label in (
        ("iPad", "iPad"),
        ("iPhone", "iPhone"),
        ("Android", "Android device"),
        ("Windows", "Windows computer"),
        ("Macintosh", "Mac computer"),
        ("Linux", "Linux computer"),
    ):
        if marker in agent:
            return label
    return text(platform, "Unknown device")


#: Public explanations for allowlisted capture-error codes; raw exceptions stay private.
ERRORS = {
    "NotAllowedError": "Access was declined or blocked by the browser.",
    "permission_denied": "The visitor declined this permission.",
    "NotFoundError": "The requested camera was not available.",
    "NotReadableError": "The camera could not be opened. It may have been in use.",
    "OverconstrainedError": "The device did not support the requested camera.",
    "SecurityError": "The browser blocked access for security reasons.",
    "insecure_context": "HTTPS or localhost was required.",
    "TimeoutError": "The request timed out.",
    "location_timeout": "The location request timed out.",
    "position_unavailable": "The device could not determine its location.",
    "NetworkError": "The upload could not reach the server.",
    "UploadError": "The server could not accept the upload.",
    "AbortError": "The operation was stopped.",
}


def event_description(kind, body):
    """Return a readable title and explanation for one stored browser event.

    Args:
        kind: Stored event kind, including unknown legacy names.
        body: Decoded event data mapping.

    Returns:
        Title/explanation pair, or None for redundant recording-success events.
    """
    simple_events = {
        "notice_shown": ("Sharing activated", "The sharing control was used."),
        "browser": (
            "Device details received",
            "The browser submitted its reported device information.",
        ),
        "location": ("Location received", "Coordinates were saved for this click."),
    }
    if kind in simple_events:
        return simple_events[kind]
    facing = camera_facing(body)
    camera = (facing + " camera").strip().capitalize()
    if kind == "permission":
        return permission_description(body)
    if kind == "capture_started":
        return (
            f"{camera} started",
            "The browser began a photo and silent video capture.",
        )
    if kind == "capture_error":
        return f"{camera} could not finish", ERRORS.get(
            body.get("reason"), "The capture could not be completed."
        )
    if kind == "diagnostic":
        return diagnostic_description(body, facing)
    if kind == "finished":
        return finished_description(body.get("reason"))
    return text(kind, "Event").replace("_", " ").capitalize(), "Event recorded."


def present_visit(visit, details, events, media):
    """Combine stored evidence into the template's visit-inspector view model.

    Args:
        visit: SQLite visit row with timestamps, user agent, and session flags.
        details: Decoded browser, location, headers, campaign, and IP-lookup mappings.
        events: Stored event rows ordered newest first.
        media: Stored photo/video metadata rows; files are never opened here.

    Returns:
        Human-readable session, device, location, media, network, and timeline fields.
    """
    permissions, reason, timeline = event_history(events, visit["created_at"])
    status, tone, summary = session_summary(visit, reason)
    browser = mapping(details.get("browser"))
    return {
        "status": status,
        "tone": tone,
        "summary": summary,
        "reason": reason,
        **device_summary(visit, browser),
        **network_summary(mapping(details.get("ip_info"))),
        "location": sharing_state(
            "location", bool(details.get("location")), permissions
        ),
        "camera": sharing_state("camera", bool(media), permissions),
        "photos": sum(item["kind"] == "photo" for item in media),
        "videos": sum(item["kind"] == "video" for item in media),
        "media_bytes": sum(item["size"] for item in media),
        "timeline": timeline,
    }


def camera_facing(body):
    """Return a readable front/rear label without trusting malformed stored fields.

    Args:
        body: Decoded event mapping.

    Returns:
        Front, Rear, or an empty string for unknown camera values.
    """
    facing = body.get("facing")
    return (
        {"rear": "Rear", "front": "Front"}.get(facing, "")
        if isinstance(facing, str)
        else ""
    )


def permission_description(body):
    """Explain the reported permission state without implying data was received.

    Args:
        body: Permission event with feature, state, and optional reason.

    Returns:
        Human-readable title and bounded error explanation.
    """
    feature = "Location" if body.get("feature") == "location" else "Camera"
    labels = {
        "granted": "allowed",
        "denied": "declined",
        "error": "unavailable",
        "unsupported": "not supported",
        "blocked": "blocked",
    }
    return f"{feature} access {labels.get(body.get('state'), 'reported')}", ERRORS.get(
        body.get("reason"), "Browser permission result recorded."
    )


def diagnostic_description(body, facing):
    """Explain upload outcomes while hiding redundant successful recording events.

    Args:
        body: Stored upload or recording diagnostic mapping.
        facing: Readable camera label, or an empty string.

    Returns:
        Title/explanation pair, or None for a successful recording event.
    """
    if body.get("stage") == "recording" and body.get("outcome") == "success":
        return None
    media = (
        body.get("mediaKind") if body.get("mediaKind") in {"photo", "video"} else "file"
    )
    if body.get("outcome") == "error":
        return f"{facing} {media} failed".strip().capitalize(), ERRORS.get(
            body.get("error"), "The operation could not be completed."
        )
    if body.get("outcome") == "unsupported":
        return (
            "Video recording not supported",
            "The browser could only provide a photo.",
        )
    return f"{facing} {media} uploaded".strip().capitalize(), format_bytes(
        body.get("bytes")
    )


def finished_description(reason):
    """Describe why a visitor session stopped accepting submissions.

    Args:
        reason: Stored completion reason, including unrecognized legacy values.

    Returns:
        Session outcome title and explanation.
    """
    outcomes = {
        "declined": ("Sharing declined", "The visitor continued without sharing."),
        "stopped": (
            "Sharing stopped",
            "The stop control was used; camera access ended.",
        ),
        "page_hidden": (
            "Sharing ended in the background",
            "The page was hidden, so capture stopped.",
        ),
        "complete": (
            "Session completed",
            "The browser finished its requests and turned the cameras off.",
        ),
    }
    return outcomes.get(
        reason, ("Session ended", "No further submissions are accepted for this click.")
    )


def event_history(events, requested_at):
    """Collect latest permission outcomes and a readable, newest-first timeline.

    Args:
        events: Stored event rows ordered newest first.
        requested_at: Original request timestamp used for the final timeline entry.

    Returns:
        Permission mapping, latest completion reason, and timeline entries.
    """
    permissions, reason, timeline = {}, None, []
    for event in events:
        try:
            body = mapping(json.loads(event["payload"]))
        except (ValueError, TypeError):
            body = {}
        if event["kind"] == "permission" and isinstance(body.get("feature"), str):
            permissions.setdefault(body["feature"], body)
        if event["kind"] == "finished" and reason is None:
            reason = text(body.get("reason"), "")
        description = event_description(event["kind"], body)
        if description:
            timeline.append(
                {
                    "id": event["id"],
                    "time": event["created_at"],
                    "title": description[0],
                    "description": description[1],
                }
            )
    timeline.append(
        {
            "id": "request",
            "time": requested_at,
            "title": "Link requested",
            "description": "The server received this click.",
        }
    )
    return permissions, reason, timeline


def sharing_state(feature, received, permissions):
    """Keep permission approval distinct from evidence actually received.

    Args:
        feature: Camera or location feature name.
        received: Whether corresponding records/files exist.
        permissions: Latest permission result indexed by feature.

    Returns:
        Label, semantic color tone, and explanatory text.
    """
    if received:
        return {
            "label": "Received",
            "tone": "green",
            "explanation": "Saved with this click.",
        }
    permission = permissions.get(feature, {})
    state = permission.get("state")
    labels = {
        "granted": "Allowed · nothing received",
        "denied": "Declined",
        "error": "Unavailable",
        "unsupported": "Not supported",
        "blocked": "Blocked",
    }
    explanation = ERRORS.get(permission.get("reason"))
    if not explanation:
        explanation = (
            "Permission was allowed, but no data was uploaded."
            if state == "granted"
            else "The browser did not support this feature."
            if state == "unsupported"
            else "No data was shared for this click."
        )
    return {
        "label": labels.get(state, "Not shared"),
        "tone": "amber" if state in {"error", "blocked"} else "neutral",
        "explanation": explanation,
    }


def session_summary(visit, reason):
    """Choose the overall session label without guessing an individual's identity.

    Args:
        visit: Stored visit row containing finished and interacted flags.
        reason: Latest stored completion reason, or None.

    Returns:
        Session status, semantic color tone, and one-sentence summary.
    """
    if reason == "declined":
        return (
            "Sharing declined",
            "neutral",
            "The visitor chose not to share location or camera captures.",
        )
    if visit["finished"]:
        status, tone = (
            ("Stopped", "neutral")
            if reason in {"stopped", "page_hidden"}
            else (
                ("Session completed", "green")
                if reason == "complete"
                else ("Session ended", "neutral")
            )
        )
        return (
            status,
            tone,
            "This session has ended. The received information is listed below.",
        )
    if visit["interacted"]:
        return (
            "Sharing started",
            "green",
            "A sharing action was recorded. Permission results and received files are shown below.",
        )
    return (
        "Request only",
        "neutral",
        "The server received this click. No sharing action has been recorded.",
    )


def viewport_dimensions(viewport):
    """Format plausible browser dimensions without treating booleans as integers.

    Args:
        viewport: Browser-reported viewport mapping.

    Returns:
        Width × height, or Not reported for missing/invalid dimensions.
    """
    width, height = viewport.get("width"), viewport.get("height")
    valid = all(
        isinstance(dimension, int)
        and not isinstance(dimension, bool)
        and 0 < dimension <= 100000
        for dimension in (width, height)
    )
    return f"{width} × {height}" if valid else "Not reported"


def device_summary(visit, browser):
    """Format the request and browser-reported device fields.

    Args:
        visit: Stored visit row supplying fallback user agent and referrer.
        browser: Decoded browser metadata mapping.

    Returns:
        Browser/device labels, dimensions, network type, and bot hint.
    """
    agent = text(browser.get("userAgent"), visit["user_agent"] or "")
    connection = mapping(browser.get("connection"))
    return {
        "browser": browser_name(agent),
        "device": device_name(agent, browser.get("platform")),
        "language": text(browser.get("language")),
        "timezone": text(browser.get("timezone")),
        "viewport": viewport_dimensions(mapping(browser.get("viewport"))),
        "connection": text(connection.get("effectiveType")),
        "referrer": visit["referrer"] or "Direct / not supplied",
        "possible_bot": bool(
            re.search(
                r"bot\b|crawler|spider|preview|facebookexternalhit|headless",
                agent,
                re.I,
            )
        ),
    }


def network_summary(info):
    """Format optional approximate IP-provider information separately from GPS.

    Args:
        info: Decoded provider response previously saved by the administrator.

    Returns:
        Approximate place, provider name, and autonomous-system label.
    """
    return {
        "ip_place": ", ".join(
            text(info.get(field), "")
            for field in ("city", "region", "country")
            if text(info.get(field), "")
        ),
        "isp": text(info.get("isp") or info.get("org")),
        "asn": text(str(info["asn"]) if info.get("asn") else None),
    }
