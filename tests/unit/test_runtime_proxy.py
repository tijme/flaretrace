"""Validate generated Caddy listeners and real parser compatibility without public ACME traffic."""

import json
import os
import subprocess

import pytest

from flaretrace.runtime.options import parse_settings
from flaretrace.runtime.proxy import caddy_configuration
from flaretrace.links.templates import MAX_TEMPLATE_REQUEST_BYTES


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ([], ":80"),
        (["--http-port", "8080"], ":8080"),
        (
            ["--domain", "trace.example.com", "--auto-tls"],
            "https://trace.example.com{uri}",
        ),
        (
            ["--domain", "trace.example.com", "--auto-tls", "--https-port", "8443"],
            "https://trace.example.com:8443{uri}",
        ),
    ],
)
def test_generated_listener_and_redirect(arguments, expected):
    """Generate only validated listeners and preserve HTTPS ports in public redirect targets.

    Args:
        arguments: Container deployment arguments for HTTP or managed HTTPS.
        expected: Listener or redirect substring required by this deployment.
    """
    configuration = caddy_configuration(parse_settings(arguments, {}))
    assert expected in configuration
    assert "127.0.0.1:8000" in configuration
    assert "admin off" in configuration
    assert f"max_size {MAX_TEMPLATE_REQUEST_BYTES}" in configuration
    if "--auto-tls" not in arguments:
        assert "redir" not in configuration
    else:
        assert "handle /healthz" in configuration and "issuer acme" in configuration


@pytest.mark.skipif(
    not os.environ.get("CADDY_BINARY"),
    reason="Real Caddy is included in the container test image",
)
@pytest.mark.parametrize(
    "arguments, listeners",
    [
        ([], {":80"}),
        (["--http-port", "8080"], {":8080"}),
        (["--domain", "trace.example.com", "--auto-tls"], {":80", ":443"}),
        (
            [
                "--domain",
                "trace.example.com",
                "--auto-tls",
                "--http-port",
                "8080",
                "--https-port",
                "8443",
            ],
            {":8080", ":8443"},
        ),
        (
            ["--domain", "localhost", "--auto-tls", "--tls-issuer", "internal"],
            {":80", ":443"},
        ),
    ],
)
def test_real_caddy_adapter_accepts_configuration(tmp_path, arguments, listeners):
    """Parse the actual generated configuration with Caddy without starting certificate requests.

    Args:
        tmp_path: Temporary directory for the generated private Caddyfile.
        arguments: Validated runtime arguments selecting listeners and TLS issuer.
        listeners: Required public socket addresses in Caddy's adapted JSON.
    """
    configuration = tmp_path / "Caddyfile"
    configuration.write_text(caddy_configuration(parse_settings(arguments, {})))
    result = subprocess.run(
        [
            os.environ["CADDY_BINARY"],
            "adapt",
            "--adapter",
            "caddyfile",
            "--config",
            str(configuration),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    servers = json.loads(result.stdout)["apps"]["http"]["servers"]
    assert {
        listener for server in servers.values() for listener in server["listen"]
    } == listeners
