"""Validate browser event data independently of HTTP requests and database writes."""

import json
import math

from flask import abort

#: Browser and application error codes safe to store without leaking raw messages.
EVENT_ERROR_CODES = frozenset(
    {
        "NotAllowedError",
        "NotFoundError",
        "NotReadableError",
        "OverconstrainedError",
        "SecurityError",
        "InvalidStateError",
        "NotSupportedError",
        "AbortError",
        "TimeoutError",
        "NetworkError",
        "UnknownError",
        "UploadError",
        "permission_denied",
        "position_unavailable",
        "location_timeout",
        "insecure_context",
    }
)


def event_envelope(payload):
    """Extract a named event and a JSON-safe data mapping.

    Args:
        payload: Decoded visitor JSON; arbitrary input is rejected safely.

    Returns:
        Pair containing the event kind and its data mapping.

    Raises:
        BadRequest: The envelope is malformed or contains non-finite JSON numbers.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        abort(400, "Expected an event and data object.")
    if not isinstance(payload.get("kind"), str):
        abort(400)
    try:
        json.dumps(payload["data"], allow_nan=False)
    except (ValueError, TypeError):
        abort(400, "Invalid JSON values.")
    return payload["kind"], payload["data"]


def location_event(payload):
    """Validate required coordinates and retain only supported location fields.

    Args:
        payload: Browser geolocation fields, including latitude, longitude, accuracy.

    Returns:
        Validated location mapping with unknown fields removed.

    Raises:
        BadRequest: Coordinates are absent, non-numeric, non-finite, or out of range.
    """
    required = ("latitude", "longitude", "accuracy")
    allowed = (
        *required,
        "altitude",
        "altitudeAccuracy",
        "heading",
        "speed",
        "timestamp",
    )
    if not all(field in payload and payload[field] is not None for field in required):
        abort(400, "Incomplete coordinates.")
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        )
        for value in payload.values()
    ):
        abort(400, "Coordinates must be finite numbers.")
    if not (
        -90 <= payload["latitude"] <= 90
        and -180 <= payload["longitude"] <= 180
        and payload["accuracy"] >= 0
    ):
        abort(400, "Coordinates are out of range.")
    return {field: payload[field] for field in allowed if field in payload}


def permission_event(payload):
    """Validate a camera/location permission result and its bounded error code.

    Args:
        payload: Feature, state, and optional browser error reason.

    Returns:
        Permission mapping restricted to those three fields.

    Raises:
        BadRequest: A feature, state, or error code is unsupported.
    """
    if not isinstance(payload.get("feature"), str) or payload["feature"] not in {
        "location",
        "camera",
    }:
        abort(400)
    if not isinstance(payload.get("state"), str) or payload["state"] not in {
        "granted",
        "denied",
        "error",
        "unsupported",
        "blocked",
    }:
        abort(400)
    if "reason" in payload and (
        not isinstance(payload["reason"], str)
        or payload["reason"]
        and payload["reason"] not in EVENT_ERROR_CODES
    ):
        abort(400, "Expected a supported permission error code.")
    return {
        field: payload[field]
        for field in ("feature", "state", "reason")
        if field in payload
    }


def capture_error_event(payload):
    """Reject capture errors that expose arbitrary messages or unknown cameras.

    Args:
        payload: Exactly a front/rear facing value and a supported reason code.

    Returns:
        Original validated capture-error mapping.

    Raises:
        BadRequest: Fields, facing, or reason do not match the capture contract.
    """
    if (
        set(payload) != {"facing", "reason"}
        or not isinstance(payload["facing"], str)
        or payload["facing"] not in {"front", "rear"}
    ):
        abort(400, "Expected a facing and supported capture error code.")
    if (
        not isinstance(payload["reason"], str)
        or payload["reason"] not in EVENT_ERROR_CODES
    ):
        abort(400, "Expected a facing and supported capture error code.")
    return payload


def diagnostic_numbers(payload):
    """Validate bounded durations, byte counts, and HTTP response codes.

    Args:
        payload: Diagnostic mapping; numeric fields are optional.

    Raises:
        BadRequest: A supplied numeric field has an invalid type or range.
    """
    if "durationMs" in payload:
        duration = payload["durationMs"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not 0 <= duration <= 300000
            or not math.isfinite(duration)
        ):
            abort(400, "Invalid diagnostic duration.")
    for field, minimum, maximum in (("bytes", 0, 8388608), ("httpStatus", 100, 599)):
        if field in payload:
            number = payload[field]
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or not minimum <= number <= maximum
            ):
                abort(400, "Invalid diagnostic number.")


def diagnostic_event(payload):
    """Validate the allowlisted diagnostic schema without retaining arbitrary fields.

    Args:
        payload: Upload/recording outcome and optional bounded metadata.

    Returns:
        Validated diagnostic mapping.

    Raises:
        BadRequest: Required fields are missing or any value is unsupported.
    """
    enums = {
        "stage": {"upload", "recording"},
        "outcome": {"success", "error", "unsupported"},
        "mediaKind": {"photo", "video"},
        "facing": {"front", "rear"},
        "format": {"image/jpeg", "video/webm", "video/mp4"},
        "error": EVENT_ERROR_CODES,
    }
    if not {"stage", "outcome"} <= set(payload) or set(payload) - (
        set(enums) | {"durationMs", "bytes", "httpStatus"}
    ):
        abort(400, "Unexpected or missing diagnostic fields.")
    for field, allowed in enums.items():
        if field in payload and (
            not isinstance(payload[field], str) or payload[field] not in allowed
        ):
            abort(400, "Invalid diagnostic value.")
    diagnostic_numbers(payload)
    return payload


def validate_event(kind, payload):
    """Dispatch one supported event to its narrowly scoped validation function.

    Args:
        kind: Browser event name from a validated envelope.
        payload: JSON-safe event data mapping.

    Returns:
        Sanitized event data ready to persist.

    Raises:
        BadRequest: The event kind or its data is unsupported.
    """
    validators = {
        "browser": dict,
        "location": location_event,
        "permission": permission_event,
        "capture_error": capture_error_event,
        "diagnostic": diagnostic_event,
    }
    if kind in validators:
        return validators[kind](payload)
    if kind in {"capture_started", "notice_shown"}:
        return {
            str(field)[:40]: str(value)[:500]
            for field, value in list(payload.items())[:8]
        }
    if kind == "finished":
        return {"reason": str(payload.get("reason", "complete"))[:100]}
    abort(400, "Unknown event type.")
