"""Module containing integration test fixtures."""

import html
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from flaretrace import create_app
from flaretrace.visits.groups import assign_group, list_groups


class VisitGroupTests(TestCase):
    """Exercise visit group tests with isolated application resources.

    Attributes:
        app: Flask application under test with temporary storage.
        client: HTTP client used for authenticated administrator requests.
        config: Explicit application configuration reused when testing a restarted installation.
        csrf: Session-bound administrator form token.
        directory: TemporaryDirectory owner for this test installation; cleaned up after use.
        path: Filesystem path of the isolated test installation’s SQLite database.
    """

    def setUp(self):
        """Create isolated storage and clients for each test case."""
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "trace.sqlite3"
        self.config = {
            "TESTING": True,
            "DATA_DIR": self.directory.name,
            "ADMIN_PASSWORD": "groups-test-password",
        }
        self.app = create_app(self.config)
        with self.database_connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings VALUES('homepage_redirect','https://example.com/')"
            )
            connection.execute(
                "INSERT INTO links(id,name,created_at) VALUES('first','First','2026-10-25T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO links(id,name,created_at) VALUES('second','Second','2026-10-25T00:00:00+00:00')"
            )
        self.client = self.app.test_client()
        self.client.get("/admin/login")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]
        self.client.post(
            "/admin/login",
            data={
                "csrf": self.csrf,
                "username": "admin",
                "password": "groups-test-password",
            },
        )
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.directory.cleanup()

    def database_connection(self):
        """Database connection for the isolated integration checks.

        Returns:
            New row-oriented SQLite connection; the caller must close it after use.
        """
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def seed(self, identifier, seconds, ip="203.0.113.10", link="first", grouped=True):
        """Seed for the isolated integration checks.

        Args:
            identifier: Stable synthetic visit identifier inserted by this fixture.
            seconds: Offset in seconds from the fixed synthetic UTC anchor timestamp.
            ip: Recorded visitor IP address used to match bounded click groups.
            link: Research-link identifier receiving the synthetic visit; None creates a link.
            grouped: Whether to assign click-group membership during fixture creation.

        Returns:
            Assigned click-group identifier, or None when grouping is deliberately deferred.
        """
        created = (
            datetime(2026, 10, 25, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        ).isoformat()
        with self.database_connection() as connection:
            connection.execute(
                """INSERT INTO visits(id,link_id,created_at,ip,method,user_agent,referrer,headers,campaign)
                                  VALUES(?,?,?,?,'GET','','','{}','{}')""",
                (identifier, link, created, ip),
            )
            return (
                assign_group(connection, identifier, link, ip, created)
                if grouped
                else None
            )

    def test_bounded_five_minute_groups_cross_clock_boundaries(self):
        """Verify bounded five minute groups cross clock boundaries."""
        first = self.seed("a", 299)
        self.assertEqual(
            self.seed("b", 300), first
        )  # Clock bucket boundary does not split a group.
        self.assertEqual(self.seed("c", 599), first)
        second = self.seed("d", 600)
        self.assertNotEqual(second, first)  # 301 seconds after the group's first click.
        self.assertEqual(self.seed("e", 899), second)
        with self.database_connection() as connection:
            groups, has_more = list_groups(connection)
            self.assertEqual([row["visit_count"] for row in groups], [2, 3])
            self.assertFalse(has_more)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 5
            )

    def test_different_ips_and_links_remain_separate_and_ipv6_is_normalized(self):
        """Verify different ips and links remain separate and ipv6 is normalized."""
        first = self.seed("a", 0)
        self.assertNotEqual(self.seed("b", 1, ip="203.0.113.11"), first)
        self.assertNotEqual(self.seed("c", 2, link="second"), first)
        ipv6 = self.seed("d", 3, ip="2001:0db8:0:0:0:0:0:1")
        self.assertEqual(self.seed("e", 4, ip="2001:db8::1"), ipv6)

    def test_group_limit_is_applied_after_aggregation(self):
        """Verify group limit is applied after aggregation."""
        self.seed("quiet", 0, ip="203.0.113.20")
        for number in range(600):
            self.seed("burst-" + str(number), 30 + number // 3)
        page = self.client.get("/admin/visits").text
        self.assertEqual(page.count('class="visit-group-row"'), 2)
        self.assertIn("600 clicks", page)
        self.assertIn("203.0.113.20", page)
        self.assertNotIn(
            "data-click-id=", page
        )  # No hundreds of hidden rows in the first response.
        self.assertEqual(len(self.client.get("/admin/export").json), 601)

    def test_click_pages_are_authenticated_bounded_and_complete(self):
        """Verify click pages are authenticated bounded and complete."""
        group = self.seed("click-000", 0)
        for number in range(1, 121):
            self.seed(f"click-{number:03d}", number)
        url = "/admin/visit-groups/" + group
        self.assertEqual(self.app.test_client().get(url).status_code, 302)
        self.assertEqual(self.client.get(url + "?before=missing").status_code, 400)
        other = self.seed("other", 5, ip="203.0.113.99")
        self.assertEqual(self.client.get(url + "?before=" + other).status_code, 400)
        found = []
        while url:
            response = self.client.get(url, headers={"X-Group-Fragment": "1"})
            self.assertEqual(response.status_code, 200)
            ids = re.findall(r'data-click-id="([^"]+)"', response.text)
            self.assertLessEqual(len(ids), 50)
            found.extend(ids)
            next_link = re.search(r'data-group-more href="([^"]+)"', response.text)
            url = html.unescape(next_link[1]) if next_link else None
        self.assertEqual(
            found, [f"click-{number:03d}" for number in range(120, -1, -1)]
        )
        self.assertEqual(len(set(found)), 121)
        self.assertIn(
            "Flaretrace", self.client.get("/admin/visit-groups/" + group).text
        )

    def test_manual_deletion_preserves_other_clicks_and_removes_empty_groups(self):
        """Verify manual deletion preserves other clicks and removes empty groups."""
        group = self.seed("a", 0)
        self.seed("b", 60)
        self.client.post("/admin/visits/b/delete", data={"csrf": self.csrf})
        with self.database_connection() as connection:
            rows, _ = list_groups(connection)
            self.assertEqual(rows[0]["visit_count"], 1)
            self.assertEqual(rows[0]["last_at"], rows[0]["started_at"])
        self.client.post("/admin/visits/a/delete", data={"csrf": self.csrf})
        self.assertEqual(
            self.client.get("/admin/visit-groups/" + group).status_code, 404
        )
        with self.database_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM visit_groups").fetchone()[0], 0
            )

    def test_existing_visits_migrate_once_without_data_loss(self):
        """Verify existing visits migrate once without data loss."""
        for number in range(1002):
            self.seed(f"legacy-{number}", number, grouped=False)
        create_app(self.config)
        with self.database_connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM visits WHERE group_id IS NULL"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 1002
            )
            count = connection.execute("SELECT COUNT(*) FROM visit_groups").fetchone()[
                0
            ]
            self.assertEqual(count, 4)
        create_app(self.config)
        with self.database_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM visit_groups").fetchone()[0],
                count,
            )

    def test_simultaneous_clicks_join_one_group(self):
        """Verify simultaneous clicks join one group."""

        def click(_):
            """Click for the isolated integration checks.

            Args:
                _: Unused thread-pool input; each invocation records a separate concurrent click.

            Returns:
                Identifier of the visit created by this concurrent visitor request.
            """
            with self.app.test_client() as client:
                return client.get(
                    "/l/first", environ_base={"REMOTE_ADDR": "203.0.113.88"}
                ).status_code

        with patch(
            "flaretrace.routes.capture.now", return_value="2026-10-25T00:00:00+00:00"
        ):
            with ThreadPoolExecutor(max_workers=5) as pool:
                self.assertEqual(list(pool.map(click, range(20))), [200] * 20)
        with self.database_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM visit_groups").fetchone()[0], 1
            )
            groups, _ = list_groups(connection)
            self.assertEqual(groups[0]["visit_count"], 20)

    def test_detail_summary_is_readable_and_raw_values_remain_escaped(self):
        """Verify detail summary is readable and raw values remain escaped."""
        self.seed("readable", 0)
        browser = {
            "language": "en-GB",
            "timezone": "Europe/Amsterdam",
            "viewport": {"width": 390, "height": 844},
            "facing": {"unexpected": "value"},
            "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1",
        }
        with self.database_connection() as connection:
            connection.execute(
                "UPDATE visits SET browser=?,interacted=1,user_agent=? WHERE id='readable'",
                (json.dumps(browser), "<script>alert(1)</script>"),
            )
            connection.execute(
                "INSERT INTO events(visit_id,created_at,kind,payload) VALUES('readable',?,'permission',?)",
                (
                    "2026-10-25T00:00:02+00:00",
                    json.dumps(
                        {
                            "feature": "location",
                            "state": "denied",
                            "reason": "permission_denied",
                        }
                    ),
                ),
            )
            connection.execute(
                "INSERT INTO events(visit_id,created_at,kind,payload) VALUES('readable',?,'browser',?)",
                ("2026-10-25T00:00:01+00:00", json.dumps(browser)),
            )
        result = self.client.get("/admin/visits/readable")
        self.assertEqual(result.status_code, 200)
        page = result.text
        for expected in (
            "iPhone",
            "Safari 17",
            "Europe/Amsterdam",
            "390 × 844",
            "Location access declined",
            "Technical details",
        ):
            self.assertIn(expected, page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
