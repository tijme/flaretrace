"""Browser-visible authentication, editing, evidence, and responsive-layout flows."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.ui, pytest.mark.timeout(120)]


@pytest.mark.parametrize("variant", ["flaretrace.svg", "flaretrace-dark.svg"])
def test_readme_banner_canvas_fits_actual_vector_ink(page, variant):
    """Verify the browser-measured logo bounds leave less than a pixel of transparent padding.

    Args:
        page: Isolated Playwright page for the selected browser engine.
        variant: Light or dark README vector asset to measure without raster conversion.
    """
    source = Path(__file__).resolve().parents[2] / "assets" / variant
    page.set_content(source.read_text())
    padding = page.locator("svg").evaluate("""async element => {
        const canvas = element.viewBox.baseVal;
        // Firefox does not measure geometry inside non-rendered mask definitions.
        const lettering = element.querySelector('mask g').cloneNode(true);
        lettering.setAttribute('visibility', 'hidden');
        element.appendChild(lettering);
        await new Promise(requestAnimationFrame);
        const ink = lettering.getBBox();
        lettering.remove();
        return [ink.x - canvas.x, ink.y - canvas.y,
            canvas.x + canvas.width - ink.x - ink.width,
            canvas.y + canvas.height - ink.y - ink.height];
    }""")
    assert all(-0.01 <= edge <= 0.1 for edge in padding), padding


def test_login_failure_success_and_logout(page, live_server, login):
    """Verify login failure success and logout.

    Args:
        page: Playwright browser page used for administrator interactions.
        live_server: Local HTTP origin and database path for the temporary test installation.
        login: Fixture callable that signs the administrator into the browser page.
    """
    page.goto(live_server + "/admin")
    expect(page.get_by_label("Username")).to_be_visible()
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill("wrong-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text("Incorrect username or password")
    login()
    expect(
        page.get_by_role("heading", name="Tracked links", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_label("Password")).to_be_visible()


def test_first_login_requires_homepage_configuration(
    page, live_server, login, database
):
    """Verify first login requires homepage configuration.

    Args:
        page: Playwright browser page used for administrator interactions.
        live_server: Local HTTP origin and database path for the temporary test installation.
        login: Fixture callable that signs the administrator into the browser page.
        database: SQLite connection to the current test installation.
    """
    database.execute("DELETE FROM settings WHERE key='homepage_redirect'")
    database.commit()
    login()
    page.get_by_label("Homepage redirect URL (required)").fill(live_server)
    page.get_by_role("button", name="Save and continue").click()
    expect(page.get_by_role("status")).to_contain_text("valid external")
    page.get_by_label("Homepage redirect URL (required)").fill(
        "https://example.org/research"
    )
    page.get_by_role("button", name="Save and continue").click()
    page.wait_for_url(live_server + "/admin")
    result = page.context.request.get(live_server, max_redirects=0)
    assert result.headers["location"] == "https://example.org/research"


@pytest.mark.parametrize("width", [390, 1440, 1920])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_settings_spacing_and_responsive_layout(page, login, live_server, width, theme):
    """Verify settings spacing and responsive layout.

    Args:
        page: Playwright browser page used for administrator interactions.
        login: Fixture callable that signs the administrator into the browser page.
        live_server: Local HTTP origin and database path for the temporary test installation.
        width: Viewport width in CSS pixels used to check responsive layouts.
        theme: Explicit light or dark color-scheme preference used by the browser.
    """
    login()
    page.set_viewport_size({"width": width, "height": 1000})
    page.evaluate("theme => localStorage.setItem('flaretrace-theme', theme)", theme)
    page.goto(live_server + "/admin/setup")
    page.evaluate("document.fonts.ready")
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    last_form = page.locator("form.panel").last.bounding_box()
    sections = page.locator(".setup-grid").bounding_box()
    assert sections["y"] - (last_form["y"] + last_form["height"]) >= 23.9
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_label("Homepage redirect URL", exact=True).fill(
        "https://example.org/updated"
    )
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_role("status")).to_contain_text("Homepage setting saved")


def test_theme_cycles_and_persists_after_reload(page, login):
    """Verify theme cycles and persists after reload.

    Args:
        page: Playwright browser page used for administrator interactions.
        login: Fixture callable that signs the administrator into the browser page.
    """
    login()
    control = page.get_by_role("button", name="Theme:")
    for mode in ("light", "dark", "system"):
        control.click()
        expect(control).to_have_text("Theme: " + mode)
    control.click()
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")


def test_template_to_evidence_export_and_deletion(
    page, login, live_server, ui_artifacts, browser_engine
):
    """Exercise the full template-to-evidence lifecycle through the real interface.

    Args:
        page: Isolated Playwright administration page.
        login: Fixture that signs in with the temporary administrator credentials.
        live_server: Loopback origin of the temporary test application.
        ui_artifacts: Directory retaining the exported CSV evidence.
        browser_engine: Chromium, Firefox, or WebKit test-engine name.
    """
    login()
    link_id = create_research_link(page, live_server)
    decline_research_visit(page.context.browser, live_server, link_id)
    exported = page.context.request.get(live_server + "/admin/export").json()
    assert len(exported) == 1 and exported[0]["finished"] == 1
    assert exported[0]["browser"] is None and not exported[0]["media"]
    page.goto(live_server + "/admin/visits")
    csv_path = download_evidence_csv(page, browser_engine, ui_artifacts)
    assert link_id in csv_path.read_text()
    page.goto(live_server + "/admin/visits/" + exported[0]["id"])
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator('form[action$="/delete"] button').click()
    page.wait_for_url(live_server + "/admin/visits")
    assert page.context.request.get(live_server + "/admin/export").json() == []


def create_research_link(page, origin):
    """Create a saved template and a link through their actual administration forms.

    Args:
        page: Authenticated Playwright administration page.
        origin: Loopback application origin.

    Returns:
        Newly created internal link identifier.
    """
    page.goto(origin + "/admin/templates/new")
    page.get_by_label("Template name").fill("Research page")
    page.get_by_label("HTML template").fill(
        '<h1>Research</h1><button id="enable-button">Share</button><button id="stop-button">Decline</button>'
    )
    page.get_by_role("button", name="Save template").click()
    template_id = page.url.rsplit("/", 1)[-1]
    page.goto(origin + "/admin/links/new")
    page.get_by_label("Link name").fill("Journalistic research")
    page.get_by_label("Template", exact=True).select_option(template_id)
    page.get_by_role("button", name="Create link").click()
    return page.url.rsplit("/", 1)[-1]


def decline_research_visit(browser, origin, link_id):
    """Open a separate visitor session and decline without requesting device access.

    Args:
        browser: Playwright browser hosting a separate anonymous context.
        origin: Loopback application origin.
        link_id: Public link segment created by the test.
    """
    visitor = browser.new_context()
    try:
        page = visitor.new_page()
        page.goto(origin + "/l/" + link_id)
        expect(page.get_by_role("heading", name="Research", exact=True)).to_be_visible()
        assert page.locator("script[data-flaretrace-runtime]").get_attribute(
            "data-config"
        )
        page.get_by_role("button", name="Decline").click()
        expect(page.locator("body")).to_have_attribute("data-server-closed", "true")
    finally:
        visitor.close()


def download_evidence_csv(page, browser_engine, artifacts):
    """Verify exported CSV bytes and headers without WebKit's attachment-rendering bug.

    Args:
        page: Authenticated visits-list page.
        browser_engine: Browser engine controlling download-event expectations.
        artifacts: Directory retaining the downloaded evidence CSV.

    Returns:
        Path to the saved CSV containing every exported visit.
    """
    destination = artifacts / "evidence.csv"
    if browser_engine == "webkit":
        # Playwright Linux WebKit renders attachments inline; see issue #34076.
        with page.expect_response("**/admin/export?format=csv") as exported_csv:
            page.get_by_role("link", name="Export CSV").click()
        response = exported_csv.value
        assert response.status == 200
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=flaretrace-visits.csv"
        )
        assert response.headers["content-type"].startswith("text/csv")
        destination.write_bytes(response.body())
    else:
        with page.expect_download() as download:
            page.get_by_role("link", name="Export CSV").click()
        assert download.value.suggested_filename == "flaretrace-visits.csv"
        download.value.save_as(str(destination))
    return destination
