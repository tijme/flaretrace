"""Verify the admin workspace is genuinely installable, in a real browser engine."""

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.ui, pytest.mark.timeout(120)]


@pytest.mark.parametrize("width", [390, 1440])
def test_service_worker_registers_for_real_with_the_expected_scope(
    page, login, live_server, width
):
    """Confirm a real browser accepts and activates the registration admin.js requests.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
        width: Viewport width in CSS pixels used to check responsive layouts.
    """
    login()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server + "/admin")
    page.wait_for_function(
        "async () => (await navigator.serviceWorker.getRegistrations()).length > 0"
    )
    scope = page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistrations())[0].scope"
    )
    assert scope == live_server + "/admin/"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_manifest_link_resolves_to_a_fetchable_same_origin_document(
    page, login, live_server
):
    """Fetch the linked manifest exactly as a real browser's install prompt would.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable authenticating the page with the temporary administrator account.
        live_server: Local application origin used for the admin form.
    """
    login()
    page.goto(live_server + "/admin")
    manifest_href = page.get_attribute('link[rel="manifest"]', "href")
    response = page.request.get(live_server + manifest_href)
    assert response.ok
    manifest = response.json()
    assert manifest["name"] == "Flaretrace"
    assert manifest["icons"]
    for icon in manifest["icons"]:
        icon_response = page.request.get(live_server + "/static/" + icon["src"])
        assert icon_response.ok
