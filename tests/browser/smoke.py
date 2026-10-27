"""Optional real-browser integration checks, using ONLY simulated media/location.

Run against a dedicated temporary installation; this creates and deletes links.
Requires Playwright and Chromium. It is not a runtime application dependency.
"""

import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

#: Origin of the temporary HTTP server serving the synthetic browser scenarios.
BASE = os.environ.get("TRACE_TEST_URL", "http://127.0.0.1:8000")
#: Administrator password injected for the temporary browser-test installation.
PASSWORD = os.environ.get("TRACE_TEST_PASSWORD")
if not PASSWORD:
    test_data = Path(
        os.environ.get("TRACE_TEST_DATA_DIR", "/tmp/flaretrace-browser-qa")
    )
    PASSWORD = (test_data / "admin-password").read_text().strip()
#: Directory for synthetic browser screenshots and diagnostic evidence.
ARTIFACTS = Path(
    os.environ.get(
        "TRACE_TEST_ARTIFACTS", Path(__file__).resolve().parents[2] / "artifacts"
    )
)
ARTIFACTS.mkdir(parents=True, exist_ok=True)
#: Browser initialization script simulating distinct front and rear cameras.
DUAL_CAMERA = "(() => {\n  const devices = navigator.mediaDevices;\n  const original = devices.getUserMedia.bind(devices);\n  window.testStreams = [];\n  devices.getUserMedia = async (constraints) => {\n    const desired = constraints.video;\n    const rear = desired?.deviceId?.exact === 'simulated-rear' || desired?.facingMode?.exact === 'environment';\n    const stream = await original({video: true, audio: false});\n    window.testStreams.push(stream);\n    const track = stream.getVideoTracks()[0];\n    const settings = track.getSettings.bind(track);\n    track.getSettings = () => ({...settings(), deviceId: rear ? 'simulated-rear' : 'simulated-front', facingMode: rear ? 'environment' : 'user'});\n    return stream;\n  };\n  devices.enumerateDevices = async () => [\n    {kind: 'videoinput', deviceId: 'simulated-front', label: 'Simulated front camera'},\n    {kind: 'videoinput', deviceId: 'simulated-rear', label: 'Simulated rear camera'}\n  ];\n})();"
#: Browser initialization script simulating explicit permission denial.
DENIED = "(() => {\n  navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('private-error-marker https://example.invalid/?secret=do-not-store', 'NotAllowedError'); };\n  navigator.geolocation.getCurrentPosition = (_, failure) => setTimeout(() => failure({code: 1, message: 'private-error-marker https://example.invalid/?secret=do-not-store'}), 10);\n  navigator.permissions.query = async () => ({state: 'denied'});\n})();"
#: Browser initialization script allowing permissions only after user activation.
GESTURE = "(() => {\n  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);\n  const query = navigator.permissions.query.bind(navigator.permissions);\n  let initial = true;\n  navigator.mediaDevices.getUserMedia = async (constraints) => {\n    if (initial) { initial = false; throw new DOMException('A user gesture is required by test', 'NotAllowedError'); }\n    return original(constraints);\n  };\n  navigator.permissions.query = async (descriptor) => descriptor.name === 'camera' ? {state: 'prompt'} : query(descriptor);\n})();"
#: Browser initialization script delivering late permissions to test cancellation.
DELAYED = "(() => {\n  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);\n  window.testStreams = [];\n  navigator.mediaDevices.getUserMedia = () => new Promise((resolve) => {\n    window.resolveCameraForTest = async () => {\n      const stream = await original({video:true, audio:false});\n      window.testStreams.push(stream);\n      resolve(stream);\n    };\n  });\n})();"


def config(page):
    """Config for the isolated browser checks.

    Args:
        page: Playwright browser page used for administrator interactions.

    Returns:
        Signed capture configuration decoded from the public page’s data attribute.
    """
    return json.loads(page.locator("[data-config]").get_attribute("data-config"))


def no_overflow(page):
    """No overflow for the isolated browser checks.

    Args:
        page: Playwright browser page used for administrator interactions.
    """
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Horizontal document overflow"
    )


def closed(page, timeout=45000):
    """Closed for the isolated browser checks.

    Args:
        page: Playwright browser page used for administrator interactions.
        timeout: Maximum wait in milliseconds before the scenario fails.
    """
    page.locator('[data-server-closed="true"]').wait_for(
        state="attached", timeout=timeout
    )


class CaptureBrowserChecks:
    """Exercise capture browser checks with isolated application resources.

    Attributes:
        admin: Authenticated administrator browser tab or HTTP client.
        admin_context: Authenticated browser context isolated from synthetic visitors.
        browser: Playwright browser owned by this test scenario.
        checks: Names of successfully completed capture regression scenarios.
        created: Identifiers of synthetic links to delete during scenario cleanup.
        created_templates: Synthetic template identifiers retained for scenario cleanup.
        errors: Uncaught browser errors accumulated for the final regression assertion.
        template_for_link: Mapping of synthetic link identifiers to their saved-template
            identifiers.
    """

    def __init__(self, playwright):
        """Launch synthetic camera-enabled Chromium and an isolated administrator context.

        Args:
            playwright: Started Playwright driver used to launch isolated browser contexts.
        """
        self.browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH"),
            headless=True,
            args=[
                "--no-sandbox",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        )
        self.admin_context = self.browser.new_context(
            viewport={"width": 1440, "height": 1050}
        )
        self.admin = self.admin_context.new_page()
        self.errors = []
        self.created = []
        self.created_templates = []
        self.template_for_link = {}
        self.checks = []
        self.sign_in()

    def sign_in(self):
        """Authenticate the browser with the temporary administrator credentials."""
        self.admin.on("pageerror", lambda event: self.errors.append(str(event)))
        self.admin.goto(BASE + "/admin/login")
        self.admin.get_by_label("Username").fill(
            os.environ.get("TRACE_TEST_USERNAME", "admin")
        )
        self.admin.get_by_label("Password").fill(PASSWORD)
        self.admin.get_by_role("button", name="Sign in").click()
        self.admin.wait_for_url(
            lambda url: url in {BASE + "/admin", BASE + "/admin/initial-setup"}
        )
        if self.admin.url.endswith("/initial-setup"):
            self.admin.get_by_label("Homepage redirect URL (required)").fill(
                "https://example.com/"
            )
            self.admin.get_by_role("button", name="Save and continue").click()
            self.admin.wait_for_url(BASE + "/admin")
        no_overflow(self.admin)
        self.admin.screenshot(
            path=str(ARTIFACTS / "dashboard-desktop.png"), full_page=True
        )

    def create_link(self, name, template=""):
        """Create a synthetic research link through the administrator interface.

        Args:
            name: Name of the option, entity, or field being processed.
            template: Authored HTML used to create a synthetic saved landing template.

        Returns:
            Identifier of the newly created synthetic research link.
        """
        template_id = ""
        if template:
            self.admin.goto(BASE + "/admin/templates/new")
            self.admin.get_by_label("Template name").fill(name)
            self.admin.get_by_label("HTML template").fill(template)
            self.admin.get_by_role("button", name="Save template").click()
            self.admin.wait_for_url("**/admin/templates/*")
            template_id = self.admin.url.rsplit("/", 1)[-1]
            self.created_templates.append(template_id)
        self.admin.goto(BASE + "/admin/links/new")
        self.admin.get_by_label("Link name").fill(name)
        # Capture regressions use blank controls unless the scenario supplies HTML.
        self.admin.get_by_label("Template", exact=True).select_option(template_id)
        self.admin.get_by_role("button", name="Create link").click()
        self.admin.wait_for_url(
            lambda url: url.startswith(BASE + "/admin/links/")
            and url != BASE + "/admin/links/new"
        )
        slug = self.admin.url.rsplit("/", 1)[-1]
        self.created.append(slug)
        self.template_for_link[slug] = template_id
        return slug

    def visitor(self, script=None, mobile=False):
        """Create a separate browser context with deterministic permission and camera behavior.

        Args:
            script: Initialization JavaScript installing deterministic browser permission mocks.
            mobile: Whether to emulate a narrow, touch-enabled mobile viewport.

        Returns:
            Pair containing the new visitor browser context and its page; the caller closes the context.
        """
        context = self.browser.new_context(
            viewport={
                "width": 390 if mobile else 1440,
                "height": 844 if mobile else 1050,
            },
            permissions=["geolocation", "camera"],
            geolocation={"latitude": 51.5007, "longitude": -0.1246, "accuracy": 10},
            is_mobile=mobile,
            has_touch=mobile,
        )
        if script:
            context.add_init_script(script)
        page = context.new_page()
        page.on("pageerror", lambda event: self.errors.append(str(event)))
        return (context, page)

    def record(self, visit_id):
        """Open the visit inspector and read the persisted capture evidence.

        Args:
            visit_id: Stored visit identifier used to join events, groups, and media.

        Returns:
            Exported evidence mapping for the requested visit, including its stored media.
        """
        exported = self.admin_context.request.get(BASE + "/admin/export").json()
        return next((row for row in exported if row["id"] == visit_id))

    def check_dual_camera(self):
        """Exercise dual camera in the running browser."""
        slug = self.create_link("Browser test · simulated dual cameras")
        context, page = self.visitor(DUAL_CAMERA)
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.wait_for_timeout(500)
        assert page.locator("body").get_attribute("data-phase") == "idle"
        assert page.evaluate("window.testStreams.length") == 0
        idle_record = self.record(visit_id)
        assert not idle_record["location"] and idle_record["browser"] is None
        assert idle_record["interacted"] == 0
        assert page.locator("body > button").count() == 2
        page.screenshot(
            path=str(ARTIFACTS / "capture-idle-desktop.png"), full_page=True
        )
        page.locator("#enable-button").click()
        page.locator('[data-phase="recording"]').wait_for(
            state="attached", timeout=15000
        )
        no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / "capture-desktop.png"), full_page=True)
        closed(page)
        assert page.evaluate(
            "window.testStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        row = self.record(visit_id)
        assert len(row["media"]) == 4, row
        assert {(media["kind"], media["facing"]) for media in row["media"]} == {
            ("photo", "front"),
            ("video", "front"),
            ("photo", "rear"),
            ("video", "rear"),
        }
        assert row["location"]["latitude"] == 51.5007
        assert row["finished"] == 1
        diagnostics = row["browser"]["diagnostics"]
        assert set(diagnostics["pageTiming"]) == {
            "responseMs",
            "domReadyMs",
            "loadMs",
            "firstContentfulPaintMs",
        }
        assert all(
            (
                value is None
                or (
                    isinstance(value, (int, float)) and value >= 0 and (value % 10 == 0)
                )
                for value in diagnostics["pageTiming"].values()
            )
        )
        assert isinstance(diagnostics["onlineHint"], bool)
        assert config(page)["id"] not in json.dumps(diagnostics)
        upload_events = [
            event["payload"]
            for event in row["events"]
            if event["kind"] == "diagnostic" and event["payload"]["stage"] == "upload"
        ]
        assert len(upload_events) == 4
        assert all(
            (
                event["outcome"] == "success" and event["httpStatus"] == 201
                for event in upload_events
            )
        )
        assert sum((event["bytes"] for event in upload_events)) == sum(
            (media["size"] for media in row["media"])
        )
        anonymous = self.browser.new_context()
        assert (
            anonymous.request.get(
                BASE + "/admin/media/" + row["media"][0]["id"], max_redirects=0
            ).status
            == 302
        )
        anonymous.close()
        self.admin.goto(BASE + "/admin/visits/" + visit_id)
        self.admin.screenshot(path=str(ARTIFACTS / "visit-desktop.png"), full_page=True)
        no_overflow(self.admin)
        context.close()
        self.checks.append(
            "explicit sharing before sensor access, dual-camera upload, location, protected media, and camera teardown"
        )

    def check_declined_permissions(self):
        """Exercise declined permissions in the running browser."""
        slug = self.create_link("Browser test · permission declined")
        context, page = self.visitor(DENIED, mobile=True)
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.locator("#enable-button").click()
        closed(page)
        assert page.locator("body").get_attribute("data-front-state") == "Declined"
        assert page.locator("body").get_attribute("data-location-state") == "Declined"
        denied_row = self.record(visit_id)
        assert len(denied_row["media"]) == 0
        assert "private-error-marker" not in json.dumps(denied_row)
        no_overflow(page)
        page.screenshot(
            path=str(ARTIFACTS / "capture-declined-mobile.png"), full_page=True
        )
        context.close()
        self.checks.append("denied permissions without repeated prompts or media")

    def check_gesture_retry(self):
        """Exercise gesture retry in the running browser."""
        slug = self.create_link("Browser test · gesture fallback")
        context, page = self.visitor(GESTURE)
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.locator("#enable-button").click()
        page.locator('[data-phase="awaiting-gesture"]').wait_for(
            state="attached", timeout=15000
        )
        page.locator("#enable-button").click()
        closed(page)
        row = self.record(visit_id)
        assert len(row["media"]) == 2, row
        assert {media["facing"] for media in row["media"]} == {"front"}
        context.close()
        self.checks.append("gesture fallback and single-camera detection")

    def check_stop_recording(self):
        """Exercise stop recording in the running browser."""
        slug = self.create_link("Browser test · stop during recording")
        context, page = self.visitor(DUAL_CAMERA, mobile=True)
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.locator("#enable-button").click()
        page.locator('[data-phase="recording"]').wait_for(
            state="attached", timeout=15000
        )
        no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / "capture-mobile.png"), full_page=True)
        page.locator("#stop-button").click()
        closed(page)
        assert page.evaluate(
            "window.testStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        page.wait_for_timeout(600)
        row = self.record(visit_id)
        assert len(row["media"]) <= 1 and all(
            (media["kind"] == "photo" for media in row["media"])
        )
        context.close()
        self.checks.append("stop during recording discards video and closes all tracks")

    def check_late_permission(self):
        """Exercise late permission in the running browser."""
        slug = self.create_link("Browser test · late permission resolution")
        context, page = self.visitor(DELAYED)
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.locator("#enable-button").click()
        page.locator('[data-front-state="Requesting…"]').wait_for(state="attached")
        assert page.evaluate("typeof window.resolveCameraForTest === 'function'")
        page.locator("#stop-button").click()
        page.evaluate("window.resolveCameraForTest()")
        page.wait_for_timeout(400)
        assert page.evaluate(
            "window.testStreams.every(s => s.getTracks().every(t => t.readyState === 'ended'))"
        )
        assert not self.record(visit_id)["media"]
        context.close()
        self.checks.append(
            "camera granted after stop is immediately released without capture"
        )

    def check_decline_before_sharing(self):
        """Exercise decline before sharing in the running browser."""
        slug = self.create_link("Browser test · decline before sharing")
        context, page = self.visitor(DUAL_CAMERA)
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.locator("#stop-button").click()
        closed(page)
        row = self.record(visit_id)
        assert (
            row["interacted"] == 0
            and row["browser"] is None
            and (row["location"] is None)
        )
        assert not row["media"] and (
            not any((event["kind"] == "diagnostic" for event in row["events"]))
        )
        assert page.evaluate("window.testStreams.length") == 0
        context.close()
        self.checks.append(
            "decline before sharing collects no device diagnostics or sensor data"
        )

    def check_missing_optional_apis(self):
        """Exercise missing optional apis in the running browser."""
        slug = self.create_link("Browser test · optional APIs unavailable")
        context, page = self.visitor(DENIED)
        context.add_init_script(
            "performance.getEntriesByType = () => { throw new Error('private-error-marker'); }; window.MediaRecorder = undefined;"
        )
        page.goto(f"{BASE}/l/{slug}")
        visit_id = config(page)["id"]
        page.locator("#enable-button").click()
        closed(page)
        row = self.record(visit_id)
        assert all(
            (
                value is None
                for value in row["browser"]["diagnostics"]["pageTiming"].values()
            )
        )
        assert row["browser"]["diagnostics"]["selectedRecordingFormat"] is None
        assert "private-error-marker" not in json.dumps(row)
        context.close()
        self.checks.append(
            "missing performance/recording APIs degrade safely without leaking error details"
        )

    def check_saved_template(self):
        """Exercise saved template in the running browser."""
        custom_html = """<style>body{margin:0;background:#edf2e5}article{padding:32px}
                #cover{position:fixed;inset:0;background:#edf2e5}</style>
                <article id="cover"><h1>Custom link content</h1><p>HTML and inline CSS.</p>
                <button id="enable-button">Begin</button><button id="stop-button">Stop</button></article>
                <script>document.body.dataset.authoredScript = 'true';</script>
                <form><input name="example"></form><svg viewBox="0 0 10 10"></svg>"""
        slug = self.create_link("Browser test · custom template", custom_html)
        before_preview = len(
            self.admin_context.request.get(BASE + "/admin/export").json()
        )
        self.admin.goto(f"{BASE}/admin/links/{slug}/template/preview")
        self.admin.get_by_role("heading", name="Custom link content").wait_for()
        assert self.admin.locator("#enable-button").is_disabled()
        assert (
            self.admin.locator("body").get_attribute("data-authored-script") == "true"
        )
        assert (
            self.admin.locator("script[data-flaretrace-runtime]").get_attribute(
                "data-config"
            )
            is None
        )
        assert (
            len(self.admin_context.request.get(BASE + "/admin/export").json())
            == before_preview
        )
        context, page = self.visitor(DENIED, mobile=True)
        external_requests = []
        page.on(
            "request",
            lambda response: (
                external_requests.append(response.url)
                if "example.invalid" in response.url
                else None
            ),
        )
        page.goto(f"{BASE}/l/{slug}")
        page.get_by_role("heading", name="Custom link content").wait_for()
        assert page.locator("body").get_attribute("data-authored-script") == "true"
        assert page.locator("#enable-button").is_visible()
        assert page.locator("#stop-button").is_visible()
        assert page.locator(".link-template-content").count() == 0
        no_overflow(page)
        assert page.locator("#enable-button").bounding_box()["height"] < 200
        assert page.locator("#cover").bounding_box()["width"] >= 380
        page.screenshot(
            path=str(ARTIFACTS / "template-capture-mobile.png"), full_page=False
        )
        page.locator("#enable-button").click()
        closed(page)
        assert not external_requests
        context.close()
        self.check_template_editor_and_reset(slug)
        self.checks.append(
            "original HTML/CSS/scripts, in-place controls, isolated preview, and reset to default"
        )

    def check_template_editor_and_reset(self, slug):
        """Check editor layouts and confirm an empty saved template restores default controls.

        Args:
            slug: Synthetic link whose selected template is edited and reset.
        """
        self.admin.goto(f"{BASE}/admin/templates/{self.template_for_link[slug]}")
        self.admin.set_viewport_size({"width": 1440, "height": 1050})
        no_overflow(self.admin)
        self.admin.screenshot(
            path=str(ARTIFACTS / "template-editor-desktop.png"), full_page=True
        )
        self.admin.set_viewport_size({"width": 390, "height": 844})
        no_overflow(self.admin)
        self.admin.screenshot(
            path=str(ARTIFACTS / "template-editor-mobile.png"), full_page=True
        )
        self.admin.get_by_label("HTML template").fill("")
        self.admin.get_by_role("button", name="Save template").click()
        context, page = self.visitor(DENIED)
        page.goto(f"{BASE}/l/{slug}")
        assert page.locator(".link-template-content").count() == 0
        assert page.locator("body > button").count() == 2
        context.close()

    def check_admin_layouts(self):
        """Exercise admin layouts in the running browser."""
        self.admin.set_viewport_size({"width": 1440, "height": 1050})
        self.admin.goto(BASE + "/admin")
        assert self.admin.get_by_role(
            "columnheader", name="Sharing actions"
        ).is_visible()
        self.admin.screenshot(
            path=str(ARTIFACTS / "dashboard-populated-desktop.png"), full_page=True
        )
        self.admin.set_viewport_size({"width": 390, "height": 844})
        no_overflow(self.admin)
        self.admin.screenshot(
            path=str(ARTIFACTS / "dashboard-mobile.png"), full_page=True
        )
        self.admin.goto(BASE + "/admin/links/new")
        no_overflow(self.admin)
        self.admin.screenshot(path=str(ARTIFACTS / "create-mobile.png"), full_page=True)
        assert not self.errors, self.errors
        self.checks.append(
            "desktop/mobile layouts without document overflow or JavaScript errors"
        )

    def report(self):
        """Print the completed scenario counts for the CI log."""
        print(
            json.dumps({"passed": self.checks, "screenshots": str(ARTIFACTS)}, indent=2)
        )

    def close(self):
        """Finalize parsing and save an unfinished reserved control if present."""
        self.admin.goto(BASE + "/admin")
        token = self.admin.locator('input[name="csrf"]').first.get_attribute("value")
        for slug in self.created:
            response = self.admin_context.request.post(
                f"{BASE}/admin/links/{slug}/delete", form={"csrf": token}
            )
            assert response.ok, response.status
        for template_id in self.created_templates:
            response = self.admin_context.request.post(
                f"{BASE}/admin/templates/{template_id}/delete", form={"csrf": token}
            )
            assert response.ok, response.status
        self.browser.close()


def main():
    """Run the smoke checks and propagate any failure."""
    with sync_playwright() as playwright:
        scenario = CaptureBrowserChecks(playwright)
        try:
            scenario.check_dual_camera()
            scenario.check_declined_permissions()
            scenario.check_gesture_retry()
            scenario.check_stop_recording()
            scenario.check_late_permission()
            scenario.check_decline_before_sharing()
            scenario.check_missing_optional_apis()
            scenario.check_saved_template()
            scenario.check_admin_layouts()
            scenario.report()
        finally:
            scenario.close()


if __name__ == "__main__":
    main()
