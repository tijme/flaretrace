"""Exercise the admin workspace's home-screen install support: manifest and service worker."""

import json

#: Minimum icon sizes Chrome's installability criteria require in the manifest.
REQUIRED_ICON_SIZES = {"192x192", "512x512"}


def test_service_worker_is_served_at_the_origin_root(client):
    """Serve /sw.js (not only /static/sw.js) so its default scope covers /admin/.

    Args:
        client: HTTP test client with an authenticated administrator session.
    """
    result = client.get("/sw.js")
    assert result.status_code == 200
    assert b'addEventListener("fetch"' in result.data


def test_service_worker_is_reachable_without_authentication(application):
    """Let any browser fetch /sw.js before logging in, as real installability requires.

    Args:
        application: Flask application configured with isolated storage and credentials.
    """
    anonymous = application.test_client()
    assert anonymous.get("/sw.js").status_code == 200


def test_manifest_is_valid_json_scoped_to_the_admin_workspace(client):
    """Expose a manifest naming the app, scoped to /admin/, with both required icon sizes.

    Args:
        client: HTTP test client with an authenticated administrator session.
    """
    result = client.get("/static/manifest.json")
    assert result.status_code == 200
    manifest = json.loads(result.data)
    assert manifest["name"] == "Flaretrace"
    assert manifest["start_url"] == "/admin"
    assert manifest["scope"] == "/admin/"
    assert manifest["display"] == "standalone"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert REQUIRED_ICON_SIZES <= sizes
    for icon in manifest["icons"]:
        asset = client.get("/static/" + icon["src"])
        assert asset.status_code == 200
        assert asset.data, icon["src"]


def test_admin_pages_reference_the_manifest_and_apple_touch_icon(client):
    """Link the manifest and an apple-touch-icon from the authenticated workspace shell.

    Args:
        client: HTTP test client with an authenticated administrator session.
    """
    page = client.get("/admin").text
    assert '<link rel="manifest" href="/static/manifest.json">' in page
    assert 'rel="apple-touch-icon"' in page
    assert '<meta name="theme-color" content="#2b6047">' in page


def test_login_page_also_references_the_manifest_before_authentication(application):
    """Advertise installability on the login page too, before any session exists.

    Args:
        application: Flask application configured with isolated storage and credentials.
    """
    anonymous = application.test_client()
    page = anonymous.get("/admin/login").text
    assert '<link rel="manifest" href="/static/manifest.json">' in page


def test_capture_page_never_references_the_manifest_or_service_worker(client, make_link):
    """Never advertise app installability from a visitor-facing capture page.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    link = make_link()
    page_html = client.get(f"/l/{link}").data
    assert b"manifest.json" not in page_html
    assert b"serviceWorker" not in page_html
