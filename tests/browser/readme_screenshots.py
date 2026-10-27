"""Capture README previews from a temporary installation containing only synthetic evidence."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from flaretrace import create_app
from flaretrace.visits.groups import assign_group

#: Fixed synthetic research timeline, unrelated to any real visitor.
ANCHOR = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
#: Disposable administrator credential used only by the temporary screenshot server.
PASSWORD = "readme-synthetic-demo-password"
#: Synthetic browser signature shown by the evidence inspector.
AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1"
#: Native desktop capture size; images are never stretched or resampled afterwards.
VIEWPORT = {"width": 1920, "height": 1080}


def timestamp(minutes=0, seconds=0):
    """Offset the fixed synthetic timeline for deterministic screenshots.

    Args:
        minutes: Whole-minute offset from the synthetic anchor.
        seconds: Additional seconds used to order event entries.

    Returns:
        ISO 8601 UTC timestamp with second precision.
    """
    return (ANCHOR + timedelta(minutes=minutes, seconds=seconds)).isoformat(
        timespec="seconds"
    )


def seed_links(connection):
    """Populate active, disabled, and expired examples without opening public capture links.

    Args:
        connection: SQLite connection to the disposable screenshot installation.
    """
    links = [
        ("harbor", "Demo · Harbor redevelopment", 1, None),
        ("procurement", "Demo · Procurement records", 1, None),
        ("environment", "Demo · Environmental report", 1, None),
        ("transport", "Demo · Transport tender", 0, None),
        ("archive", "Demo · Archived briefing", 1, "2026-10-01T00:00:00+00:00"),
    ]
    for index, (identifier, name, active, expiry) in enumerate(links):
        connection.execute(
            "INSERT INTO links(id,name,public_slug,created_at,active,expires_at) VALUES(?,?,?,?,?,?)",
            (
                identifier,
                name,
                identifier + "-update",
                timestamp(-1440 * index),
                active,
                expiry,
            ),
        )


def seed_events(connection, visit_id, minute, browser, location):
    """Record a synthetic permission timeline without requesting any actual permissions.

    Args:
        connection: SQLite connection to the disposable screenshot installation.
        visit_id: Synthetic visit identifier receiving the timeline.
        minute: Visit offset from the fixed synthetic timeline.
        browser: Synthetic browser metadata mapping.
        location: Synthetic coordinates mapping, or None when not shared.
    """
    events = [("notice_shown", {}), ("browser", browser)]
    if location:
        events.extend(
            [
                ("permission", {"feature": "location", "state": "granted"}),
                ("location", location),
            ]
        )
    events.extend(
        [
            (
                "permission",
                {"feature": "camera", "state": "denied", "reason": "permission_denied"},
            ),
            ("finished", {"reason": "complete"}),
        ]
    )
    for second, (kind, payload) in enumerate(events):
        connection.execute(
            "INSERT INTO events(visit_id,created_at,kind,payload) VALUES(?,?,?,?)",
            (visit_id, timestamp(minute, second), kind, json.dumps(payload)),
        )


def seed_visits(connection):
    """Insert reserved-address visits and approximate demonstration coordinates.

    Args:
        connection: Row-oriented SQLite connection to the disposable installation.
    """
    browser = {
        "userAgent": AGENT,
        "language": "en-GB",
        "platform": "iPhone",
        "timezone": "Europe/Amsterdam",
        "viewport": {"width": 390, "height": 844},
        "connection": {"effectiveType": "4g"},
    }
    location = {"latitude": 51.9225, "longitude": 4.47917, "accuracy": 12.4}
    records = [
        ("demo0001f4ce8a12", "harbor", "203.0.113.42", 0, True, True),
        ("demo0002f4ce8a12", "harbor", "203.0.113.42", -1, False, False),
        ("demo0003f4ce8a12", "harbor", "203.0.113.42", -2, False, False),
        ("demo0004f4ce8a12", "procurement", "198.51.100.23", -8, True, True),
        ("demo0005f4ce8a12", "environment", "192.0.2.84", -16, False, False),
        ("demo0006f4ce8a12", "harbor", "203.0.113.105", -24, True, False),
        ("demo0007f4ce8a12", "procurement", "198.51.100.61", -35, False, False),
        ("demo0008f4ce8a12", "environment", "192.0.2.17", -48, True, True),
    ]
    for identifier, link, address, minute, shared, located in reversed(records):
        connection.execute(
            """INSERT INTO visits(id,link_id,created_at,ip,method,user_agent,referrer,
               headers,campaign,browser,location,interacted,finished)
               VALUES(?,?,?,?,'GET',?,'https://example.org/research',?,'{}',?,?,?,?)""",
            (
                identifier,
                link,
                timestamp(minute),
                address,
                AGENT,
                json.dumps(
                    {"Transport-Peer-IP": "172.18.0.3", "X-Forwarded-For": address}
                ),
                json.dumps(browser) if shared else None,
                json.dumps(location) if located else None,
                int(shared),
                int(shared),
            ),
        )
        assign_group(connection, identifier, link, address, timestamp(minute))
        if shared:
            seed_events(
                connection, identifier, minute, browser, location if located else None
            )


def seed_installation(directory):
    """Populate only a newly created temporary database for the README gallery.

    Args:
        directory: TemporaryDirectory path created by this command, never a live installation.
    """
    with sqlite3.connect(Path(directory) / "trace.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "INSERT INTO settings VALUES('homepage_redirect','https://example.org/')"
        )
        seed_links(connection)
        seed_visits(connection)


def capture_gallery(origin, output):
    """Capture real desktop pages at their native 16:9 viewport with no image transformations.

    Args:
        origin: Loopback origin of the disposable screenshot server.
        output: Existing destination directory for the three PNG captures.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport=VIEWPORT,
            color_scheme="dark",
            device_scale_factor=1,
            reduced_motion="reduce",
            timezone_id="UTC",
        )
        context.add_init_script("localStorage.setItem('flaretrace-theme', 'dark')")
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin + "/")
                else route.abort()
            ),
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(origin + "/admin/login")
        page.get_by_label("Username").fill("admin")
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(origin + "/admin")
        screens = [
            ("admin-links.png", "/admin", "Tracked links"),
            ("admin-visits.png", "/admin/visits", "Visits"),
            (
                "admin-visit-inspector.png",
                "/admin/visits/demo0001f4ce8a12",
                "Click from",
            ),
        ]
        for filename, route, heading in screens:
            page.goto(origin + route)
            expect(
                page.get_by_role(
                    "heading", name=heading, exact=heading != "Click from"
                ).first
            ).to_be_visible()
            page.evaluate("document.fonts.ready")
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            if filename == "admin-visits.png":
                page.get_by_role("button", name="3 clicks").click()
                expect(page.locator("tr[data-click-id]")).to_have_count(3)
            page.evaluate("document.activeElement?.blur(); window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / filename), animations="disabled")
            print(
                f"Captured {filename} at {VIEWPORT['width']}×{VIEWPORT['height']}.",
                flush=True,
            )
        assert not errors, errors
        context.close()
        browser.close()


def main():
    """Create an isolated server, capture the gallery, and release all temporary resources."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("assets/screenshots"))
    options = parser.parse_args()
    options.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flaretrace-readme-") as directory:
        application = create_app(
            {
                "TESTING": True,
                "DATA_DIR": directory,
                "ADMIN_PASSWORD": PASSWORD,
                "TRUST_PROXY": False,
            }
        )
        seed_installation(directory)
        server = make_server("127.0.0.1", 0, application, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            capture_gallery(
                "http://127.0.0.1:" + str(server.server_port), options.output
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
