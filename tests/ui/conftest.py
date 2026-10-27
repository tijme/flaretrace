"""Real browser fixtures run an isolated local server and retain diagnostic artifacts."""

import os
from pathlib import Path
import re
import threading

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Pytest runtest makereport for the isolated ui checks.

    Args:
        item: Pytest test item that receives its completed phase report.
        call: Pytest phase information identifying setup, execution, or teardown.

    Yields:
        Control to pytest’s remaining report hooks; the resumed result is attached to the test item.
    """
    outcome = yield
    setattr(item, "rep_" + call.when, outcome.get_result())


@pytest.fixture
def live_server(application):
    """Live server for the isolated ui checks.

    Args:
        application: Flask application configured with isolated storage and credentials.

    Yields:
        Loopback HTTP origin; the server is shut down when the fixture finishes.
    """
    server = make_server("127.0.0.1", 0, application, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(server.server_port)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser_engine():
    """Browser engine for the isolated ui checks.

    Returns:
        Configured Playwright engine name; Chromium is the default.
    """
    return os.environ.get("CI_BROWSER", "chromium")


@pytest.fixture(scope="session")
def browser(browser_engine):
    """Browser for the isolated ui checks.

    Args:
        browser_engine: Selected Playwright engine: Chromium, Firefox, or WebKit.

    Yields:
        Launched Playwright browser; the fixture closes it after the session.
    """
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_engine).launch(
            headless=True, args=["--no-sandbox"] if browser_engine == "chromium" else []
        )
        yield browser
        browser.close()


@pytest.fixture
def ui_artifacts(request):
    """Ui artifacts for the isolated ui checks.

    Args:
        request: Pytest fixture request providing the current test name and failure state.

    Returns:
        Writable artifact directory unique to the current test scenario.
    """
    root = Path(
        os.environ.get(
            "CI_UI_ARTIFACTS",
            "artifacts/ci/ui/" + os.environ.get("CI_BROWSER", "chromium"),
        )
    )
    output = root / re.sub(r"[^a-zA-Z0-9_.-]+", "-", request.node.name)
    output.mkdir(parents=True, exist_ok=True)
    return output


@pytest.fixture
def page(browser, ui_artifacts, request):
    """Page for the isolated ui checks.

    Args:
        browser: Playwright browser instance controlled by this scenario.
        ui_artifacts: Directory for this scenario’s traces, downloads, and failure screenshots.
        request: Pytest fixture request providing the current test name and failure state.

    Yields:
        Isolated administrator page; teardown saves a screenshot, trace, and browser-error log.
    """
    context = browser.new_context(
        viewport={"width": 1440, "height": 1000}, color_scheme="light"
    )
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    try:
        page.screenshot(path=str(ui_artifacts / "page.png"), full_page=True)
        context.tracing.stop(path=str(ui_artifacts / "trace.zip"))
        (ui_artifacts / "browser-errors.log").write_text("\n".join(errors))
        assert not errors, errors
    finally:
        context.close()


@pytest.fixture
def login(page, live_server):
    """Render the login form or authenticate administrator credentials.

    Args:
        page: Playwright browser page used for administrator interactions.
        live_server: Local HTTP origin and database path for the temporary test installation.

    Returns:
        Zero-argument sign-in callable returning the authenticated browser page.
    """

    def sign_in():
        """Authenticate the browser with the temporary administrator credentials.

        Returns:
            Authenticated administrator browser page after navigation completes.
        """
        page.goto(live_server + "/admin/login")
        page.get_by_label("Username").fill("admin")
        page.get_by_label("Password").fill("feature-test-password")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(re.compile(r"/admin(?:/initial-setup)?$"))
        return page

    return sign_in
