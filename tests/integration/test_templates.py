"""Module containing integration test fixtures."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import pytest

from flaretrace import create_app
from flaretrace.links.templates import MAX_TEMPLATE_BYTES, validate_template


@pytest.mark.parametrize(
    "content_type", ["application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_full_size_template_can_be_created_and_updated(
    client, csrf, database, content_type
):
    """Save 10 MB HTML through real form parsing, including worst-case percent encoding.

    Args:
        client: Authenticated administrator HTTP client.
        csrf: Session-bound administrator form token.
        database: Connection used to verify the complete persisted template.
        content_type: Browser form encoding used for creation and updates.
    """
    source = "<pre>" + "\n" * (10_000_000 - len("<pre></pre>")) + "</pre>"
    response = client.post(
        "/admin/templates/new",
        data={"csrf": csrf, "name": "Large template", "template_html": source},
        content_type=content_type,
    )
    assert response.status_code == 302
    identifier = response.location.rsplit("/", 1)[-1]
    updated = source.replace("\n", "\t")
    response = client.post(
        response.location,
        data={"csrf": csrf, "name": "Updated template", "template_html": updated},
        content_type=content_type,
    )
    assert response.status_code == 302
    saved = database.execute(
        "SELECT template_html FROM template_library WHERE id=?", (identifier,)
    ).fetchone()[0]
    assert saved == updated


class LinkTemplateTests(unittest.TestCase):
    """Exercise link template tests with isolated application resources.

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
                "ADMIN_PASSWORD": "template-test-password",
            }
        )
        with sqlite3.connect(
            Path(self.temporary_directory.name) / "trace.sqlite3"
        ) as configured:
            configured.execute(
                "INSERT OR REPLACE INTO settings VALUES('homepage_redirect','https://example.com/')"
            )
        self.client = self.app.test_client()
        self.client.get("/admin/login")
        with self.client.session_transaction() as sess:
            csrf = sess["csrf"]
        response = self.client.post(
            "/admin/login",
            data={
                "csrf": csrf,
                "username": "admin",
                "password": "template-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            self.csrf = sess["csrf"]

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.temporary_directory.cleanup()

    def database_connection(self):
        """Database connection for the isolated integration checks.

        Returns:
            New row-oriented SQLite connection; the caller must close it after use.
        """
        return sqlite3.connect(Path(self.temporary_directory.name) / "trace.sqlite3")

    def create_link(self, source=""):
        """Create a synthetic research link through the administrator interface.

        Args:
            source: Complete source text to parse or validate without executing it.

        Returns:
            Identifier of the newly created synthetic research link.
        """
        template_id = ""
        if source:
            saved = self.client.post(
                "/admin/templates/new",
                data={"csrf": self.csrf, "name": "Reusable", "template_html": source},
            )
            self.assertEqual(saved.status_code, 302)
            template_id = saved.location.rsplit("/", 1)[-1]
        result = self.client.post(
            "/admin/links/new",
            data={
                "csrf": self.csrf,
                "name": "Example",
                "expiry": "7",
                "template_id": template_id,
            },
        )
        self.assertEqual(result.status_code, 302)
        return result.location.rsplit("/", 1)[-1]

    def test_default_page_unchanged(self):
        """Verify default page unchanged."""
        slug = self.create_link()
        result = self.client.get("/l/" + slug)
        self.assertNotIn('class="link-template-content"', result.text)
        self.assertIn('id="enable-button"', result.text)
        self.assertIn('id="stop-button"', result.text)
        self.assertNotIn("Share precise", result.text)
        self.assertNotIn(">Decline<", result.text)

    def test_capture_page_serves_authored_markup_without_runtime_chrome(self):
        """Verify capture page serves authored markup without runtime chrome."""
        slug = self.create_link(
            '<style>body{color:teal}</style><h1>Authored</h1><button id="enable-button">Go</button>'
        )
        result = self.client.get(f"/l/{slug}")
        self.assertEqual(result.status_code, 200)
        self.assertIn("<h1>Authored</h1>", result.text)
        self.assertIn('<button id="enable-button">Go</button>', result.text)
        self.assertIn(
            'src="http://localhost/static/capture.js" defer data-flaretrace-runtime',
            result.text,
        )
        self.assertNotIn("capture-disclosure", result.text)
        self.assertNotIn("capture-status", result.text)
        self.assertNotIn("emergency-stop", result.text)
        self.assertNotIn("<iframe", result.text)
        self.assertNotIn("/static/app.css", result.text)
        self.assertNotIn("/static/admin.js", result.text)
        self.assertNotIn("/static/theme.js", result.text)
        self.assertEqual(result.headers["X-Frame-Options"], "DENY")

    def test_link_specific_content_edit_and_reset(self):
        """Verify link specific content edit and reset."""
        first = self.create_link(
            "<style>body { color: teal; }</style><h1>First page</h1>"
        )
        second = self.create_link("<h1>Second page</h1>")
        self.assertIn("First page", self.client.get(f"/l/{first}/content").text)
        self.assertNotIn("First page", self.client.get(f"/l/{second}/content").text)
        with self.database_connection() as connection:
            template_id = connection.execute(
                "SELECT template_id FROM links WHERE id=?", (first,)
            ).fetchone()[0]
        result = self.client.post(
            f"/admin/templates/{template_id}",
            data={
                "csrf": self.csrf,
                "name": "Updated",
                "template_html": "<p>Updated</p>",
            },
        )
        self.assertEqual(result.status_code, 302)
        self.assertIn("Updated", self.client.get(f"/l/{first}/content").text)
        self.client.post(
            f"/admin/templates/{template_id}",
            data={"csrf": self.csrf, "name": "Default", "template_html": ""},
        )
        page = self.client.get(f"/l/{first}").text
        self.assertNotIn('class="link-template-content"', page)
        self.assertIn('id="enable-button"', page)

    def test_original_document_is_saved_and_only_capture_script_is_appended(self):
        """Keep full HTML documents intact in storage, content responses, and capture pages."""
        source = """ \n<!DOCTYPE HTML><html lang='nl'><head>
            <title>Authored page</title><base href="https://example.invalid/">
            <link rel="stylesheet" href="theme.css"></head><body class=original>
            <script>window.authored = true;</script>
            <form action="https://example.invalid"><input name="query"></form>
            <iframe src="https://example.invalid"></iframe>
            <svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="2"/></svg>
            <a href="https://example.invalid" onclick="return true">Link text</a>
            <img src="https://example.invalid/logo.png" loading="lazy">
            <button id="enable-button">Share</button><p>{{7*7}} &copy;</p>
            </body></html> \n"""
        slug = self.create_link(source)
        with self.database_connection() as connection:
            saved = connection.execute(
                "SELECT template_html FROM links WHERE id=?", (slug,)
            ).fetchone()[0]
        self.assertEqual(saved, source)
        result = self.client.get(f"/l/{slug}/content")
        self.assertEqual(result.text, source)
        capture = self.client.get(f"/l/{slug}")
        self.assertTrue(capture.text.startswith(source + "\n<script "))
        self.assertIn("data-flaretrace-runtime", capture.text[len(source) :])
        self.assertEqual(capture.text.count("<html"), 1)
        self.assertEqual(
            capture.headers["Content-Security-Policy"], "frame-ancestors 'none'"
        )
        self.assertNotIn("default-src", capture.headers["Content-Security-Policy"])

    def test_preview_and_content_do_not_create_visits(self):
        """Verify preview and content do not create visits."""
        slug = self.create_link("<h1>Preview</h1>")
        self.assertEqual(len(self.client.get("/admin/export").json), 0)
        result = self.client.get(f"/admin/links/{slug}/template/preview")
        self.assertEqual(result.status_code, 200)
        self.assertIn('data-preview="true"', result.text)
        self.assertIn(
            "sandbox allow-scripts", result.headers["Content-Security-Policy"]
        )
        self.assertNotIn("allow-same-origin", result.headers["Content-Security-Policy"])
        self.assertIn("camera=()", result.headers["Permissions-Policy"])
        self.assertNotIn("data-config=", result.text)
        self.assertNotIn('id="enable-button"', result.text)
        self.client.get(f"/admin/links/{slug}/template/content")
        self.client.get(f"/l/{slug}/content")
        self.assertEqual(len(self.client.get("/admin/export").json), 0)

    def test_authentication_csrf_and_limits(self):
        """Verify authentication csrf and limits."""
        slug = self.create_link()
        anon = self.app.test_client()
        self.assertEqual(anon.get("/admin/templates").status_code, 302)
        self.assertEqual(
            anon.get(f"/admin/links/{slug}/template/preview").status_code, 302
        )
        self.assertEqual(
            self.client.post(
                f"/admin/links/{slug}/template", data={"template_html": "<p>x</p>"}
            ).status_code,
            400,
        )
        response = self.client.post(
            "/admin/links/new",
            data={
                "csrf": self.csrf,
                "name": "Big",
                "expiry": "7",
                "template_html": "<p>Inline templates are not supported.</p>",
            },
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/admin/templates/new",
            data={
                "csrf": self.csrf,
                "name": "Big",
                "template_html": "x" * (MAX_TEMPLATE_BYTES + 1),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.client.post(f"/admin/links/{slug}/toggle", data={"csrf": self.csrf})
        self.assertEqual(self.client.get(f"/l/{slug}/content").status_code, 410)
        self.assertEqual(
            self.client.get(f"/admin/links/{slug}/template/content").status_code, 200
        )

    def test_migrates_existing_database_without_data_loss(self):
        """Verify migrates existing database without data loss."""
        with tempfile.TemporaryDirectory() as path:
            connection = sqlite3.connect(Path(path) / "trace.sqlite3")
            connection.executescript("""CREATE TABLE links (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, destination TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, expires_at TEXT, active INTEGER NOT NULL DEFAULT 1);
                INSERT INTO links(id,name,created_at) VALUES('existing','Old link','2026-10-24T00:00:00+00:00');""")
            connection.close()
            migrated = create_app(
                {
                    "TESTING": True,
                    "DATA_DIR": path,
                    "ADMIN_PASSWORD": "template-test-password",
                }
            )
            result = migrated.test_client().get("/l/existing")
            self.assertEqual(result.status_code, 200)
            with sqlite3.connect(Path(path) / "trace.sqlite3") as database_connection:
                self.assertEqual(
                    database_connection.execute(
                        'SELECT name,template_html FROM links WHERE id="existing"'
                    ).fetchone(),
                    ("Old link", ""),
                )

    def test_data_images_entities_and_svg_are_preserved(self):
        """Retain original entities, embedded images, vector elements, and scripts."""
        result = validate_template(
            '<img src="data:image/png;base64,aGVsbG8="><p>1 &lt; 2 &amp; 3</p><svg><script>bad</script></svg>'
        )
        self.assertIn("data:image/png;base64,aGVsbG8=", result)
        self.assertIn("1 &lt; 2 &amp; 3", result)
        self.assertIn("<svg><script>bad</script></svg>", result)


if __name__ == "__main__":
    unittest.main()


class LibraryControlTests(LinkTemplateTests):
    """Exercise library control tests with isolated application resources."""

    def test_library_selection_only_and_no_slug_update(self):
        """Verify library selection only and no slug update."""
        self.assertNotIn(
            'name="template_html"', self.client.get("/admin/links/new").text
        )
        self.assertEqual(
            self.client.post(
                "/admin/links/new",
                data={
                    "csrf": self.csrf,
                    "name": "Bad",
                    "expiry": "7",
                    "template_html": "<h1>not selected</h1>",
                },
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/admin/links/new",
                data={
                    "csrf": self.csrf,
                    "name": "Bad",
                    "expiry": "7",
                    "template_id": "missing",
                },
            ).status_code,
            400,
        )
        slug = self.create_link()
        self.assertEqual(
            self.client.post(
                f"/admin/links/{slug}/segment",
                data={"csrf": self.csrf, "public_slug": "new"},
            ).status_code,
            404,
        )

    def test_labels_elements_and_aliases_preserved(self):
        """Verify labels elements and aliases preserved."""
        for enable, stop in [
            ("button", "button"),
            ("a", "div"),
            ("div", "span"),
            ("input", "input"),
        ]:
            with self.subTest(enable=enable, stop=stop):
                opening = (
                    f'<{enable} id="enable-button" value="Begin custom">Begin custom</{enable}>'
                    if enable != "input"
                    else '<input id="enable-button" value="Begin custom">'
                )
                ending = (
                    f'<{stop} id="disable-button" value="Cancel custom">Cancel custom</{stop}>'
                    if stop != "input"
                    else '<input id="disable-button" value="Cancel custom">'
                )
                slug = self.create_link(opening + ending)
                page = self.client.get("/l/" + slug).text
                self.assertIn(f'<{enable} id="enable-button"', page)
                self.assertIn(f'<{stop} id="disable-button"', page)
                self.assertIn("Begin custom", page)
                self.assertIn("Cancel custom", page)
                self.assertNotIn(
                    "This page can request location and camera access", page
                )
                content = self.client.get("/l/" + slug + "/content").text
                self.assertIn(f'id="enable-button"', content)
                self.assertIn(f'id="disable-button"', content)
                self.assertNotIn('data-custom-label="true"', page)
        slug = self.create_link(
            '<a id="enable-button">Go</a><span id="stop-button">Halt</span>'
        )
        self.assertIn('<span id="stop-button"', self.client.get("/l/" + slug).text)

    def test_library_propagation_and_delete_guard(self):
        """Verify library propagation and delete guard."""
        slug = self.create_link("<h1>One</h1>")
        with self.database_connection() as database_connection:
            template_id = database_connection.execute(
                "SELECT template_id FROM links WHERE id=?", (slug,)
            ).fetchone()[0]
        self.assertEqual(
            self.client.post(
                "/admin/templates/" + template_id + "/delete", data={"csrf": self.csrf}
            ).status_code,
            409,
        )
        self.client.post(
            "/admin/templates/" + template_id,
            data={
                "csrf": self.csrf,
                "name": "Changed",
                "template_html": "<h1>Two</h1>",
            },
        )
        self.assertIn("Two", self.client.get("/l/" + slug + "/content").text)
        self.assertIn("Flaretrace", self.client.get("/admin/templates").text)

    def test_existing_custom_html_migrates_into_library(self):
        """Verify existing custom html migrates into library."""
        with self.database_connection() as database_connection:
            database_connection.execute(
                "INSERT INTO links(id,name,created_at,template_html) VALUES('legacy-html','Legacy','2026-10-24T00:00:00+00:00','<h1>Keep legacy HTML</h1>')"
            )
        create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.temporary_directory.name,
                "ADMIN_PASSWORD": "template-test-password",
            }
        )
        with self.database_connection() as database_connection:
            row = database_connection.execute(
                "SELECT l.template_html,t.template_html FROM links l JOIN template_library t ON l.template_id=t.id WHERE l.id='legacy-html'"
            ).fetchone()
            self.assertEqual(
                row, ("<h1>Keep legacy HTML</h1>", "<h1>Keep legacy HTML</h1>")
            )
