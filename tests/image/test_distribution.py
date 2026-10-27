"""Exercise the real single-image HTTP/TLS distribution with isolated Compose services and volumes."""

import html
import http.cookiejar
import json
import os
from pathlib import Path
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

import pytest

from tests.ci.container_fixtures import ADMIN_PASSWORD, SIGNING_KEY


def http_client(context=None):
    """Create a private HTTP session that never sends fixture traffic through environment proxies.

    Args:
        context: Optional verified TLS context trusting the disposable Caddy development CA.

    Returns:
        URL opener with isolated administrator cookies and optional certificate verification.
    """
    handlers = [
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    ]
    if context:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def wait_until_ready(origin, opener):
    """Bound startup waits while retaining real HTTP and verified HTTPS readiness checks.

    Args:
        origin: Private-network origin of a disposable production-image service.
        opener: HTTP session whose TLS verification policy must remain enabled.

    Raises:
        AssertionError: The production service fails to become healthy within 45 seconds.
    """
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with opener.open(origin + "/healthz", timeout=2) as response:
                if response.status == 200 and json.load(response) == {"status": "ok"}:
                    return
        except (OSError, ValueError):
            time.sleep(0.2)
    raise AssertionError(f"Production image did not become ready: {origin}")


def form_token(opener, origin, path="/admin/login"):
    """Read an actual session-bound CSRF token from a server-rendered administrator page.

    Args:
        opener: Isolated HTTP client retaining its own session cookie.
        origin: Production-image origin under test.
        path: Administrator form containing the token.

    Returns:
        Escaped token decoded into the value required by the matching form submission.
    """
    with opener.open(origin + path, timeout=5) as response:
        source = response.read().decode()
    return html.unescape(re.search(r'name="csrf" value="([^"]+)"', source).group(1))


def submit_form(opener, origin, path, fields):
    """Submit one administrator form and decode its final same-origin response.

    Args:
        opener: Administrator session owning the submitted CSRF token.
        origin: Production-image origin under test.
        path: Administrator form action.
        fields: Form field names and values, including the session-bound token.

    Returns:
        Pair of final response URL and UTF-8 page text.
    """
    encoded = urllib.parse.urlencode(fields).encode()
    with opener.open(origin + path, data=encoded, timeout=5) as response:
        return response.url, response.read().decode()


@pytest.fixture(scope="module")
def evidence():
    """Locate the disposable volumes mounted read-only into the image-test runner.

    Returns:
        Root directory containing HTTP, TLS, and stale-ownership fixture volumes.
    """
    return Path(os.environ["SUT_EVIDENCE"])


@pytest.mark.parametrize("service", ["HTTP", "REPAIR"])
def test_image_serves_health_login_and_bundled_assets(service):
    """Serve real pages, JavaScript, styles, and fonts through the proxy without host files.

    Args:
        service: Fresh or restored production-image fixture to probe.
    """
    origin = os.environ["SUT_" + service]
    opener = http_client()
    wait_until_ready(origin, opener)
    for route in (
        "/admin/login",
        "/static/app.css",
        "/static/admin.js",
        "/static/capture.js",
        "/static/fonts/dm-sans-variable.ttf",
    ):
        with opener.open(origin + route, timeout=5) as response:
            assert response.status == 200 and response.read()
            assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_stale_ownership_is_repaired_without_data_loss(evidence):
    """Boot despite UID-1000 credentials and misleading markers, preserving every saved value.

    Args:
        evidence: Read-only mount of the isolated production-image data volumes.
    """
    wait_until_ready(os.environ["SUT_REPAIR"], http_client())
    directory = evidence / "repair"
    assert (directory / "session-key").read_text().strip() == SIGNING_KEY
    assert (directory / "admin-password").read_text().strip() == ADMIN_PASSWORD
    assert (
        directory / "media/synthetic.txt"
    ).read_text().strip() == "preserved evidence"
    for name in (
        "session-key",
        "admin-password",
        "trace.sqlite3",
        ".ownership-v1",
        ".ownership-v2",
        "media/synthetic.txt",
    ):
        assert (directory / name).stat().st_uid == 10001
    assert (directory / "session-key").stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700


def test_backend_is_not_publicly_listening():
    """Keep Gunicorn unreachable from other containers while the bundled public proxy works."""
    wait_until_ready(os.environ["SUT_HTTP"], http_client())
    with pytest.raises(OSError):
        socket.create_connection(("http", 8000), timeout=2)


@pytest.mark.parametrize(
    "tls_environment, redirect_environment, volume",
    [("TLS", "REDIRECT", "tls"), ("TLS_DEFAULT", "REDIRECT_DEFAULT", "tls-default")],
)
def test_verified_https_and_configured_port_redirect(
    evidence, tls_environment, redirect_environment, volume
):
    """Verify real TLS and redirects on default and custom ports without added privileges.

    Args:
        evidence: Read-only certificate storage generated by the actual non-root Caddy process.
        tls_environment: SUT_ environment suffix locating the verified HTTPS listener.
        redirect_environment: SUT_ environment suffix locating its plain HTTP listener.
        volume: Read-only evidence subdirectory containing this service's development CA.
    """
    tls_origin = os.environ["SUT_" + tls_environment]
    redirect_origin = os.environ["SUT_" + redirect_environment]
    wait_until_ready(redirect_origin, http_client())
    certificate = evidence / volume / "tls/pki/authorities/local/root.crt"
    context = ssl.create_default_context(cafile=str(certificate))
    opener = http_client(context)
    wait_until_ready(tls_origin, opener)
    with opener.open(redirect_origin + "/admin/login?source=ci", timeout=5) as response:
        assert response.url == tls_origin + "/admin/login?source=ci"
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert certificate.stat().st_uid == 10001
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_full_investigation_flow_through_bundled_proxy(evidence):
    """Authenticate, configure, create, visit, submit signed evidence, and export through Caddy.

    Args:
        evidence: Isolated volume containing the generated administrator credential.
    """
    origin = os.environ["SUT_HTTP"]
    opener = http_client()
    wait_until_ready(origin, opener)
    csrf = form_token(opener, origin)
    password = (evidence / "http/admin-password").read_text().strip()
    url, source = submit_form(
        opener,
        origin,
        "/admin/login",
        {"csrf": csrf, "username": "admin", "password": password},
    )
    assert url.endswith("/admin/initial-setup")
    csrf = form_token(opener, origin, "/admin/initial-setup")
    submit_form(
        opener,
        origin,
        "/admin/initial-setup",
        {"csrf": csrf, "homepage_redirect": "https://example.com/"},
    )
    url, source = submit_form(
        opener,
        origin,
        "/admin/links/new",
        {"csrf": csrf, "name": "Single image", "expiry": "never"},
    )
    identifier = url.rsplit("/", 1)[-1]
    request = urllib.request.Request(
        origin + "/l/" + identifier, headers={"X-Forwarded-For": "198.51.100.99"}
    )
    with opener.open(request, timeout=5) as response:
        config = json.loads(
            html.unescape(
                re.search(r'data-config="([^"]+)"', response.read().decode()).group(1)
            )
        )
    event = urllib.request.Request(
        origin + "/api/visits/" + config["id"] + "/events",
        data=json.dumps(
            {"kind": "browser", "data": {"language": "synthetic-image-test"}}
        ).encode(),
        headers={"Content-Type": "application/json", "X-Visit-Token": config["token"]},
    )
    with opener.open(event, timeout=5) as response:
        assert json.load(response) == {"ok": True}
    with opener.open(origin + "/admin/export", timeout=5) as response:
        records = json.load(response)
    visit = next(record for record in records if record["id"] == config["id"])
    assert visit["browser"]["language"] == "synthetic-image-test"
    assert visit["ip"] != "198.51.100.99"
    assert (evidence / "http/trace.sqlite3").stat().st_uid == 10001
