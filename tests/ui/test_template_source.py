"""Verify authored documents, appended capture behavior, and isolated previews in a browser."""

from playwright.sync_api import expect


def test_original_document_runs_with_appended_controls(page, client, csrf, live_server):
    """Render original scripts, SVGs, and external CSS while binding stop controls.

    Args:
        page: Isolated browser page that records unexpected JavaScript errors.
        client: Authenticated administrator client used to create the saved template.
        csrf: Session-bound token required by administrator forms.
        live_server: Local application origin sharing the client's temporary database.
    """
    page.route(
        "https://assets.example.invalid/theme.css",
        lambda route: route.fulfill(
            content_type="text/css", body="body { background: rgb(1, 2, 3); }"
        ),
    )
    source = """<!DOCTYPE html><html lang="nl"><head>
<base href="https://assets.example.invalid/"><title>Original document</title>
<link rel="stylesheet" href="theme.css"></head><body id="authored" class="original">
<script>window.authoredLoaded = true;</script><svg id="vector" viewBox="0 0 10 10"></svg>
<form id="original-form"><input name="query"></form>
<button id="enable-button">Share</button><a id="stop-button" href="leave">Decline</a>
</body></html>"""
    saved = client.post(
        "/admin/templates/new",
        data={
            "csrf": csrf,
            "name": "Original",
            "template_html": source,
        },
    )
    assert saved.status_code == 302
    link = client.post(
        "/admin/links/new",
        data={
            "csrf": csrf,
            "name": "Original",
            "expiry": "7",
            "template_id": saved.location.rsplit("/", 1)[-1],
        },
    )
    assert link.status_code == 302
    response = page.goto(live_server + "/l/" + link.location.rsplit("/", 1)[-1])
    assert response.text().startswith(source + "\n<script ")
    assert page.title() == "Original document"
    assert page.evaluate("window.authoredLoaded") is True
    expect(page.locator("body")).to_have_attribute("id", "authored")
    expect(page.locator("body")).to_have_css("background-color", "rgb(1, 2, 3)")
    expect(page.locator("#vector")).to_have_count(1)
    expect(page.locator("#original-form input")).to_have_count(1)
    page.get_by_role("button", name="Decline").click()
    expect(page.locator("body")).to_have_attribute("data-server-closed", "true")


def test_preview_scripts_run_without_administrator_origin(
    page, client, csrf, login, live_server
):
    """Run authored preview scripts in an opaque origin with sharing disabled.

    Args:
        page: Isolated browser page used for the authenticated preview.
        client: Administrator client used to save the template without opening a visit.
        csrf: Session-bound token required by the template-save form.
        login: Callable authenticating the browser with synthetic administrator credentials.
        live_server: Local application origin serving the preview response.
    """
    saved = client.post(
        "/admin/templates/new",
        data={
            "csrf": csrf,
            "name": "Preview",
            "template_html": '<button id="enable-button">Share</button>'
            "<script>document.body.dataset.authoredOrigin = window.origin;</script>",
        },
    )
    assert saved.status_code == 302
    login()
    response = page.goto(live_server + saved.location + "/preview")
    expect(page.locator("body")).to_have_attribute("data-authored-origin", "null")
    expect(page.get_by_role("button", name="Share")).to_be_disabled()
    assert "camera=()" in response.headers["permissions-policy"]
    assert "allow-same-origin" not in response.headers["content-security-policy"]
    assert client.get("/admin/export").json == []
