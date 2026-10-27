"""Exercise the bundled widget's policy dialogs and user-activated capture control."""

from playwright.sync_api import expect
import pytest

from flaretrace.links.bundled import BUNDLED_TEMPLATE_ID


def create_default_link(page, login, origin):
    """Create a link through the form's default bundled-template selection.

    Args:
        page: Isolated browser page used for administrator and synthetic visitor interactions.
        login: Callable authenticating the page with the temporary administrator account.
        origin: Local test application origin sharing the fixture database.

    Returns:
        Public link URL for the newly created synthetic research link.
    """
    login()
    page.goto(origin + "/admin/links/new")
    expect(page.get_by_label("Template", exact=True)).to_have_value(BUNDLED_TEMPLATE_ID)
    page.get_by_label("Link name").fill("Consent template test")
    page.get_by_role("button", name="Create link").click()
    page.wait_for_url(
        lambda url: url.startswith(origin + "/admin/links/")
        and url != origin + "/admin/links/new"
    )
    return origin + "/l/" + page.url.rsplit("/", 1)[-1]


@pytest.mark.parametrize("width, name", [(1440, "desktop"), (390, "mobile")])
def test_default_template_policies_can_be_read_without_sharing(
    page, login, live_server, client, width, name
):
    """Read and dismiss both editable policies without starting browser or sensor capture.

    Args:
        page: Browser page retaining unexpected JavaScript errors for the fixture.
        login: Callable signing into the temporary administrator interface.
        live_server: Local application origin used for the public link.
        client: Administrator HTTP client used to inspect the recorded visit.
        width: Desktop or mobile viewport width used for layout verification.
        name: Viewport label determining the corresponding desktop or mobile height.
    """
    url = create_default_link(page, login, live_server)
    page.set_viewport_size(
        {"width": width, "height": 1000 if name == "desktop" else 844}
    )
    page.goto(url)
    expect(page.get_by_role("checkbox", name="I'm not a robot")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for policy in ("Privacy", "Terms"):
        page.get_by_role("link", name=policy, exact=True).click()
        dialog = page.get_by_role("dialog", name=policy, exact=True)
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("browser and device information", ignore_case=True)
        expect(dialog).to_contain_text("photos and video", ignore_case=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        dialog.get_by_role("button", name="Close", exact=True).click()
        expect(dialog).not_to_be_visible()
    expect(page.locator("body")).to_have_attribute("data-phase", "idle")
    visit = client.get("/admin/export").json[0]
    assert visit["browser"] is None and visit["location"] is None and not visit["media"]
    assert visit["interacted"] == 0


def test_sharing_starts_only_after_the_explicit_action(page, login, live_server):
    """Use synthetic permission denials to prove the page never opens sensors before consent.

    Args:
        page: Browser page with mocked camera, geolocation, and permission APIs.
        login: Callable signing into the isolated administrator interface.
        live_server: Local application origin serving the selected bundled template.
    """
    url = create_default_link(page, login, live_server)
    page.add_init_script("""(() => {
      window.sensorRequests = 0;
      navigator.geolocation.getCurrentPosition = (success, failure) => {
        window.sensorRequests++; failure({code: 1});
      };
      navigator.mediaDevices.getUserMedia = async () => {
        window.sensorRequests++; throw new DOMException('Synthetic denial', 'NotAllowedError');
      };
      navigator.permissions.query = async () => ({state: 'denied'});
    })();""")
    page.goto(url)
    assert page.evaluate("window.sensorRequests") == 0
    page.get_by_role("checkbox", name="I'm not a robot").click()
    expect(page.locator("body")).to_have_attribute("data-server-closed", "true")
    assert page.evaluate("window.sensorRequests") > 0
