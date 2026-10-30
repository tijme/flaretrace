"""Browser-visible checks for visit limits, campaigns, QR codes, and visit notes."""

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.ui, pytest.mark.timeout(120)]


def seed_groups(database, link, count):
    """Insert `count` distinct single-click groups directly, oldest index first.

    Args:
        database: SQLite connection to the current test installation.
        link: Research-link identifier that owns every seeded group.
        count: Number of distinct groups and visits to insert.
    """
    for index in range(count):
        group_id = f"group-{index:04d}"
        timestamp = f"2026-01-01T{index // 3600:02d}:{index // 60 % 60:02d}:{index % 60:02d}+00:00"
        database.execute(
            "INSERT INTO visit_groups(id,link_id,ip,started_at,last_at) VALUES(?,?,?,?,?)",
            (group_id, link, f"203.0.113.{index % 250}", timestamp, timestamp),
        )
        database.execute(
            "INSERT INTO visits(id,link_id,created_at,ip,method,user_agent,referrer,"
            "headers,campaign,group_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                f"visit-{index:04d}",
                link,
                timestamp,
                f"203.0.113.{index % 250}",
                "GET",
                "ua",
                "",
                "{}",
                "{}",
                group_id,
            ),
        )
    database.commit()


@pytest.mark.parametrize("width", [390, 1440])
def test_load_older_visits_navigates_to_the_next_page(
    page, login, live_server, client, make_link, database, width
):
    """Follow 'Load older visits' to the next page without horizontal scroll.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
        client: Administrator HTTP client used to create the link and seed groups.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
        width: Viewport width in CSS pixels used to check responsive layouts.
    """
    link = make_link()
    seed_groups(database, link, 260)
    login()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server + "/admin/visits")
    expect(page.get_by_role("link", name="Load older visits")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("link", name="Load older visits").click()
    page.wait_for_url(lambda url: "before=" in url)
    expect(page.get_by_role("link", name="Load older visits")).not_to_be_attached()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [390, 1440])
def test_new_link_form_fits_narrow_viewports_with_the_new_fields(
    page, login, live_server, width
):
    """Show the visit-limit and campaign fields without introducing horizontal scroll.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
        width: Viewport width in CSS pixels used to check responsive layouts.
    """
    login()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server + "/admin/links/new")
    expect(page.get_by_label("Visit limit")).to_be_visible()
    expect(page.get_by_label("Campaign")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_label("Link name").fill("Mobile field test")
    page.get_by_label("Visit limit").fill("5")
    page.get_by_role("button", name="Create link").click()
    page.wait_for_url(
        lambda url: url.startswith(live_server + "/admin/links/")
        and url != live_server + "/admin/links/new"
    )
    expect(page.get_by_text("5 real visits used")).to_be_visible()


@pytest.mark.parametrize("width", [390, 1440])
def test_link_detail_renders_a_scannable_qr_image(page, login, live_server, width):
    """Render a loaded QR image for the link's public URL at every viewport width.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
        width: Viewport width in CSS pixels used to check responsive layouts.
    """
    login()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server + "/admin/links/new")
    page.get_by_label("Link name").fill("QR field test")
    page.get_by_role("button", name="Create link").click()
    page.wait_for_url(
        lambda url: url.startswith(live_server + "/admin/links/")
        and url != live_server + "/admin/links/new"
    )
    qr_image = page.locator(".qr-row img")
    expect(qr_image).to_be_visible()
    assert qr_image.evaluate("image => image.naturalWidth > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_campaign_can_be_created_and_assigned_from_the_browser(
    page, login, live_server
):
    """Create a campaign, assign a link to it, and see it reflected in both pages.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
    """
    login()
    page.goto(live_server + "/admin/campaigns/new")
    page.get_by_label("Campaign name").fill("Browser campaign")
    page.get_by_role("button", name="Create campaign").click()
    page.wait_for_url(
        lambda url: url.startswith(live_server + "/admin/campaigns/")
        and not url.endswith("/new")
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.goto(live_server + "/admin/links/new")
    page.get_by_label("Link name").fill("Campaign-linked")
    page.get_by_label("Campaign").select_option(label="Browser campaign")
    page.get_by_role("button", name="Create link").click()
    page.wait_for_url(
        lambda url: url.startswith(live_server + "/admin/links/")
        and url != live_server + "/admin/links/new"
    )
    page.goto(live_server + "/admin/campaigns")
    expect(page.get_by_role("link", name="Browser campaign")).to_be_visible()
    page.get_by_role("link", name="Browser campaign").click()
    expect(page.get_by_role("link", name="Campaign-linked")).to_be_visible()


@pytest.mark.parametrize("width", [390, 1440])
def test_webhook_test_button_fits_narrow_viewports(page, login, live_server, width):
    """Show the test-webhook button only once configured, without horizontal scroll.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
        width: Viewport width in CSS pixels used to check responsive layouts.
    """
    login()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server + "/admin/setup")
    expect(page.get_by_role("button", name="Send test webhook")).not_to_be_attached()
    page.get_by_label("Webhook URL").fill("https://hooks.example.com/notify")
    page.get_by_role("button", name="Save webhook").click()
    expect(page.get_by_role("button", name="Send test webhook")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_visit_note_can_be_saved_and_survives_a_reload(
    page, login, live_server, client, csrf
):
    """Save a note on a visit through the browser and see it after reloading.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
        client: Administrator HTTP client used to create a link and open it once.
        csrf: Session-bound token required by administrator POST forms.
    """
    link = client.post(
        "/admin/links/new",
        data={"csrf": csrf, "name": "Note test", "expiry": "never"},
    ).location.rsplit("/", 1)[-1]
    client.get("/l/" + link)
    login()
    page.goto(live_server + "/admin/visits")
    page.get_by_role("link", name="Inspect").first.click()
    page.get_by_label("Note").fill("Confirmed via browser test")
    page.get_by_role("button", name="Save note").click()
    expect(page.locator(".flash.success")).to_contain_text("Note saved")
    page.reload()
    expect(page.get_by_label("Note")).to_have_value("Confirmed via browser test")
