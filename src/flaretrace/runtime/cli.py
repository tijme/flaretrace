"""Provide the single-image entrypoint, credential command, and container health probe."""

import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request

from .options import parse_settings
from .processes import supervise
from .proxy import caddy_configuration
from .storage import correct_owner, drop_privileges, prepare_storage

#: Container-private runtime metadata; no administrator password or signing key is stored here.
HEALTH_FILE = Path("/run/flaretrace/health.json")


def application_environment(settings):
    """Make Flask trust only the bundled loopback proxy and use the validated public origin.

    Args:
        settings: Immutable container settings, including optional explicit administrator credentials.

    Returns:
        Environment for both non-root child processes; secrets are never printed.
    """
    return dict(
        os.environ,
        DATA_DIR=str(settings.data_directory),
        ADMIN_USERNAME=settings.admin_username,
        ADMIN_PASSWORD=settings.admin_password,
        PUBLIC_BASE_URL=settings.public_origin,
        DOMAIN="",
        SITE_ADDRESS="",
        AUTO_TLS="0",
        TRUST_PROXY="1",
        XDG_DATA_HOME=str(settings.data_directory / "tls"),
        XDG_CONFIG_HOME=str(HEALTH_FILE.parent),
    )


def prepare_runtime(settings):
    """Repair persistent storage and write private runtime metadata before dropping root.

    Args:
        settings: Validated serving configuration with a dedicated data directory.

    Returns:
        Path to the generated, container-private Caddyfile.
    """
    os.umask(0o077)
    prepare_storage(
        settings.data_directory,
        settings.user_id,
        settings.group_id,
        settings.repair_storage,
    )
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.geteuid() == 0:
        correct_owner(HEALTH_FILE.parent, settings.user_id, settings.group_id)
    drop_privileges(settings.user_id, settings.group_id)
    configuration = HEALTH_FILE.parent / "Caddyfile"
    configuration.write_text(caddy_configuration(settings))
    HEALTH_FILE.write_text(
        json.dumps(
            {
                "http_port": settings.http_port,
                "backend_port": settings.backend_port,
                "data_directory": str(settings.data_directory),
                "admin_username": settings.admin_username,
                "generated_password": not bool(settings.admin_password),
            }
        )
    )
    return configuration


def serve(settings):
    """Start both required services with no Docker socket, host scripts, or external proxy.

    Args:
        settings: Validated settings used for both listeners and application initialization.

    Returns:
        Supervisor exit status; an unexpected child exit fails the container.

    Raises:
        RuntimeError: The Caddy executable is missing from the runtime image.
    """
    caddy = shutil.which("caddy")
    if not caddy:
        raise RuntimeError("Caddy is missing; use the published Flaretrace image")
    configuration = prepare_runtime(settings)
    commands = [
        [
            sys.executable,
            "-m",
            "gunicorn",
            "--bind",
            f"127.0.0.1:{settings.backend_port}",
            "--workers",
            "1",
            "--threads",
            "8",
            "--timeout",
            "90",
            "--graceful-timeout",
            "5",
            "--keep-alive",
            "1",
            "flaretrace.wsgi:app",
        ],
        [caddy, "run", "--config", str(configuration), "--adapter", "caddyfile"],
    ]
    return supervise(commands, application_environment(settings))


def healthcheck():
    """Probe both the private app and public proxy without using environment proxies.

    Returns:
        Zero only when both services return their actual SQLite-backed readiness response.
    """
    try:
        options = json.loads(HEALTH_FILE.read_text())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for port in (options["backend_port"], options["http_port"]):
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                if response.status != 200 or json.load(response) != {"status": "ok"}:
                    return 1
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 1


def credentials(settings):
    """Display the administrator username and existing generated password without changing either.

    Args:
        settings: Parsed settings identifying the persistent data directory.

    Returns:
        Zero after printing labeled username and password lines.

    Raises:
        RuntimeError: The running container uses an explicit password rather than its saved fallback.
    """
    directory = settings.data_directory
    username = settings.admin_username
    if HEALTH_FILE.exists():
        running = json.loads(HEALTH_FILE.read_text())
        if not running["generated_password"]:
            raise RuntimeError(
                "an explicit administrator password is configured; no generated password is in use"
            )
        directory = Path(running["data_directory"])
        username = running.get("admin_username", "admin")
    if settings.admin_password:
        raise RuntimeError("an explicit administrator password is configured")
    saved_password = (directory / "admin-password").read_text().strip()
    print(f"Username: {username}\nPassword: {saved_password}")
    return 0


def main(argv=None):
    """Dispatch the container command and report configuration failures without leaking secrets.

    Args:
        argv: Optional command arguments; None uses the current process arguments.

    Returns:
        Serving, credential-display, or readiness exit status.
    """
    settings = parse_settings(argv)
    try:
        if settings.command == "healthcheck":
            return healthcheck()
        if settings.command == "credentials":
            return credentials(settings)
        return serve(settings)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Flaretrace startup error: {error}", file=sys.stderr)
        return 1
