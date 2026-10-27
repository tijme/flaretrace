"""Validate container arguments before touching storage, credentials, or network listeners."""

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from flaretrace.config import _flag, _host, _site_address, public_origin


@dataclass(frozen=True)
class RuntimeSettings:
    """Validated settings shared by storage initialization and both serving processes.

    Attributes:
        command: Serve, display administrator credentials, or check container readiness.
        data_directory: Persistent database, media, credential, and certificate directory.
        domain: Public hostname; empty for HTTP requests using their original Host header.
        auto_tls: Whether Caddy manages HTTPS certificates and HTTP redirects.
        tls_issuer: Public ACME certificates or a local, explicitly selected development CA.
        http_port: Container HTTP listener; Docker publication must be configured separately.
        https_port: Container HTTPS TCP/UDP listener and advertised redirect port.
        backend_port: Private loopback Gunicorn listener, distinct from the public ports.
        public_origin: Explicit or inferred origin for shared research links.
        user_id: Non-root numeric account used by both serving processes.
        group_id: Non-root numeric group used by both serving processes.
        repair_storage: Whether to force a full ownership scan of persistent storage.
        admin_username: Validated administrator login name passed to Flask.
        admin_password: Optional explicit credential; excluded from diagnostic representations.
    """

    command: str
    data_directory: Path
    domain: str
    auto_tls: bool
    tls_issuer: str
    http_port: int
    https_port: int
    backend_port: int
    public_origin: str
    user_id: int
    group_id: int
    repair_storage: bool
    admin_username: str
    admin_password: str = field(repr=False)


def port_number(value):
    """Validate a container listener port without accepting whitespace or socket syntax.

    Args:
        value: Decimal port supplied by an argument or legacy environment setting.

    Returns:
        Integer from 1 through 65535.

    Raises:
        ValueError: The input is not a permitted decimal port.
    """
    if not re.fullmatch(r"[0-9]{1,5}", str(value)) or not 1 <= int(value) <= 65535:
        raise ValueError("ports must be integers from 1 through 65535")
    return int(value)


def account_number(value):
    """Reject root and invalid numeric accounts for the long-running serving processes.

    Args:
        value: Numeric UID or GID requested for the container's serving processes.

    Returns:
        Positive UID or GID fitting a signed 32-bit account identifier.

    Raises:
        ValueError: The account is root, negative, malformed, or outside the supported range.
    """
    if not str(value).isdigit() or not 1 <= int(value) < 2**31:
        raise ValueError("UID/GID must be a non-root integer from 1 through 2147483647")
    return int(value)


def argument_parser(environment):
    """Describe container operations and legacy environment fallbacks in one parser.

    Args:
        environment: Environment mapping; explicit arguments override these defaults.

    Returns:
        ArgumentParser for the installed flaretrace container command.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("serve", "credentials", "healthcheck"),
        nargs="?",
        default="serve",
    )
    parser.add_argument(
        "--domain",
        default=environment.get("DOMAIN", ""),
        help="Public hostname; automatically uses the bundled proxy.",
    )
    parser.add_argument(
        "--auto-tls",
        nargs="?",
        const="1",
        default=environment.get("AUTO_TLS") or None,
        help="Obtain and renew HTTPS certificates; optional 0/1 value.",
    )
    parser.add_argument(
        "--no-auto-tls", dest="auto_tls", action="store_const", const="0"
    )
    parser.add_argument(
        "--tls-issuer",
        choices=("acme", "internal"),
        default="acme",
        help="internal is a development CA, not publicly trusted.",
    )
    parser.add_argument(
        "--http-port",
        help="HTTP listener inside the container (default: 80).",
    )
    parser.add_argument(
        "--https-port",
        help="HTTPS listener inside the container (default: 443).",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path(environment.get("DATA_DIR", "/data"))
    )
    parser.add_argument(
        "--public-base-url", default=environment.get("PUBLIC_BASE_URL", "")
    )
    parser.add_argument(
        "--uid", default="10001", help="Non-root serving UID after bootstrap."
    )
    parser.add_argument(
        "--gid", default="10001", help="Non-root serving GID after bootstrap."
    )
    parser.add_argument(
        "--repair-storage",
        action="store_true",
        help="Force an ownership repair without deleting data or secrets.",
    )
    parser.add_argument(
        "--admin-username", default=environment.get("ADMIN_USERNAME") or "admin"
    )
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument(
        "--admin-password",
        default=environment.get("ADMIN_PASSWORD", ""),
        help="Prefer --admin-password-file to avoid shell-history exposure.",
    )
    credentials.add_argument(
        "--admin-password-file",
        type=Path,
        help="Read an explicit password from a mounted secret file.",
    )
    return parser


def public_settings(arguments, environment):
    """Resolve public listeners and origins while retaining old SITE_ADDRESS support.

    Args:
        arguments: Parsed namespace; only chosen argument values are validated.
        environment: Optional legacy SITE_ADDRESS used when no domain is supplied.

    Returns:
        Domain, TLS flag, HTTP port, HTTPS port, and public origin in that order.

    Raises:
        ValueError: Hostname, listener ports, TLS requirements, or public origin is invalid.
    """
    domain, legacy_port, legacy_scheme = arguments.domain.strip(), None, ""
    if not domain and environment.get("SITE_ADDRESS"):
        domain, legacy_port, legacy_scheme = _site_address(environment["SITE_ADDRESS"])
    domain = _host(domain) if domain else ""
    auto_tls = (
        _flag(arguments.auto_tls, "--auto-tls")
        if arguments.auto_tls is not None
        else bool(
            legacy_scheme != "http"
            and environment.get("SITE_ADDRESS")
            and not arguments.domain
        )
    )
    http_port, https_port = listener_ports(
        arguments, environment, auto_tls, legacy_port
    )
    if http_port == https_port:
        raise ValueError("--http-port and --https-port must be different")
    if auto_tls and not domain:
        raise ValueError("--auto-tls requires --domain HOST")
    if (
        auto_tls
        and arguments.tls_issuer == "acme"
        and ("." not in domain or re.fullmatch(r"[0-9.]+", domain))
    ):
        raise ValueError("public automatic TLS requires a fully qualified DNS domain")
    if arguments.tls_issuer == "internal" and not auto_tls:
        raise ValueError("--tls-issuer internal requires --auto-tls")
    scheme, selected_port = ("https", https_port) if auto_tls else ("http", http_port)
    suffix = "" if selected_port == (443 if auto_tls else 80) else f":{selected_port}"
    inferred = f"{scheme}://{domain}{suffix}" if domain else ""
    origin = public_origin(
        {"PUBLIC_BASE_URL": arguments.public_base_url}, inferred, auto_tls
    )
    return domain, auto_tls, http_port, https_port, origin


def listener_ports(arguments, environment, auto_tls, legacy_port):
    """Choose listener ports with explicit arguments ahead of legacy origins and defaults.

    Args:
        arguments: Parsed namespace; absent listener arguments remain None.
        environment: Optional old HTTP_PORT and HTTPS_PORT fallback values.
        auto_tls: Whether a legacy origin's explicit port belongs to HTTPS.
        legacy_port: Optional port embedded in the old SITE_ADDRESS origin.

    Returns:
        Validated HTTP and HTTPS listener integers; collisions are rejected by public_settings.
    """
    http_default = (
        legacy_port
        if legacy_port is not None and not auto_tls
        else environment.get("HTTP_PORT", "80")
    )
    https_default = (
        legacy_port
        if legacy_port is not None and auto_tls
        else environment.get("HTTPS_PORT", "443")
    )
    return (
        port_number(
            arguments.http_port if arguments.http_port is not None else http_default
        ),
        port_number(
            arguments.https_port if arguments.https_port is not None else https_default
        ),
    )


def parse_settings(argv=None, environ=None):
    """Parse and validate container arguments without starting services or mutating storage.

    Args:
        argv: Argument sequence; None reads the current process arguments.
        environ: Optional environment mapping; None uses the process environment.

    Returns:
        Immutable runtime settings, with explicit credentials hidden from representations.

    Raises:
        SystemExit: An invalid option, credential, or deployment setting was supplied.
        ValueError: Invalid credentials are caught locally and reported as argument errors.
    """
    environment = os.environ if environ is None else environ
    parser = argument_parser(environment)
    arguments = parser.parse_args(argv)
    try:
        domain, auto_tls, http_port, https_port, origin = public_settings(
            arguments, environment
        )
        password = (
            arguments.admin_password_file.read_text().strip()
            if arguments.admin_password_file
            else arguments.admin_password
        )
        if password and len(password) < 12:
            raise ValueError(
                "administrator passwords must contain at least 12 characters"
            )
        if arguments.admin_password_file and not password:
            raise ValueError("the administrator password file is empty")
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", arguments.admin_username):
            raise ValueError(
                "administrator usernames require 1–80 letters, digits, or ._@-"
            )
        return RuntimeSettings(
            arguments.command,
            arguments.data_dir.absolute(),
            domain,
            auto_tls,
            arguments.tls_issuer,
            http_port,
            https_port,
            next(
                port
                for port in (8000, 8001, 8002)
                if port not in {http_port, https_port}
            ),
            origin,
            account_number(arguments.uid),
            account_number(arguments.gid),
            arguments.repair_storage,
            arguments.admin_username,
            password,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
