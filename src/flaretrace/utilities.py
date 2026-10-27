"""Pure time, environment, and URL helpers shared by routes and startup."""

import os
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

#: Timezone used for every persisted server timestamp.
UTC = timezone.utc


def now():
    """Format the current UTC instant for database records.

    Returns:
        ISO 8601 UTC timestamp with second precision.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def env_bool(name, default=False):
    """Read a boolean environment option with an explicit fallback.

    Args:
        name: Name of the option, entity, or field being processed.
        default: Fallback used when no usable input is supplied.

    Returns:
        True for 1, true, or yes; otherwise False.
    """
    return os.environ.get(name, str(int(default))).lower() in {"1", "true", "yes"}


def safe_referrer(value):
    """Remove credentials, query parameters, and fragments from a referrer URL.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        Bounded HTTP(S) URL without sensitive components, or an empty string.
    """
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path[:500], "", ""))[:1000]
    except ValueError:
        return ""


def valid_destination(value):
    """Check whether an optional redirect is a safe HTTP(S) URL.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        True for an empty or acceptable credential-free HTTP(S) URL.
    """
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        _port = parsed.port
        return (
            len(value) <= 2000
            and parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and (not parsed.username)
            and (not parsed.password)
            and (not any((ord(character) < 33 for character in value)))
            and ("\\" not in value)
        )
    except ValueError:
        return False
