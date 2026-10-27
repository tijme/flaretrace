"""Module containing integration test fixtures."""

import tempfile
import unittest
from flaretrace import create_app


class InitialSetupTests(unittest.TestCase):
    """Exercise initial setup tests with isolated application resources.

    Attributes:
        app: Flask application under test with temporary storage.
        client: HTTP client used for authenticated administrator requests.
        temporary_directory: TemporaryDirectory owner for database records, credentials, and media
            fixtures.
    """

    def setUp(self):
        """Create isolated storage and clients for each test case."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.temporary_directory.name,
                "ADMIN_PASSWORD": "initial-setup-password",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.temporary_directory.cleanup()

    def token(self):
        """Token for the isolated integration checks.

        Returns:
            Current session-bound CSRF token read from the test client.
        """
        with self.client.session_transaction() as state:
            return state["csrf"]

    def login(self):
        """Render the login form or authenticate administrator credentials.

        Returns:
            Zero-argument sign-in callable returning the authenticated browser page.
        """
        self.client.get("/admin/login")
        return self.client.post(
            "/admin/login",
            data={
                "csrf": self.token(),
                "username": "admin",
                "password": "initial-setup-password",
            },
        )

    def test_setup_gate_and_valid_completion(self):
        """Verify setup gate and valid completion."""
        self.assertEqual(self.client.get("/").data, b"")
        self.assertEqual(self.login().location, "/admin/initial-setup")
        for path in (
            "/admin",
            "/admin/templates",
            "/admin/links/new",
            "/admin/export",
            "/admin/setup",
        ):
            self.assertEqual(self.client.get(path).location, "/admin/initial-setup")
        for value in ("", "javascript:alert(1)", "http://localhost/"):
            result = self.client.post(
                "/admin/initial-setup",
                data={"csrf": self.token(), "homepage_redirect": value},
            )
            self.assertEqual(result.status_code, 400)
        result = self.client.post(
            "/admin/initial-setup",
            data={
                "csrf": self.token(),
                "homepage_redirect": "https://example.com/landing",
            },
        )
        self.assertEqual(result.location, "/admin")
        self.assertEqual(self.client.get("/").location, "https://example.com/landing")
        self.assertEqual(self.client.get("/admin/templates").status_code, 200)
        self.client.post("/admin/logout", data={"csrf": self.token()})
        self.assertEqual(self.login().location, "/admin")

    def test_setup_authentication_and_csrf(self):
        """Verify setup authentication and csrf."""
        self.assertEqual(
            self.client.get("/admin/initial-setup").location, "/admin/login"
        )
        self.login()
        self.assertEqual(
            self.client.post(
                "/admin/initial-setup",
                data={"homepage_redirect": "https://example.com/"},
            ).status_code,
            400,
        )
