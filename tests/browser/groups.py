"""Optional grouped-visit and detail-page browser regression checks.

Requires Playwright and Pillow, TRACE_TEST_URL, TRACE_TEST_PASSWORD, and
TRACE_TEST_DATA_DIR pointing to a dedicated temporary installation. Seeds only
synthetic records and images; never requests browser sensor access.
"""

import base64
import io
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[2]
from flaretrace.visits.groups import assign_group

base = os.environ["TRACE_TEST_URL"]
data = Path(os.environ["TRACE_TEST_DATA_DIR"])
artifacts = Path(os.environ.get("TRACE_TEST_ARTIFACTS", root / "artifacts"))
artifacts.mkdir(parents=True, exist_ok=True)
anchor = datetime.now(timezone.utc) - timedelta(seconds=180)
connection = sqlite3.connect(data / "trace.sqlite3")
connection.row_factory = sqlite3.Row
connection.execute("PRAGMA foreign_keys=ON")
connection.execute(
    "INSERT INTO links(id,name,created_at) VALUES('synthetic-qa','Synthetic sample · Repeat clicks',?)",
    (anchor.isoformat(),),
)
connection.execute(
    "INSERT INTO links(id,name,created_at) VALUES('synthetic-rich','Synthetic sample · Shared information',?)",
    (anchor.isoformat(),),
)


def seed(identifier, second, link="synthetic-qa", ip="203.0.113.44"):
    """Seed for the isolated browser checks.

    Args:
        identifier: Stable synthetic visit identifier inserted by this fixture.
        second: Offset in seconds from the fixture’s anchor timestamp.
        link: Research-link identifier receiving the synthetic visit; None creates a link.
        ip: Recorded visitor IP address used to match bounded click groups.

    Returns:
        Assigned click-group identifier, or None when grouping is deliberately deferred.
    """
    when = (anchor + timedelta(seconds=second)).isoformat(timespec="seconds")
    connection.execute(
        """INSERT INTO visits(id,link_id,created_at,ip,method,user_agent,referrer,headers,campaign)
       VALUES(?,?,?,?,'GET',?,'https://example.org/guide',?,'{}')""",
        (
            identifier,
            link,
            when,
            ip,
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1",
            json.dumps(
                {
                    "Transport-Peer-IP": "172.18.0.3",
                    "X-Forwarded-For": ip + ", 198.51.100.6",
                    "User-Agent": "Synthetic browser",
                }
            ),
        ),
    )
    return assign_group(connection, identifier, link, ip, when)


burst = seed("burst-000", 0)
for number in range(1, 121):
    seed(f"burst-{number:03d}", number)
rich_group = seed("rich", 125, "synthetic-rich", "2001:db8:44:1234:5678:9abc:def0:1234")
seed("rich-repeat", 126, "synthetic-rich", "2001:db8:44:1234:5678:9abc:def0:1234")
seed("empty", 127, "synthetic-rich", "203.0.113.9")
browser = {
    "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1",
    "language": "en-GB",
    "timezone": "Europe/Amsterdam",
    "viewport": {"width": 390, "height": 844},
    "connection": {"effectiveType": "4g"},
}
location = {"latitude": 51.5007, "longitude": -0.1246, "accuracy": 8.5}
connection.execute(
    "UPDATE visits SET browser=?,location=?,interacted=1,finished=1 WHERE id='rich'",
    (json.dumps(browser), json.dumps(location)),
)
event_data = [
    ("notice_shown", {}),
    ("browser", browser),
    ("permission", {"feature": "location", "state": "granted"}),
    ("location", location),
    ("permission", {"feature": "camera", "state": "granted"}),
    ("capture_started", {"facing": "front"}),
    (
        "diagnostic",
        {
            "stage": "upload",
            "outcome": "success",
            "mediaKind": "photo",
            "facing": "front",
            "bytes": 62000,
        },
    ),
    ("capture_started", {"facing": "rear"}),
    (
        "diagnostic",
        {
            "stage": "upload",
            "outcome": "success",
            "mediaKind": "photo",
            "facing": "rear",
            "bytes": 64000,
        },
    ),
    ("capture_error", {"facing": "rear", "reason": "NotReadableError"}),
    ("finished", {"reason": "complete"}),
]
for index, (kind, payload) in enumerate(event_data):
    when = (anchor + timedelta(seconds=125 + index)).isoformat(timespec="seconds")
    connection.execute(
        "INSERT INTO events(visit_id,created_at,kind,payload) VALUES('rich',?,?,?)",
        (when, kind, json.dumps(payload)),
    )
for facing, color in (("front", (47, 91, 70)), ("rear", (66, 80, 105))):
    output = io.BytesIO()
    Image.new("RGB", (640, 400), color).save(output, format="JPEG")
    payload = output.getvalue()
    name = "synthetic-" + facing + ".jpg"
    (data / "media" / name).write_bytes(payload)
    connection.execute(
        "INSERT INTO media(id,visit_id,kind,facing,mime,filename,size,created_at) VALUES(?,'rich','photo',?,'image/jpeg',?,?,?)",
        (
            facing,
            facing,
            name,
            len(payload),
            (anchor + timedelta(seconds=133)).isoformat(timespec="seconds"),
        ),
    )
connection.commit()

with sync_playwright() as playwright:
    web = playwright.chromium.launch(
        executable_path=os.environ.get("CHROMIUM_PATH"),
        headless=True,
        args=["--no-sandbox"],
    )
    context = web.new_context(
        viewport={"width": 1440, "height": 1100}, color_scheme="dark"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/admin/login")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill(os.environ["TRACE_TEST_PASSWORD"])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(base + "/admin")
    page.goto(base + "/admin/visits")
    button = page.locator("#toggle-" + burst)
    expect(button).to_have_text("121 clicks")
    parent = page.locator('[data-group-row="' + burst + '"]')
    parent.focus()
    page.keyboard.press("Enter")
    body = page.locator('[data-group-body="' + burst + '"]')
    expect(body.locator("tr[data-click-id]")).to_have_count(50)
    body.locator("[data-group-more]").click()
    expect(body.locator("tr[data-click-id]")).to_have_count(100)
    # Keep the disclosure focused while the background poll adds a click.
    button.focus()
    seed("burst-newest", 180)
    connection.commit()
    expect(button).to_have_text("122 clicks", timeout=8000)
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(body.locator("tr[data-click-id]")).to_have_count(100, timeout=8000)
    expect(body.locator("tr[data-click-id]").first).to_have_attribute(
        "data-click-id", "burst-newest"
    )
    body.locator("[data-group-more]").click()
    expect(body.locator("tr[data-click-id]")).to_have_count(122)
    assert body.locator("[data-group-more]").count() == 0
    assert (
        len(
            set(
                body.locator("tr[data-click-id]").evaluate_all(
                    "(rows)=>rows.map(row=>row.dataset.clickId)"
                )
            )
        )
        == 122
    )
    # Reconcile the loaded tail too: these clicks are beyond the newest 50.
    body.locator(".table-scroll").evaluate("(item)=>item.scrollTop=220")
    button.focus()
    connection.execute("UPDATE visits SET interacted=1,finished=1 WHERE id='burst-010'")
    connection.commit()
    csrf = page.locator('input[name="csrf"]').first.input_value()
    result = context.request.post(
        base + "/admin/visits/burst-011/delete", form={"csrf": csrf}
    )
    assert result.ok
    expect(button).to_have_text("121 clicks", timeout=8000)
    expect(body.locator("tr[data-click-id]")).to_have_count(121, timeout=8000)
    expect(body.locator('tr[data-click-id="burst-010"]')).to_contain_text(
        "Started", timeout=8000
    )
    expect(body.locator('tr[data-click-id="burst-010"]')).to_contain_text("Ended")
    assert body.locator('tr[data-click-id="burst-011"]').count() == 0
    assert body.locator(".table-scroll").evaluate("(item)=>item.scrollTop") > 0
    # Collapsing with the keyboard keeps the loaded clicks for the next expansion.
    button.focus()
    page.keyboard.press("Space")
    expect(button).to_have_attribute("aria-expanded", "false")
    page.wait_for_timeout(2500)
    page.keyboard.press("Enter")
    expect(body.locator("tr[data-click-id]")).to_have_count(121)
    page.locator("body").click(position={"x": 400, "y": 75})
    page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
    page.screenshot(
        path=str(artifacts / "flaretrace-groups-desktop.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    box = button.bounding_box()
    assert box and 0 <= box["x"] and box["x"] + box["width"] <= 390
    page.screenshot(
        path=str(artifacts / "flaretrace-groups-mobile.png"), full_page=True
    )
    button.click()
    page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
    page.screenshot(
        path=str(artifacts / "flaretrace-groups-mobile-collapsed.png"), full_page=True
    )
    button.click()
    # Click a dynamically loaded row without using its Inspect link.
    body.locator('tr[data-click-id="burst-newest"]').locator("td").nth(1).click()
    page.wait_for_url(base + "/admin/visits/burst-newest")
    page.goto(base + "/admin/visits/rich")
    assert page.get_by_role("heading", name="What happened").is_visible()
    expect(page.locator(".visit-facts")).to_contain_text("2")
    assert "Safari 17" in page.locator(".visit-overview").inner_text()
    assert page.locator("details[open]").count() == 0
    page.locator("#browser-details summary").click()
    connection.execute(
        "INSERT INTO events(visit_id,created_at,kind,payload) VALUES('rich',?,'diagnostic',?)",
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "stage": "upload",
                    "outcome": "error",
                    "facing": "rear",
                    "mediaKind": "video",
                    "error": "NetworkError",
                }
            ),
        ),
    )
    connection.commit()
    expect(page.locator(".activity-list").first).to_contain_text(
        "Rear video failed", timeout=8000
    )
    expect(page.locator("#browser-details")).to_have_attribute("open", "")
    page.locator("#browser-details summary").click()
    for width, name, theme in (
        (1440, "desktop-dark", "dark"),
        (390, "mobile-dark", "dark"),
        (1440, "desktop-light", "light"),
        (390, "mobile-light", "light"),
    ):
        page.set_viewport_size({"width": width, "height": 1050 if width > 600 else 844})
        page.evaluate(
            "(theme)=>{localStorage.setItem('flaretrace-theme',theme)}", theme
        )
        page.reload()
        page.locator(".visit-media img").first.evaluate("(image)=>image.decode()")
        page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
        assert page.evaluate("window.scrollY") == 0
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), name
        page.screenshot(
            path=str(artifacts / f"flaretrace-detail-{name}.png"), full_page=True
        )
        if width == 390:
            page.screenshot(
                path=str(artifacts / f"flaretrace-detail-{name}-viewport.png")
            )
    page.goto(base + "/admin/visits/empty")
    assert (
        "No sharing action has been recorded"
        in page.locator(".visit-summary").inner_text()
    )
    assert page.locator(".activity-list li").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
    page.screenshot(
        path=str(artifacts / "flaretrace-detail-empty-mobile.png"), full_page=True
    )
    assert not errors, errors
    svg = base64.b64encode((root / "assets/flaretrace.svg").read_bytes()).decode()
    banner_page = context.new_page()
    banner_page.set_viewport_size({"width": 1100, "height": 260})
    banner_page.set_content(
        '<html><body style="margin:0;background:#0b0f0d"><img alt="Flaretrace" style="width:100%;height:auto" src="data:image/svg+xml;base64,'
        + svg
        + '"></body></html>'
    )
    banner_page.locator("img").evaluate("(image)=>image.decode()")
    banner_page.screenshot(path=str(artifacts / "flaretrace-banner.png"))
    web.close()
connection.close()
print(
    "Passed: bounded groups, 50-click pagination, 122-row completeness, older-row edits/deletion, expansion through refresh, keyboard controls, individual navigation, technical-disclosure preservation, live timeline, desktop/mobile themes, empty detail, no JS errors."
)
