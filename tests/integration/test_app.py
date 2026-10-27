"""Module containing integration test fixtures."""

import html
import json
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flaretrace import create_app


class FlaretraceTests(unittest.TestCase):
    """Exercise flaretrace tests with isolated application resources.

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
                "ADMIN_PASSWORD": "a-long-test-password",
                "TRUST_PROXY": False,
            }
        )
        self.client = self.app.test_client()
        self.login()

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.temporary_directory.cleanup()

    def csrf(self, client=None):
        """Read the authenticated client’s session-bound form token.

        Args:
            client: HTTP test client with an authenticated administrator session.

        Returns:
            CSRF token stored in the administrator session.
        """
        with (client or self.client).session_transaction() as sess:
            return sess["csrf"]

    def login(self):
        """Render the login form or authenticate administrator credentials."""
        with self.database() as configured:
            configured.execute(
                "INSERT OR REPLACE INTO settings VALUES('homepage_redirect','https://example.com/')"
            )
        self.client.get("/admin/login")
        response = self.client.post(
            "/admin/login",
            data={
                "csrf": self.csrf(),
                "username": "admin",
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def link(self, name="Test link", destination=""):
        """Link for the isolated integration checks.

        Args:
            name: Name of the option, entity, or field being processed.
            destination: Optional HTTP(S) redirect saved on the synthetic research link.

        Returns:
            Identifier of the synthetic research link created through the administrator form.
        """
        response = self.client.post(
            "/admin/links/new",
            data={
                "csrf": self.csrf(),
                "name": name,
                "destination": destination,
                "expiry": "7",
            },
        )
        self.assertEqual(response.status_code, 302)
        return response.location.rsplit("/", 1)[-1]

    def visit(self, slug=None, client=None, headers=None):
        """Open a research link and decode its signed browser capture configuration.

        Args:
            slug: Public research-link identifier stored in the links table.
            client: HTTP test client with an authenticated administrator session.
            headers: Additional HTTP headers supplied to the synthetic visitor request.

        Returns:
            Capture configuration containing the visit identifier and signed submission token.
        """
        response = (client or self.client).get(
            "/l/" + (slug or self.link()), headers=headers or {}
        )
        self.assertEqual(response.status_code, 200)
        match = re.search(r'data-config="([^"]+)"', response.text)
        return json.loads(html.unescape(match.group(1)))

    def event(self, config, kind, data, **kwargs):
        """Event for the isolated integration checks.

        Args:
            config: Signed capture configuration containing the visit identifier and submission token.
            kind: Validated event category recorded alongside the JSON payload.
            data: JSON event details.
            kwargs: Keyword arguments forwarded unchanged to the wrapped callable.

        Returns:
            Flask test response returned by the signed event-submission endpoint.
        """
        headers = {"X-Visit-Token": config["token"]}
        headers.update(kwargs.pop("headers", {}))
        return self.client.post(
            f"/api/visits/{config['id']}/events",
            json={"kind": kind, "data": data},
            headers=headers,
            **kwargs,
        )

    def upload(
        self,
        config,
        kind="photo",
        facing="front",
        data=b"\xff\xd8\xfftest",
        mime="image/jpeg",
    ):
        """Upload for the isolated integration checks.

        Args:
            config: Signed capture configuration containing the visit identifier and submission token.
            kind: Validated event category recorded alongside the JSON payload.
            facing: Front or rear camera identifier attached to the synthetic capture.
            data: Synthetic media bytes; no real camera evidence is used.
            mime: MIME type declared by the synthetic media upload.

        Returns:
            Flask test response returned by the signed media-upload endpoint.
        """
        return self.client.post(
            f"/api/visits/{config['id']}/media?kind={kind}&facing={facing}",
            data=data,
            content_type=mime,
            headers={"X-Visit-Token": config["token"]},
        )

    def database(self):
        """Open a row-oriented connection to the isolated test installation.

        Returns:
            New row-oriented SQLite connection; the caller must close it after use.
        """
        connection = sqlite3.connect(
            Path(self.temporary_directory.name) / "trace.sqlite3"
        )
        connection.row_factory = sqlite3.Row
        return connection

    def test_admin_auth_csrf_and_secret_redaction(self):
        """Verify admin auth csrf and secret redaction."""
        visitor = self.app.test_client()
        self.assertEqual(visitor.get("/admin").status_code, 302)
        self.assertEqual(visitor.get("/admin/export").status_code, 302)
        self.assertEqual(
            self.client.post("/admin/links/new", data={"name": "x"}).status_code, 400
        )
        slug = self.link()
        visit = self.visit(
            slug,
            visitor,
            {
                "Authorization": "Bearer secret",
                "X-Api-Key": "secret",
                "Referer": "https://example.com/path?password=secret#secret",
                "User-Agent": "Test UA",
            },
        )
        row = self.client.get("/admin/export").json[0]
        self.assertEqual(row["referrer"], "https://example.com/path")
        self.assertNotIn("secret", json.dumps(row))
        self.assertEqual(row["user_agent"], "Test UA")
        self.assertEqual(
            self.event(visit, "browser", {"language": "en"}).status_code, 200
        )

    def test_full_visit_media_authorization_and_deletion(self):
        """Verify full visit media authorization and deletion."""
        config = self.visit()
        self.assertEqual(
            self.event(config, "browser", {"timezone": "UTC"}).status_code, 200
        )
        self.assertEqual(
            self.event(
                config, "location", {"latitude": 51.5, "longitude": -0.1, "accuracy": 5}
            ).status_code,
            200,
        )
        saved = self.upload(config)
        self.assertEqual(saved.status_code, 201)
        media_id = saved.json["id"]
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get(f"/admin/media/{media_id}").status_code, 302)
        with self.client.get(f"/admin/media/{media_id}") as response:
            self.assertEqual(response.data, b"\xff\xd8\xfftest")
        for path in (
            "/admin",
            "/admin/visits",
            "/admin/setup",
            f"/admin/visits/{config['id']}",
            "/admin/export?format=csv",
        ):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(
            self.event(config, "finished", {"reason": "complete"}).status_code, 200
        )
        self.assertEqual(self.upload(config, facing="rear").status_code, 409)
        self.client.post(
            f"/admin/visits/{config['id']}/delete", data={"csrf": self.csrf()}
        )
        self.assertEqual(
            len(list((Path(self.temporary_directory.name) / "media").iterdir())), 0
        )
        self.assertEqual(self.client.get("/admin/export").json, [])

    def test_tokens_origins_and_input_validation(self):
        """Verify tokens origins and input validation."""
        config = self.visit()
        self.assertEqual(
            self.client.post(
                f"/api/visits/{config['id']}/events",
                json={"kind": "browser", "data": {}},
            ).status_code,
            403,
        )
        another = self.visit()
        fake = {"id": config["id"], "token": another["token"]}
        self.assertEqual(self.event(fake, "browser", {}).status_code, 403)
        self.assertEqual(
            self.event(
                config, "browser", {}, headers={"Origin": "https://evil.example"}
            ).status_code,
            403,
        )
        for location in (
            {"latitude": 91, "longitude": 0, "accuracy": 1},
            {"latitude": None, "longitude": 1, "accuracy": 1},
            {"latitude": True, "longitude": 1, "accuracy": 1},
            {"latitude": 1, "longitude": 1, "accuracy": -1},
        ):
            self.assertEqual(self.event(config, "location", location).status_code, 400)
        self.assertEqual(self.event(config, [], {}).status_code, 400)
        self.assertEqual(self.upload(config, data=b"not a JPEG").status_code, 415)
        self.assertEqual(self.upload(config, mime="text/html").status_code, 415)
        self.assertEqual(
            self.upload(config, data=b"x" * (8 * 1024 * 1024 + 1)).status_code, 413
        )

    def test_diagnostics_store_only_valid_structured_values(self):
        """Verify diagnostics store only valid structured values."""
        config = self.visit()
        diagnostics = [
            {"stage": "recording", "outcome": "unsupported"},
            {
                "stage": "upload",
                "outcome": "success",
                "mediaKind": "photo",
                "facing": "front",
                "format": "image/jpeg",
                "durationMs": 0,
                "bytes": 0,
                "httpStatus": 100,
            },
            {
                "stage": "recording",
                "outcome": "error",
                "mediaKind": "video",
                "facing": "rear",
                "format": "video/webm",
                "durationMs": 5000.5,
                "error": "InvalidStateError",
            },
            {
                "stage": "upload",
                "outcome": "error",
                "mediaKind": "video",
                "format": "video/mp4",
                "durationMs": 300000,
                "bytes": 8388608,
                "httpStatus": 599,
                "error": "UploadError",
            },
        ]
        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(
                    self.event(config, "diagnostic", diagnostic).status_code, 200
                )
        with self.database() as connection:
            saved = connection.execute(
                "SELECT kind,payload FROM events WHERE visit_id=? ORDER BY id",
                (config["id"],),
            ).fetchall()
        self.assertEqual(
            [row["kind"] for row in saved], ["diagnostic"] * len(diagnostics)
        )
        self.assertEqual([json.loads(row["payload"]) for row in saved], diagnostics)

    def test_diagnostic_rejects_unknown_fields_missing_fields_and_invalid_enums(self):
        """Verify diagnostic rejects unknown fields missing fields and invalid enums."""
        config = self.visit()
        valid = {"stage": "upload", "outcome": "error"}
        invalid = [{}, {"stage": "upload"}, {"outcome": "error"}]
        invalid.extend(
            {**valid, key: "https://example.com/private?token=secret"}
            for key in ("message", "url", "stack", "reason", "unexpected")
        )
        invalid_values = {
            "stage": "camera",
            "outcome": "failure",
            "mediaKind": "audio",
            "facing": "environment",
            "format": "text/plain",
            "error": "Camera failed at https://example.com/private",
        }
        for key, value in invalid_values.items():
            invalid.extend(
                {**valid, key: value} for value in (value, "", None, True, 1, [], {})
            )
        for diagnostic in invalid:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(
                    self.event(config, "diagnostic", diagnostic).status_code, 400
                )
        with self.database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE visit_id=?", (config["id"],)
                ).fetchone()[0],
                0,
            )

    def test_diagnostic_rejects_non_finite_boolean_and_out_of_range_numbers(self):
        """Verify diagnostic rejects non finite boolean and out of range numbers."""
        config = self.visit()
        invalid_numbers = {
            "durationMs": (
                True,
                False,
                None,
                "5",
                [],
                {},
                float("nan"),
                float("inf"),
                float("-inf"),
                -1,
                300001,
                10**1000,
            ),
            "bytes": (
                True,
                False,
                None,
                "5",
                [],
                {},
                float("nan"),
                float("inf"),
                -1,
                8388609,
                1.0,
            ),
            "httpStatus": (
                True,
                False,
                None,
                "200",
                [],
                {},
                float("nan"),
                float("inf"),
                99,
                600,
                200.0,
            ),
        }
        for field, values in invalid_numbers.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    diagnostic = {"stage": "upload", "outcome": "success", field: value}
                    self.assertEqual(
                        self.event(config, "diagnostic", diagnostic).status_code, 400
                    )
        with self.database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE visit_id=?", (config["id"],)
                ).fetchone()[0],
                0,
            )

    def test_error_events_accept_only_known_codes(self):
        """Verify error events accept only known codes."""
        config = self.visit()
        codes = (
            "NotAllowedError",
            "NotFoundError",
            "NotReadableError",
            "OverconstrainedError",
            "SecurityError",
            "InvalidStateError",
            "NotSupportedError",
            "AbortError",
            "TimeoutError",
            "NetworkError",
            "UnknownError",
            "UploadError",
            "permission_denied",
            "position_unavailable",
            "location_timeout",
            "insecure_context",
        )
        expected = []
        for code in codes:
            events = (
                ("permission", {"feature": "camera", "state": "error", "reason": code}),
                ("capture_error", {"facing": "rear", "reason": code}),
                (
                    "diagnostic",
                    {"stage": "recording", "outcome": "error", "error": code},
                ),
            )
            for kind, data in events:
                with self.subTest(kind=kind, code=code):
                    self.assertEqual(self.event(config, kind, data).status_code, 200)
                expected.append((kind, data))
        for data in (
            {"feature": "location", "state": "granted"},
            {"feature": "location", "state": "denied", "reason": ""},
        ):
            self.assertEqual(self.event(config, "permission", data).status_code, 200)
            expected.append(("permission", data))
        front_error = {"facing": "front", "reason": "NotAllowedError"}
        self.assertEqual(
            self.event(config, "capture_error", front_error).status_code, 200
        )
        expected.append(("capture_error", front_error))
        with self.database() as connection:
            saved = connection.execute(
                "SELECT kind,payload FROM events WHERE visit_id=? ORDER BY id",
                (config["id"],),
            ).fetchall()
        self.assertEqual(
            [(row["kind"], json.loads(row["payload"])) for row in saved], expected
        )

    def test_permission_rejects_freeform_and_non_string_reasons(self):
        """Verify permission rejects freeform and non string reasons."""
        config = self.visit()
        for reason in (
            "Camera denied: https://example.com/private",
            "UnknownError: camera failed",
            "unknownerror",
            None,
            True,
            1,
            [],
            {},
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    self.event(
                        config,
                        "permission",
                        {
                            "feature": "camera",
                            "state": "error",
                            "reason": reason,
                        },
                    ).status_code,
                    400,
                )
        for data in (
            {"feature": [], "state": "error"},
            {"feature": "camera", "state": {}},
        ):
            self.assertEqual(self.event(config, "permission", data).status_code, 400)
        with self.database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE visit_id=?", (config["id"],)
                ).fetchone()[0],
                0,
            )

    def test_capture_error_rejects_freeform_reason_and_unexpected_fields(self):
        """Verify capture error rejects freeform reason and unexpected fields."""
        config = self.visit()
        invalid = [
            {},
            {"facing": "front"},
            {"reason": "UnknownError"},
            {
                "facing": "front",
                "reason": "UnknownError",
                "message": "private capture details",
            },
        ]
        invalid.extend(
            {"facing": "front", "reason": reason}
            for reason in (
                "",
                "Permission denied at https://example.com/private",
                None,
                True,
                1,
                [],
                {},
            )
        )
        invalid.extend(
            {"facing": facing, "reason": "UnknownError"}
            for facing in ("environment", "", None, True, [], {})
        )
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(
                    self.event(config, "capture_error", data).status_code, 400
                )
        with self.database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE visit_id=?", (config["id"],)
                ).fetchone()[0],
                0,
            )

    def test_media_counts_duplicate_and_link_cascade(self):
        """Verify media counts duplicate and link cascade."""
        slug = self.link()
        config = self.visit(slug)
        self.assertEqual(self.upload(config).status_code, 201)
        self.assertEqual(self.upload(config).status_code, 409)
        self.assertEqual(
            self.upload(
                config, "video", data=b"\x1aE\xdf\xa3webm", mime="video/webm"
            ).status_code,
            201,
        )
        self.assertEqual(self.upload(config, facing="rear").status_code, 201)
        self.assertEqual(
            self.upload(
                config,
                "video",
                "rear",
                data=b"\x00\x00\x00\x18ftypmp42",
                mime="video/mp4",
            ).status_code,
            201,
        )
        self.client.post(f"/admin/links/{slug}/delete", data={"csrf": self.csrf()})
        self.assertEqual(
            len(list((Path(self.temporary_directory.name) / "media").iterdir())), 0
        )
        with self.database() as connection:
            for table in ("links", "visits", "media", "events"):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
                )

    def test_records_and_expired_links_are_kept_indefinitely(self):
        """Verify records and expired links are kept indefinitely."""
        config = self.visit()
        self.upload(config)
        self.event(config, "browser", {"language": "en"})
        old = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
        with self.database() as connection:
            connection.execute("UPDATE visits SET created_at=?", (old,))
            connection.execute("UPDATE links SET created_at=?,expires_at=?", (old, old))
        with self.app.app_context():
            self.assertEqual(self.app.cleanup_records(), 0)
        from unittest.mock import patch

        # Old deployment settings and scheduled cleanup commands are harmless.
        with patch.dict("os.environ", {"RETENTION_DAYS": "7"}):
            restarted = create_app(
                {
                    "TESTING": True,
                    "DATA_DIR": self.temporary_directory.name,
                    "ADMIN_PASSWORD": "a-long-test-password",
                    "RETENTION_DAYS": 7,
                }
            )
        restarted.test_client().get("/healthz")
        with restarted.app_context():
            self.assertEqual(restarted.cleanup_records(), 0)
        self.assertEqual(
            len(list((Path(self.temporary_directory.name) / "media").iterdir())), 1
        )
        with self.database() as connection:
            slug = connection.execute("SELECT id FROM links").fetchone()[0]
            for table in ("links", "visits", "media", "events"):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1
                )
        self.assertEqual(self.client.get("/l/" + slug).status_code, 410)
        self.assertEqual(self.client.get("/admin/links/" + slug).status_code, 200)
        self.assertEqual(len(self.client.get("/admin/export").json), 1)
        self.assertIn(
            "Records kept until manually deleted", self.client.get("/admin").text
        )

    def test_tables_sort_newest_first_with_equal_timestamps(self):
        """Verify tables sort newest first with equal timestamps."""
        from unittest.mock import patch

        with patch(
            "flaretrace.routes.capture.now", return_value="2026-10-24T00:00:00+00:00"
        ):
            first = self.link("First")
            second = self.link("Second")
            older = self.visit(second)
            newer = self.visit(second)
        page = self.client.get("/admin").text
        self.assertLess(
            page.index('data-href="/admin/links/' + second),
            page.index('data-href="/admin/links/' + first),
        )
        for path in ("/admin/visits", "/admin/links/" + second):
            page = self.client.get(path).text
            self.assertIn("2 clicks", page)
        with self.database() as connection:
            group_id = connection.execute(
                "SELECT group_id FROM visits WHERE id=?", (newer["id"],)
            ).fetchone()[0]
        page = self.client.get("/admin/visit-groups/" + group_id).text
        self.assertLess(
            page.index('data-href="/admin/visits/' + newer["id"]),
            page.index('data-href="/admin/visits/' + older["id"]),
        )
        self.assertEqual(
            [row["id"] for row in self.client.get("/admin/export").json],
            [newer["id"], older["id"]],
        )

    def test_templates_sort_by_creation_not_name_or_last_edit(self):
        """Verify templates sort by creation not name or last edit."""

        def save(name):
            """Save for the isolated integration checks.

            Args:
                name: Name of the option, entity, or field being processed.

            Returns:
                Result of the synthetic upload request used to assert write-failure cleanup.
            """
            result = self.client.post(
                "/admin/templates/new",
                data={
                    "csrf": self.csrf(),
                    "name": name,
                    "template_html": "<p>Content</p>",
                },
            )
            return result.location

        first = save("Alpha first")
        second = save("Zulu latest")
        self.client.post(
            first,
            data={
                "csrf": self.csrf(),
                "name": "Alpha renamed",
                "template_html": "<p>Edited</p>",
            },
        )
        page = self.client.get("/admin/templates").text
        self.assertLess(
            page.index('data-href="' + second), page.index('data-href="' + first)
        )

    def test_disable_expire_destination_and_headers(self):
        """Verify disable expire destination and headers."""
        for destination in (
            "javascript:alert(1)",
            "https://user:pass@example.com",
            "https://example.com/\nfoo",
            "http://example.com\\evil",
            "https://example.com:bad",
        ):
            response = self.client.post(
                "/admin/links/new",
                data={
                    "csrf": self.csrf(),
                    "name": "x",
                    "destination": destination,
                    "expiry": "7",
                },
            )
            self.assertEqual(response.status_code, 400)
        slug = self.link(destination="https://example.com/path")
        self.client.post(f"/admin/links/{slug}/toggle", data={"csrf": self.csrf()})
        self.assertEqual(self.client.get("/l/" + slug).status_code, 410)
        with self.database() as connection:
            connection.execute(
                "UPDATE links SET active=1,expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (slug,),
            )
        self.assertEqual(self.client.get("/l/" + slug).status_code, 410)
        response = self.client.get("/admin")
        self.assertIn(
            "frame-ancestors 'none'", response.headers["Content-Security-Policy"]
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_csv_formula_and_template_escape(self):
        """Verify csv formula and template escape."""
        config = self.visit(
            headers={
                "User-Agent": '=HYPERLINK("https://evil")',
                "X-Debug": "<script>alert(1)</script>",
            }
        )
        csv_data = self.client.get("/admin/export?format=csv").text
        self.assertIn("'=HYPERLINK", csv_data)
        detail = self.client.get(f"/admin/visits/{config['id']}").text
        self.assertNotIn("<script>alert(1)</script>", detail)

    def test_untrusted_proxy_is_ignored(self):
        """Verify untrusted proxy is ignored."""
        config = self.visit(
            headers={"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Proto": "https"}
        )
        self.assertEqual(self.client.get("/admin/export").json[0]["ip"], "127.0.0.1")

    def test_generated_credentials_persist_and_https_cookies(self):
        """Verify generated credentials persist and https cookies."""
        with tempfile.TemporaryDirectory() as directory:
            first = create_app(
                {
                    "TESTING": True,
                    "DATA_DIR": directory,
                    "ADMIN_PASSWORD": "",
                    "PUBLIC_BASE_URL": "https://trace.example.com",
                }
            )
            password = (Path(directory) / "admin-password").read_text().strip()
            second = create_app(
                {"TESTING": True, "DATA_DIR": directory, "ADMIN_PASSWORD": ""}
            )
            self.assertEqual(first.secret_key, second.secret_key)
            self.assertTrue(first.config["SESSION_COOKIE_SECURE"])
            self.assertGreaterEqual(len(password), 24)
            self.assertEqual(
                (Path(directory) / "admin-password").stat().st_mode & 0o777, 0o600
            )


if __name__ == "__main__":
    unittest.main()
