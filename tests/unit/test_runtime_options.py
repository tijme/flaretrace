"""Exercise container arguments, legacy defaults, safe ports, and credential validation."""

import pytest

from flaretrace.runtime.options import account_number, parse_settings, port_number
from flaretrace.runtime.proxy import caddy_configuration


def test_default_container_settings():
    """Use HTTP by default with non-root services and an isolated private backend port."""
    settings = parse_settings([], {})
    assert settings.command == "serve"
    assert (settings.http_port, settings.https_port, settings.backend_port) == (
        80,
        443,
        8000,
    )
    assert (settings.user_id, settings.group_id) == (10001, 10001)
    assert not settings.auto_tls and not settings.domain and not settings.public_origin
    assert str(settings.data_directory) == "/data"


@pytest.mark.parametrize("command", ["serve", "credentials", "healthcheck"])
def test_explicit_container_commands(command):
    """Accept service, credential, and readiness commands without a host launcher.

    Args:
        command: Supported operation performed inside the image.
    """
    assert parse_settings([command], {}).command == command


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", True),
        ("yes", True),
        ("TRUE", True),
        ("0", False),
        ("false", False),
        ("NO", False),
    ],
)
def test_tls_boolean_values(value, expected):
    """Preserve explicit boolean spellings previously accepted by the host launcher.

    Args:
        value: Explicit automatic-TLS value supplied on the command line.
        expected: Expected normalized boolean.
    """
    settings = parse_settings(
        ["--domain", "trace.example.com", "--auto-tls", value], {}
    )
    assert settings.auto_tls is expected


def test_arguments_override_environment_and_keep_public_ports():
    """Prefer explicit domain, TLS, listener, credential, and account arguments over defaults."""
    settings = parse_settings(
        [
            "--domain",
            "TRACE.EXAMPLE.COM.",
            "--auto-tls",
            "--http-port",
            "8080",
            "--https-port=8443",
            "--uid",
            "1000",
            "--gid",
            "1001",
            "--repair-storage",
            "--admin-username",
            "editor",
            "--admin-password",
            "explicit-strong-password",
        ],
        {"DOMAIN": "old.example.com", "AUTO_TLS": "0", "HTTP_PORT": "invalid"},
    )
    assert (
        settings.domain,
        settings.auto_tls,
        settings.http_port,
        settings.https_port,
    ) == ("trace.example.com", True, 8080, 8443)
    assert settings.public_origin == "https://trace.example.com:8443"
    assert (
        settings.user_id == 1000
        and settings.group_id == 1001
        and settings.repair_storage
    )
    assert (
        settings.admin_username == "editor"
        and settings.admin_password == "explicit-strong-password"
    )
    assert "explicit-strong-password" not in repr(settings)


def test_no_auto_tls_disables_environment_default():
    """Allow explicit HTTP even when the legacy environment enables TLS."""
    settings = parse_settings(
        ["--no-auto-tls"], {"DOMAIN": "trace.example.com", "AUTO_TLS": "1"}
    )
    assert (
        not settings.auto_tls and settings.public_origin == "http://trace.example.com"
    )


@pytest.mark.parametrize(
    "address, expected",
    [
        ("https://trace.example.com:9443", "https://trace.example.com:9443"),
        ("http://trace.example.com:8080", "http://trace.example.com:8080"),
        ("trace.example.com", "https://trace.example.com"),
    ],
)
def test_legacy_origins_remain_usable(address, expected):
    """Retain existing SITE_ADDRESS URLs when moving an installation to the single image.

    Args:
        address: Existing deployment origin supplied through the environment.
        expected: Public origin retaining its original protocol and custom port.
    """
    assert parse_settings([], {"SITE_ADDRESS": address}).public_origin == expected


def test_backend_port_never_collides_with_public_listeners():
    """Move Gunicorn to another loopback port when a custom public port uses its default."""
    assert (
        parse_settings(["--http-port", "8000", "--https-port", "8001"], {}).backend_port
        == 8002
    )


def test_explicit_listener_overrides_legacy_origin_port():
    """Let container arguments override a port embedded in an older environment origin."""
    settings = parse_settings(
        ["--https-port", "8443"], {"SITE_ADDRESS": "https://trace.example.com:9443"}
    )
    assert settings.https_port == 8443
    assert settings.public_origin == "https://trace.example.com:8443"


def test_internal_tls_is_explicit_and_external_origin_can_override():
    """Support a private development CA and reverse-proxy deployments without weakening public TLS."""
    settings = parse_settings(
        [
            "--domain",
            "localhost",
            "--auto-tls",
            "--tls-issuer",
            "internal",
            "--https-port",
            "8443",
        ],
        {},
    )
    assert settings.public_origin == "https://localhost:8443"
    assert "tls internal" in caddy_configuration(settings)
    public = parse_settings(
        [
            "--domain",
            "trace.example.com",
            "--public-base-url",
            "https://public.example.com:9443",
        ],
        {},
    )
    assert public.public_origin == "https://public.example.com:9443"


def test_password_file_is_read_without_exposing_it(tmp_path):
    """Load a mounted secret file and hide its contents from runtime-setting representations.

    Args:
        tmp_path: Temporary directory containing only a synthetic administrator credential.
    """
    secret = tmp_path / "password"
    secret.write_text("synthetic-secret-password\n")
    settings = parse_settings(["--admin-password-file", str(secret)], {})
    assert settings.admin_password == "synthetic-secret-password"
    assert settings.admin_password not in repr(settings)
    secret.write_text("")
    with pytest.raises(SystemExit):
        parse_settings(["--admin-password-file", str(secret)], {})


@pytest.mark.parametrize(
    "arguments",
    [
        ["--unknown"],
        ["--domain"],
        ["--domain", "https://example.com"],
        ["--domain", "bad..example.com"],
        ["--auto-tls"],
        ["--domain", "localhost", "--auto-tls"],
        ["--domain", "127.0.0.1", "--auto-tls"],
        ["--auto-tls=maybe"],
        ["--tls-issuer", "internal"],
        ["--http-port", "0"],
        ["--http-port", "65536"],
        ["--https-port", "x"],
        ["--http-port", "443"],
        ["--https-port", "80"],
        ["--uid", "0"],
        ["--gid", "-1"],
        ["--admin-username", "bad/name"],
        ["--admin-password", "short"],
        ["--admin-password-file", "/missing-secret"],
        [
            "--domain",
            "trace.example.com",
            "--auto-tls",
            "--public-base-url",
            "http://example.com",
        ],
    ],
)
def test_invalid_arguments_fail_before_storage_or_listeners(arguments):
    """Reject dangerous or inconsistent deployment input before any startup side effects.

    Args:
        arguments: Deliberately invalid container argument sequence.
    """
    with pytest.raises(SystemExit) as failure:
        parse_settings(arguments, {})
    assert failure.value.code == 2


@pytest.mark.parametrize("value", ["0", "65536", "abc", "80\n", "80:80", "-1"])
def test_invalid_port_number(value):
    """Reject malformed public listeners even when they originate from environment settings.

    Args:
        value: Non-decimal, out-of-range, or whitespace-suffixed port text.
    """
    with pytest.raises(ValueError):
        port_number(value)


@pytest.mark.parametrize("value", ["0", "-1", "abc", str(2**31)])
def test_invalid_account_number(value):
    """Reject root or malformed identities before the bootstrap changes ownership.

    Args:
        value: Invalid numeric serving account identifier.
    """
    with pytest.raises(ValueError):
        account_number(value)
