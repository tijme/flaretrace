"""Browser-visible checks for visit limits, campaigns, QR codes, and visit notes."""

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.ui, pytest.mark.timeout(120)]


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
