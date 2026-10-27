"""Module containing integration test fixtures."""

import tempfile
import unittest

from flaretrace import create_app


class AdministratorCredentialsTests(unittest.TestCase):
    """Exercise administrator credentials tests with isolated application resources.

    Attributes:
        directory: TemporaryDirectory owner for this test installation; cleaned up after use.
    """

    def setUp(self):
        """Create isolated storage and clients for each test case."""
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.directory.cleanup()

    def application(self, **overrides):
        """Create a configured Flask application with isolated test storage.

        Args:
            overrides: Explicit configuration values applied to the temporary application.

        Returns:
            Configured Flask application using this test’s temporary data directory.
        """
        config = {
            "TESTING": True,
            "DATA_DIR": self.directory.name,
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "a-long-test-password",
        }
        config.update(overrides)
        app = create_app(config)
        import sqlite3
        from pathlib import Path

        with sqlite3.connect(
            Path(self.directory.name) / "trace.sqlite3"
        ) as database_connection:
            database_connection.execute(
                "INSERT OR REPLACE INTO settings VALUES('homepage_redirect','https://example.com/')"
            )
        return app

    def login(self, client, username="admin", password="a-long-test-password"):
        """Render the login form or authenticate administrator credentials.

        Args:
            client: HTTP test client with an authenticated administrator session.
            username: Administrator username supplied to the login form.
            password: Administrator password for the temporary test installation only.

        Returns:
            Zero-argument sign-in callable returning the authenticated browser page.
        """
        client.get("/admin/login")
        with client.session_transaction() as session:
            token = session["csrf"]
        return client.post(
            "/admin/login",
            data={"csrf": token, "username": username, "password": password},
        )

    def test_default_and_custom_username(self):
        """Verify default and custom username."""
        for username in ("admin", "operator.one@example.com"):
            with self.subTest(username=username):
                application = self.application(ADMIN_USERNAME=username)
                client = application.test_client()
                self.assertEqual(self.login(client, username).status_code, 302)
                self.assertEqual(client.get("/admin").status_code, 200)
                with client.session_transaction() as session:
                    self.assertEqual(session["admin_username"], username)
                    self.assertNotIn("a-long-test-password", str(dict(session)))

    def test_missing_wrong_and_unicode_username_rejected(self):
        """Verify missing wrong and unicode username rejected."""
        for username in ("", "Admin", "wrong", "\u2603"):
            with self.subTest(username=username):
                client = self.application().test_client()
                response = self.login(client, username)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Incorrect username or password", response.text)
                self.assertEqual(client.get("/admin").status_code, 302)

    def test_wrong_password_rejected(self):
        """Verify wrong password rejected."""
        client = self.application().test_client()
        response = self.login(client, password="wrong-password")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/admin").status_code, 302)

    def test_invalid_username_configuration(self):
        """Verify invalid username configuration."""
        for username in ("", "admin user", "admin\n", "\u2603", "x" * 81, None):
            with self.subTest(username=username):
                with self.assertRaisesRegex(ValueError, "ADMIN_USERNAME"):
                    self.application(ADMIN_USERNAME=username)

    def test_generated_password_works_with_default_username(self):
        """Verify generated password works with default username."""
        application = self.application(ADMIN_PASSWORD="")
        from pathlib import Path

        password = (Path(self.directory.name) / "admin-password").read_text().strip()
        client = application.test_client()
        self.assertEqual(self.login(client, password=password).status_code, 302)
        self.assertEqual(client.get("/admin").status_code, 200)

    def test_omitted_credentials_generate_private_persistent_password(self):
        """Verify omitted credentials generate private persistent password."""
        from pathlib import Path
        from unittest.mock import patch
        import os
        import stat

        config = {"TESTING": True, "DATA_DIR": self.directory.name}
        with patch.dict(os.environ, {"ADMIN_USERNAME": "", "ADMIN_PASSWORD": ""}):
            first = create_app(config)
            password_file = Path(self.directory.name) / "admin-password"
            password = password_file.read_text().strip()
            self.assertGreaterEqual(len(password), 32)
            self.assertEqual(stat.S_IMODE(password_file.stat().st_mode), 0o600)
            first_client = first.test_client()
            self.assertEqual(
                self.login(first_client, password=password).status_code, 302
            )
            with first_client.session_transaction() as session:
                self.assertEqual(session["admin_username"], "admin")
            restarted = create_app(config)
            self.assertEqual(password_file.read_text().strip(), password)
            self.assertEqual(
                self.login(restarted.test_client(), password=password).status_code, 302
            )
            # Fresh installations receive a different generated secret.
            with tempfile.TemporaryDirectory() as other:
                create_app({"TESTING": True, "DATA_DIR": other})
                self.assertNotEqual(
                    (Path(other) / "admin-password").read_text().strip(), password
                )

    def test_custom_password_does_not_create_generated_password_file(self):
        """Verify custom password does not create generated password file."""
        from pathlib import Path

        application = self.application(
            ADMIN_USERNAME="operator", ADMIN_PASSWORD="a-long-custom-password"
        )
        self.assertFalse((Path(self.directory.name) / "admin-password").exists())
        self.assertEqual(
            self.login(
                application.test_client(),
                username="operator",
                password="a-long-custom-password",
            ).status_code,
            302,
        )

    def test_legacy_session_flag_is_insufficient(self):
        """Verify legacy session flag is insufficient."""
        client = self.application().test_client()
        with client.session_transaction() as session:
            session["admin"] = True
        self.assertEqual(client.get("/admin").status_code, 302)

    def test_credential_rotation_invalidates_existing_cookie(self):
        """Verify credential rotation invalidates existing cookie."""
        first = self.application()
        client = first.test_client()
        self.login(client)
        cookie = client.get_cookie("session").value
        unchanged = self.application().test_client()
        unchanged.set_cookie("session", cookie)
        self.assertEqual(unchanged.get("/admin").status_code, 200)
        for changed in (
            {"ADMIN_USERNAME": "operator"},
            {"ADMIN_PASSWORD": "a-different-long-password"},
        ):
            with self.subTest(changed=tuple(changed)):
                rotated = self.application(**changed).test_client()
                rotated.set_cookie("session", cookie)
                self.assertEqual(rotated.get("/admin").status_code, 302)


if __name__ == "__main__":
    unittest.main()
