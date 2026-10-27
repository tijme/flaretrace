"""Optional admin integration checks against a temporary test installation.

Requires Playwright/Chromium and TRACE_TEST_URL + TRACE_TEST_PASSWORD.
Creates only synthetic request records; never calls browser sensor APIs.
"""

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright


class AdminBrowserChecks:
    """Exercise admin browser checks with isolated application resources.

    Attributes:
        anonymous: Unauthenticated browser context used only for synthetic visitor requests.
        base: Origin of the temporary local HTTP server, without a trailing slash.
        browser: Playwright browser owned by this test scenario.
        context: Isolated browser context containing test cookies and permissions.
        created: Identifiers of synthetic links to delete during scenario cleanup.
        csrf: Session-bound administrator form token.
        dashboard: Authenticated administrator dashboard tab observed during live refresh.
        document_requests: Top-level navigations recorded to ensure polling never reloads the page.
        first: Identifier of the first synthetic link, used to check dashboard ordering.
        link_page: Administrator tab showing the selected research link and its groups.
        newer: Signed capture configuration for the newest synthetic visit.
        older: Signed capture configuration for the earlier synthetic visit.
        password: Administrator password for the temporary test installation only.
        second: Identifier of the newest synthetic link used by the live evidence checks.
        visits: Administrator tab showing visit groups and the selected visit inspector.
    """

    def __init__(self, playwright):
        """Launch Chromium and authenticate against the temporary local installation.

        Args:
            playwright: Started Playwright driver used to launch isolated browser contexts.
        """
        self.base = os.environ["TRACE_TEST_URL"].rstrip("/")
        self.password = os.environ["TRACE_TEST_PASSWORD"]
        self.created = []
        self.browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH"),
            headless=True,
            args=["--no-sandbox"],
        )
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 1000}
        )
        self.dashboard = self.context.new_page()
        self.dashboard.goto(self.base + "/admin/login")
        self.dashboard.get_by_label("Username").fill(
            os.environ.get("TRACE_TEST_USERNAME", "admin")
        )
        self.dashboard.get_by_label("Password").fill(self.password)
        self.dashboard.get_by_role("button", name="Sign in").click()
        self.dashboard.wait_for_url(re.compile("/admin(?:/initial-setup)?$"))
        if self.dashboard.url.endswith("/initial-setup"):
            self.dashboard.get_by_label("Homepage redirect URL (required)").fill(
                "https://example.com/"
            )
            self.dashboard.get_by_role("button", name="Save and continue").click()
            self.dashboard.wait_for_url(self.base + "/admin")
        self.csrf = self.dashboard.locator('input[name="csrf"]').first.input_value()
        self.document_requests = []
        self.dashboard.on(
            "request",
            lambda request: (
                self.document_requests.append(request.url)
                if request.resource_type == "document"
                else None
            ),
        )
        self.anonymous = self.browser.new_context()

    def create_link(self, name):
        """Create a synthetic research link through the administrator interface.

        Args:
            name: Name of the option, entity, or field being processed.

        Returns:
            Identifier of the newly created synthetic research link.
        """
        response = self.context.request.post(
            self.base + "/admin/links/new",
            form={"csrf": self.csrf, "name": name, "expiry": "7"},
        )
        assert response.ok
        slug = urlsplit(response.url).path.rsplit("/", 1)[-1]
        self.created.append(slug)
        return slug

    def visit(self, slug):
        """Open a research link and decode its signed browser capture configuration.

        Args:
            slug: Public research-link identifier stored in the links table.

        Returns:
            Capture configuration containing the visit identifier and signed submission token.
        """
        response = self.anonymous.request.get(self.base + "/l/" + slug, max_retries=2)
        match = re.search('data-config="([^"]+)"', response.text())
        return json.loads(html.unescape(match.group(1)))

    def check_live_rows(self):
        """Exercise live rows in the running browser."""
        self.first = self.create_link("Live admin first")
        expect(self.dashboard.locator("tr[data-href]").first).to_have_attribute(
            "data-href", "/admin/links/" + self.first, timeout=6000
        )
        self.second = self.create_link("Live admin newest")
        expect(self.dashboard.locator("tr[data-href]").first).to_have_attribute(
            "data-href", "/admin/links/" + self.second, timeout=6000
        )
        self.visits = self.context.new_page()
        self.visits.goto(self.base + "/admin/visits")
        self.link_page = self.context.new_page()
        self.link_page.goto(self.base + "/admin/links/" + self.second)
        self.older = self.visit(self.second)
        self.newer = self.visit(self.second)
        for page in (self.visits, self.link_page):
            expect(page.locator(".group-toggle").first).to_contain_text(
                "2 clicks", timeout=6000
            )
            page.locator(".group-toggle").first.click()
            expect(page.locator("tr[data-click-id]")).to_have_count(2, timeout=6000)
        expect(
            self.dashboard.locator(
                'tr[data-href="/admin/links/' + self.second + '"] td'
            ).nth(2)
        ).to_have_text("2", timeout=6000)
        assert not self.document_requests, (
            "Background refresh must not navigate the page"
        )
        assert (
            self.visits.locator(
                'tr[data-href="/admin/visits/' + self.older["id"] + '"]'
            ).count()
            == 1
        )

    def check_live_inspector(self):
        """Exercise live inspector in the running browser."""
        self.visits.locator('tr[data-click-id="' + self.newer["id"] + '"]').locator(
            "td"
        ).nth(2).click()
        self.visits.wait_for_url(self.base + "/admin/visits/" + self.newer["id"])
        response = self.anonymous.request.post(
            self.base + "/api/visits/" + self.newer["id"] + "/events",
            data=json.dumps(
                {"kind": "browser", "data": {"language": "synthetic-live-test"}}
            ),
            headers={
                "X-Visit-Token": self.newer["token"],
                "Content-Type": "application/json",
            },
        )
        assert response.ok
        expect(self.visits.locator(".facts-strip")).to_contain_text("Yes", timeout=6000)
        expect(self.visits.locator("#visit-details")).to_contain_text(
            "synthetic-live-test", timeout=6000
        )

    def check_network_recovery(self):
        """Exercise network recovery in the running browser."""
        self.link_page.route(
            "**/admin/links/" + self.second, lambda route: route.abort()
        )
        expect(self.link_page.locator("[data-live-status]")).to_contain_text(
            "Retrying automatically", timeout=6000
        )
        assert self.link_page.locator("tr[data-click-id]").count() == 2
        self.link_page.unroute("**/admin/links/" + self.second)
        expect(self.link_page.locator("[data-live-status]")).to_have_text(
            "Live updates every 2 seconds.", timeout=12000
        )

    def check_mobile_layout(self):
        """Exercise mobile layout in the running browser."""
        artifacts = Path(
            os.environ.get(
                "TRACE_TEST_ARTIFACTS",
                Path(__file__).resolve().parents[2] / "artifacts",
            )
        )
        artifacts.mkdir(parents=True, exist_ok=True)
        self.link_page.screenshot(
            path=str(artifacts / "live-admin-desktop.png"), full_page=True
        )
        self.link_page.set_viewport_size({"width": 390, "height": 844})
        self.link_page.screenshot(
            path=str(artifacts / "live-admin-mobile.png"), full_page=True
        )
        if not self.link_page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth"
        ):
            print(
                self.link_page.evaluate(
                    "Array.from(document.querySelectorAll('body *')).filter(el => {\n                  const box = el.getBoundingClientRect(); return box.right > innerWidth && box.width > 0;\n                }).map(el => ({tag: el.tagName, class: el.className, width: el.getBoundingClientRect().width,\n                  right: el.getBoundingClientRect().right})).slice(0, 15)"
                )
            )
        assert self.link_page.evaluate(
            "document.documentElement.scrollWidth <= innerWidth"
        )
        assert (
            "Records kept until manually deleted"
            in self.dashboard.locator("footer").inner_text()
        )
        print(
            "Passed: newest-first rows, live counts/lists/details, delegated row navigation, no page reload, network recovery, mobile layout, retention copy."
        )

    def close(self):
        """Finalize parsing and save an unfinished reserved control if present."""
        for slug in self.created:
            self.context.request.post(
                self.base + "/admin/links/" + slug + "/delete", form={"csrf": self.csrf}
            )
        self.anonymous.close()
        self.context.close()
        self.browser.close()


def main():
    """Run the admin live checks and propagate any failure."""
    with sync_playwright() as playwright:
        scenario = AdminBrowserChecks(playwright)
        try:
            scenario.check_live_rows()
            scenario.check_live_inspector()
            scenario.check_network_recovery()
            scenario.check_mobile_layout()
        finally:
            scenario.close()


if __name__ == "__main__":
    main()
