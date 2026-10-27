"""Shared API fixtures use temporary storage and no external network services."""

import html
import json
import re
import sqlite3

import pytest

from flaretrace import create_app


@pytest.fixture
def application(tmp_path, monkeypatch):
    """Create a configured Flask application with isolated test storage.

    Args:
        tmp_path: Pytest-provided temporary directory unique to this test.
        monkeypatch: Pytest helper that restores replaced environment values or attributes.

    Returns:
        Configured Flask application using this test’s temporary data directory.
    """
    for name in (
        "DOMAIN",
        "AUTO_TLS",
        "PUBLIC_BASE_URL",
        "SITE_ADDRESS",
        "TRUST_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path),
            "ADMIN_PASSWORD": "feature-test-password",
            "TRUST_PROXY": False,
        }
    )
    connection = sqlite3.connect(tmp_path / "trace.sqlite3")
    try:
        connection.execute(
            "INSERT INTO settings VALUES('homepage_redirect','https://example.com/')"
        )
        connection.commit()
    finally:
        connection.close()
    return app


@pytest.fixture
def database(application):
    """Open a row-oriented database connection for one test and close it afterwards.

    Args:
        application: Flask application configured with isolated storage and credentials.

    Yields:
        Open SQLite connection; the fixture closes it after use.
    """
    connection = sqlite3.connect(application.config["DATA_DIR"] + "/trace.sqlite3")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def client(application):
    """Create an authenticated administrator HTTP client for one test.

    Args:
        application: Flask application configured with isolated storage and credentials.

    Returns:
        Logged-in Flask test client with its own cookie jar.
    """
    client = application.test_client()
    client.get("/admin/login")
    with client.session_transaction() as state:
        token = state["csrf"]
    result = client.post(
        "/admin/login",
        data={"csrf": token, "username": "admin", "password": "feature-test-password"},
    )
    assert result.status_code == 302
    return client


@pytest.fixture
def csrf(client):
    """Read the authenticated client’s session-bound form token.

    Args:
        client: HTTP test client with an authenticated administrator session.

    Returns:
        CSRF token stored in the administrator session.
    """
    with client.session_transaction() as state:
        return state["csrf"]


@pytest.fixture
def make_link(client, csrf):
    """Provide a factory that creates research links through the administrator form.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.

    Returns:
        Callable accepting link settings and returning the created link identifier.
    """

    def create(**settings):
        """Submit an administrator form and return the newly created link identifier.

        Args:
            settings: Overrides applied to the link, application, or isolated test configuration.

        Returns:
            Identifier of the research link created by the administrator form.
        """
        result = client.post(
            "/admin/links/new",
            data={"csrf": csrf, "name": "Feature test", "expiry": "never", **settings},
        )
        assert result.status_code == 302, result.text
        return result.location.rsplit("/", 1)[-1]

    return create


@pytest.fixture
def make_visit(client, make_link):
    """Provide a factory that opens a public link and reads its signed capture configuration.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.

    Returns:
        Callable returning the capture configuration for a newly opened visit.
    """

    def visit(link=None, **request_options):
        """Open a research link and decode its signed browser capture configuration.

        Args:
            link: Research-link identifier receiving the synthetic visit; None creates a link.
            request_options: Keyword options forwarded to the Flask test-client request.

        Returns:
            Capture configuration containing the visit identifier and signed submission token.
        """
        result = client.get("/l/" + (link or make_link()), **request_options)
        assert result.status_code == 200
        return json.loads(
            html.unescape(re.search(r'data-config="([^"]+)"', result.text).group(1))
        )

    return visit
