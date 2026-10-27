"""Resolve optional public-host settings without requiring an .env file."""

from __future__ import annotations

import os
import re
from datetime import timedelta
from urllib.parse import urlsplit

from .utilities import env_bool, valid_destination


def configure_application(application, overrides=None):
    """Apply environment settings and explicit overrides to a Flask application.

    Args:
        application: Flask application whose config mapping will be updated.
        overrides: Optional configuration mapping, normally supplied by tests.

    Raises:
        ValueError: The configured public origin or deployment options are invalid.
    """
    from pathlib import Path

    runtime = runtime_options()
    public_origin = runtime["PUBLIC_BASE_URL"]
    application.config.from_mapping(
        DATA_DIR=os.environ.get("DATA_DIR", str(Path.cwd() / "data")),
        ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME") or "admin",
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
        PUBLIC_BASE_URL=public_origin,
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=public_origin.startswith("https://")
        or env_bool("COOKIE_SECURE"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        TRUST_PROXY=runtime["TRUST_PROXY"],
    )
    application.config.update(overrides or {})
    application.config["RETENTION_DAYS"] = 0
    if application.config["PUBLIC_BASE_URL"].startswith("https://"):
        application.config["SESSION_COOKIE_SECURE"] = True
    if application.config["PUBLIC_BASE_URL"] and not valid_destination(
        application.config["PUBLIC_BASE_URL"]
    ):
        raise ValueError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL")


def _flag(value, name):
    """Parse an explicit deployment boolean or reject an ambiguous value.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        name: Name of the option, entity, or field being processed.

    Returns:
        Normalized boolean; invalid explicit values raise ValueError.

    Raises:
        ValueError: The explicit boolean value is not 1/0, true/false, or yes/no.
    """
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be 1 or 0 (true/false also accepted)")


def _host(value):
    """Validate a bare deployment hostname without accepting URL syntax.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        Normalized deployment hostname, or an empty string when unset.

    Raises:
        ValueError: The hostname is empty, too long, non-ASCII, or contains invalid labels.
    """
    hostname = value.lower().rstrip(".")
    if not hostname or len(hostname) > 253 or not hostname.isascii():
        raise ValueError(
            "DOMAIN must be a hostname such as trace.example.com; use ASCII/punycode"
        )
    if any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError("DOMAIN must be a hostname without a scheme, path, or port")
    return hostname


def _site_address(value):
    """Validate the host and optional scheme accepted by Caddy.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.

    Returns:
        Validated Caddy site address, or an empty string when unset.

    Raises:
        ValueError: The site address has invalid URL components, hostname, or port.
    """
    explicit_scheme = "://" in value
    parsed = urlsplit(value if explicit_scheme else "//" + value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("SITE_ADDRESS has an invalid port") from error
    if (
        parsed.scheme not in {"", "http", "https"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        raise ValueError("SITE_ADDRESS must be an HTTP(S) origin or hostname")
    hostname = _host(parsed.hostname)
    return hostname, port, parsed.scheme if explicit_scheme else ""


def runtime_options(environ=None):
    """Resolve validated public-host and proxy settings from an environment mapping.

    Args:
        environ: Optional environment mapping; defaults to os.environ.

    Returns:
        DOMAIN, AUTO_TLS, SITE_ADDRESS, PUBLIC_BASE_URL, and TRUST_PROXY values.
        Domain-based deployments default to HTTP; bare legacy SITE_ADDRESS retains
        its HTTPS default. An explicit public origin takes precedence.
    """
    values = os.environ if environ is None else environ
    hostname, port, auto_tls = deployment_host(values)
    port = published_port(values, hostname, port, auto_tls)
    address = hostname + (f":{port}" if port is not None else "")
    inferred_origin = (
        f"{'https' if auto_tls else 'http'}://{address}" if address else ""
    )
    return {
        "DOMAIN": hostname,
        "AUTO_TLS": auto_tls,
        "SITE_ADDRESS": inferred_origin or "http://localhost",
        "PUBLIC_BASE_URL": public_origin(values, inferred_origin, auto_tls),
        "TRUST_PROXY": proxy_trust(values, hostname),
    }


def deployment_host(values):
    """Resolve a normalized hostname, legacy port, and automatic-TLS flag.

    Args:
        values: Deployment environment settings.

    Returns:
        Hostname, optional explicit port, and whether HTTPS is enabled.

    Raises:
        ValueError: Hostname, legacy origin, or TLS configuration is invalid.
    """
    domain = values.get("DOMAIN", "").strip()
    legacy = values.get("SITE_ADDRESS", "").strip()
    hostname, port, legacy_scheme = "", None, ""
    if domain:
        hostname = _host(domain)
    elif legacy:
        hostname, port, legacy_scheme = _site_address(legacy)
    tls_value = values.get("AUTO_TLS", "").strip()
    auto_tls = (
        _flag(tls_value, "AUTO_TLS")
        if tls_value
        else bool(legacy and not domain and legacy_scheme != "http")
    )
    if auto_tls and (
        not hostname or "." not in hostname or re.fullmatch(r"[0-9.]+", hostname)
    ):
        raise ValueError(
            "AUTO_TLS=1 requires a fully qualified DNS domain (DOMAIN=trace.example.com)"
        )
    return hostname, port, auto_tls


def published_port(values, hostname, legacy_port, auto_tls):
    """Choose the external port without overriding an explicit legacy origin port.

    Args:
        values: Deployment environment settings.
        hostname: Normalized public hostname, or an empty string.
        legacy_port: Port explicitly supplied in SITE_ADDRESS, if present.
        auto_tls: Whether the public listener uses HTTPS.

    Returns:
        Non-default published port, or None for the protocol default.

    Raises:
        ValueError: The selected port is not an integer from 1 to 65535.
    """
    if not hostname or legacy_port is not None:
        return legacy_port
    setting = "HTTPS_PORT" if auto_tls else "HTTP_PORT"
    default_port = 443 if auto_tls else 80
    raw_port = str(values.get(setting, default_port))
    if not re.fullmatch(r"[0-9]{1,5}", raw_port) or not 1 <= int(raw_port) <= 65535:
        raise ValueError(f"{setting} must be an integer from 1 to 65535")
    return int(raw_port) if int(raw_port) != default_port else None


def public_origin(values, inferred_origin, auto_tls):
    """Validate the explicit or inferred HTTP(S) origin used for shared links.

    Args:
        values: Deployment environment settings.
        inferred_origin: Origin calculated from domain, TLS, and published ports.
        auto_tls: Whether an HTTP origin would conflict with automatic TLS.

    Returns:
        Public origin without a trailing slash, or an empty string.

    Raises:
        ValueError: The origin contains invalid ports, credentials, or URL components.
    """
    origin = values.get("PUBLIC_BASE_URL", "").strip().rstrip("/") or inferred_origin
    if not origin:
        return origin
    parsed = urlsplit(origin)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("PUBLIC_BASE_URL has an invalid port") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "PUBLIC_BASE_URL must be an HTTP(S) origin without a path or credentials"
        )
    if auto_tls and parsed.scheme != "https":
        raise ValueError("PUBLIC_BASE_URL must use HTTPS when AUTO_TLS=1")
    return origin


def proxy_trust(values, hostname):
    """Trust forwarded headers only when explicitly enabled or safely inferred.

    Args:
        values: Deployment environment settings.
        hostname: Whether a built-in public proxy is configured.

    Returns:
        True only for explicit trust or a domain with a loopback app binding.
    """
    explicit = values.get("TRUST_PROXY", "").strip()
    loopback = values.get("BIND_ADDRESS", "127.0.0.1") in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    return _flag(explicit, "TRUST_PROXY") if explicit else bool(hostname and loopback)
