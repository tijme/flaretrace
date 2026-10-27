"""Module containing integration test fixtures."""

import html
import re
import tempfile
import unittest
from flaretrace import create_app
from flaretrace.links.templates import DEFAULT_TEMPLATE, validate_template


class UpdateTests(unittest.TestCase):
    """Exercise update tests with isolated application resources.

    Attributes:
        app: Flask application under test with temporary storage.
        client: HTTP client used for authenticated administrator requests.
        csrf: Session-bound administrator form token.
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
                "ADMIN_PASSWORD": "update-test-password",
            }
        )
        import sqlite3
        from pathlib import Path

        with sqlite3.connect(
            Path(self.temporary_directory.name) / "trace.sqlite3"
        ) as database_connection:
            database_connection.execute(
                "INSERT OR REPLACE INTO settings VALUES('homepage_redirect','https://example.com/')"
            )
        self.client = self.app.test_client()
        self.client.get("/admin/login")
        with self.client.session_transaction() as session:
            csrf = session["csrf"]
        self.client.post(
            "/admin/login",
            data={
                "csrf": csrf,
                "username": "admin",
                "password": "update-test-password",
            },
        )
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.temporary_directory.cleanup()

    def test_homepage_setting(self):
        """Verify homepage setting."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.client.post(
            "/admin/setup",
            data={"csrf": self.csrf, "homepage_redirect": "https://example.com/"},
        )
        self.assertEqual(self.client.get("/").location, "https://example.com/")
        self.client.post(
            "/admin/setup",
            data={"csrf": self.csrf, "homepage_redirect": "javascript:bad"},
        )
        self.assertEqual(self.client.get("/").location, "https://example.com/")
        self.client.post(
            "/admin/setup", data={"csrf": self.csrf, "homepage_redirect": ""}
        )
        self.assertEqual(self.client.get("/").data, b"")

    def test_default_editor_and_network_details(self):
        """Verify default editor and network details."""
        response = self.client.get("/admin/templates/new")
        self.assertIn('id="enable-button"', html.unescape(response.text))
        self.assertEqual(validate_template(DEFAULT_TEMPLATE), DEFAULT_TEMPLATE)
        response = self.client.post(
            "/admin/links/new", data={"csrf": self.csrf, "name": "Test", "expiry": "7"}
        )
        slug = response.location.rsplit("/", 1)[-1]
        response = self.client.get(
            "/l/" + slug,
            headers={
                "X-Forwarded-For": "203.0.113.2, 203.0.113.3",
                "X-Real-IP": "203.0.113.2",
            },
        )
        self.assertNotIn('class="link-template-content"', response.text)
        rows = self.client.get("/admin/export").json
        self.assertEqual(
            rows[0]["headers"]["X-Forwarded-For"], "203.0.113.2, 203.0.113.3"
        )
        self.assertEqual(rows[0]["headers"]["Transport-Peer-IP"], "127.0.0.1")
        self.assertIn(
            "203.0.113.3", self.client.get("/admin/visits/" + rows[0]["id"]).text
        )

    def test_mutable_public_segment_preserves_visits(self):
        """Verify mutable public segment preserves visits."""
        response = self.client.post(
            "/admin/links/new",
            data={
                "csrf": self.csrf,
                "name": "Segment",
                "expiry": "7",
                "public_slug": "my-link",
            },
        )
        internal = response.location.rsplit("/", 1)[-1]
        self.assertEqual(self.client.get("/l/my-link").status_code, 200)
        self.assertEqual(self.client.get("/l/" + internal).status_code, 404)
        result = self.client.post(
            "/admin/links/" + internal + "/segment",
            data={"csrf": self.csrf, "public_slug": "renamed"},
        )
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.client.get("/l/my-link").status_code, 200)
        self.assertEqual(self.client.get("/l/renamed").status_code, 404)
        self.assertNotIn(
            "Update share URL", self.client.get("/admin/links/" + internal).text
        )
        other = self.client.post(
            "/admin/links/new",
            data={
                "csrf": self.csrf,
                "name": "Duplicate",
                "expiry": "7",
                "public_slug": "my-link",
            },
        )
        self.assertEqual(other.status_code, 400)

    def test_optional_ip_enrichment_cached(self):
        """Verify optional ip enrichment cached."""
        import json
        from unittest.mock import patch, MagicMock

        response = self.client.post(
            "/admin/links/new", data={"csrf": self.csrf, "name": "IP", "expiry": "7"}
        )
        slug = response.location.rsplit("/", 1)[-1]
        self.client.get("/l/" + slug, environ_base={"REMOTE_ADDR": "8.8.8.8"})
        record = self.client.get("/admin/export").json[0]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "success": True,
                "country": "Example",
                "connection": {"asn": 123, "isp": "Test ISP"},
            }
        ).encode()
        with patch(
            "flaretrace.visits.enrichment.urllib.request.urlopen", return_value=response
        ) as lookup:
            self.client.post(
                "/admin/visits/" + record["id"] + "/enrich", data={"csrf": self.csrf}
            )
            self.client.post(
                "/admin/visits/" + record["id"] + "/enrich", data={"csrf": self.csrf}
            )
            self.assertEqual(lookup.call_count, 1)
        enriched = self.client.get("/admin/export").json[0]
        self.assertEqual(enriched["ip_info"]["isp"], "Test ISP")
        self.assertIn("Test ISP", self.client.get("/admin/visits/" + record["id"]).text)

    def test_google_maps_link(self):
        """Verify google maps link."""
        response = self.client.post(
            "/admin/links/new", data={"csrf": self.csrf, "name": "Map", "expiry": "7"}
        )
        slug = response.location.rsplit("/", 1)[-1]
        page = self.client.get("/l/" + slug).text
        import json

        config = json.loads(
            html.unescape(re.search(r'data-config="([^"]+)"', page).group(1))
        )
        self.client.post(
            "/api/visits/" + config["id"] + "/events",
            json={
                "kind": "location",
                "data": {"latitude": 51.5, "longitude": 4.5, "accuracy": 10},
            },
            headers={"X-Visit-Token": config["token"]},
        )
        self.assertIn(
            "https://www.google.com/maps/search/",
            self.client.get("/admin/visits/" + config["id"]).text,
        )
