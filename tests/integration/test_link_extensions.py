"""Exercise visit limits, bot filtering, campaigns, visit notes, QR codes, and webhooks."""

import json
from unittest.mock import MagicMock, patch

import pytest

from flaretrace.visits.webhooks import sign_payload

#: Ordinary Android/Chrome user agent used to simulate a genuine visitor.
NORMAL_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "Chrome/120.0 Mobile Safari/537.36"
)
#: Known link-preview user agent used to simulate an automated fetch.
BOT_AGENT = "facebookexternalhit/1.1"


def test_one_time_link_rejects_a_second_real_visit(client, make_link):
    """A max_visits=1 link accepts exactly one real visitor and then expires.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    link = make_link(max_visits="1")
    first = client.get(f"/l/{link}", headers={"User-Agent": NORMAL_AGENT})
    assert first.status_code == 200
    second = client.get(f"/l/{link}", headers={"User-Agent": NORMAL_AGENT})
    assert second.status_code == 410


def test_bot_requests_never_count_toward_the_visit_limit(client, make_link):
    """A bot/preview fetch does not consume a one-time link's single allowed visit.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    link = make_link(max_visits="1")
    assert client.get(f"/l/{link}", headers={"User-Agent": BOT_AGENT}).status_code == 200
    assert client.get(f"/l/{link}", headers={"User-Agent": BOT_AGENT}).status_code == 200
    human = client.get(f"/l/{link}", headers={"User-Agent": NORMAL_AGENT})
    assert human.status_code == 200
    blocked = client.get(f"/l/{link}", headers={"User-Agent": NORMAL_AGENT})
    assert blocked.status_code == 410


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "100001", "1.5"])
def test_new_link_rejects_an_invalid_visit_limit(client, csrf, raw):
    """Reject a non-integer, zero, negative, or out-of-range visit limit at creation.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        raw: Invalid max_visits form value expected to be rejected.
    """
    result = client.post(
        "/admin/links/new",
        data={"csrf": csrf, "name": "x", "expiry": "never", "max_visits": raw},
    )
    assert result.status_code == 400


def test_new_link_accepts_a_blank_visit_limit_as_unlimited(client, make_link, database):
    """An empty max_visits field stores NULL, meaning unlimited visits.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    link = make_link(max_visits="")
    row = database.execute(
        "SELECT max_visits FROM links WHERE id=?", (link,)
    ).fetchone()
    assert row["max_visits"] is None


def test_a_known_bot_user_agent_is_persisted_as_is_bot(client, make_link, database):
    """Persist is_bot=1 for a request using a known crawler/preview user agent.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    link = make_link()
    client.get(f"/l/{link}", headers={"User-Agent": BOT_AGENT})
    row = database.execute(
        "SELECT is_bot FROM visits WHERE link_id=?", (link,)
    ).fetchone()
    assert row["is_bot"] == 1


def test_an_ordinary_user_agent_is_not_flagged(client, make_link, database):
    """Persist is_bot=0 for a request using a real mobile/desktop browser string.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    link = make_link()
    client.get(f"/l/{link}", headers={"User-Agent": NORMAL_AGENT})
    row = database.execute(
        "SELECT is_bot FROM visits WHERE link_id=?", (link,)
    ).fetchone()
    assert row["is_bot"] == 0


def test_hiding_automated_requests_removes_a_bot_only_group_from_the_list(
    client, make_link
):
    """The bots=hide filter excludes a group whose only click was automated.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    make_link_a = make_link()
    client.get(f"/l/{make_link_a}", headers={"User-Agent": BOT_AGENT})
    shown = client.get("/admin/visits").text
    hidden = client.get("/admin/visits?bots=hide").text
    assert "No visits yet" not in shown
    assert "No visits yet" in hidden


def test_campaigns_page_renders_for_an_authenticated_administrator(client):
    """Render the campaign overview page without error when none exist yet.

    Args:
        client: HTTP test client with an authenticated administrator session.
    """
    result = client.get("/admin/campaigns")
    assert result.status_code == 200
    assert b"No campaigns yet" in result.data


def test_new_campaign_form_renders_and_rejects_a_blank_name(client, csrf):
    """Render the creation form on GET and reject a blank name on POST.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
    """
    assert client.get("/admin/campaigns/new").status_code == 200
    rejected = client.post("/admin/campaigns/new", data={"csrf": csrf, "name": "  "})
    assert rejected.status_code == 400


def test_create_campaign_and_assign_a_link_to_it(client, csrf, make_link, database):
    """Create a campaign, assign an existing link to it, and persist the assignment.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    created = client.post("/admin/campaigns/new", data={"csrf": csrf, "name": "Q4"})
    assert created.status_code == 302
    campaign_id = created.location.rsplit("/", 1)[-1]
    link = make_link()
    assign = client.post(
        f"/admin/links/{link}/campaign", data={"csrf": csrf, "campaign_id": campaign_id}
    )
    assert assign.status_code == 302
    row = database.execute(
        "SELECT campaign_id FROM links WHERE id=?", (link,)
    ).fetchone()
    assert row["campaign_id"] == campaign_id
    detail = client.get(f"/admin/campaigns/{campaign_id}")
    assert detail.status_code == 200
    assert link.encode() in detail.data


def test_new_link_can_be_created_directly_inside_a_campaign(client, csrf, database):
    """Create a link that is immediately assigned to a chosen campaign.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        database: SQLite connection to the current test installation.
    """
    created = client.post(
        "/admin/campaigns/new", data={"csrf": csrf, "name": "Direct"}
    )
    campaign_id = created.location.rsplit("/", 1)[-1]
    result = client.post(
        "/admin/links/new",
        data={
            "csrf": csrf,
            "name": "x",
            "expiry": "never",
            "campaign_id": campaign_id,
        },
    )
    assert result.status_code == 302
    slug = result.location.rsplit("/", 1)[-1]
    row = database.execute(
        "SELECT campaign_id FROM links WHERE id=?", (slug,)
    ).fetchone()
    assert row["campaign_id"] == campaign_id


def test_creating_a_link_with_an_unknown_campaign_is_rejected(client, csrf):
    """Reject creating a link that references a campaign identifier that does not exist.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
    """
    result = client.post(
        "/admin/links/new",
        data={
            "csrf": csrf,
            "name": "x",
            "expiry": "never",
            "campaign_id": "nonexistent",
        },
    )
    assert result.status_code == 400


def test_assigning_an_unknown_campaign_is_rejected(client, csrf, make_link):
    """Reject assigning a link to a campaign identifier that does not exist.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    link = make_link()
    result = client.post(
        f"/admin/links/{link}/campaign", data={"csrf": csrf, "campaign_id": "nope"}
    )
    assert result.status_code == 400


def test_deleting_a_campaign_unassigns_its_links_without_deleting_them(
    client, csrf, make_link, database
):
    """Deleting a campaign keeps its links and visits, only clearing campaign_id.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    created = client.post("/admin/campaigns/new", data={"csrf": csrf, "name": "Temp"})
    campaign_id = created.location.rsplit("/", 1)[-1]
    link = make_link(campaign_id=campaign_id)
    delete = client.post(f"/admin/campaigns/{campaign_id}/delete", data={"csrf": csrf})
    assert delete.status_code == 302
    row = database.execute(
        "SELECT campaign_id FROM links WHERE id=?", (link,)
    ).fetchone()
    assert row is not None and row["campaign_id"] is None
    assert client.get(f"/admin/campaigns/{campaign_id}").status_code == 404


def test_export_scoped_to_a_campaign_includes_only_its_links(
    client, csrf, make_link, make_visit
):
    """A campaign-scoped export only includes visits from links in that campaign.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
    """
    created = client.post(
        "/admin/campaigns/new", data={"csrf": csrf, "name": "Scoped"}
    )
    campaign_id = created.location.rsplit("/", 1)[-1]
    in_campaign = make_link(campaign_id=campaign_id)
    outside = make_link()
    make_visit(link=in_campaign)
    make_visit(link=outside)
    rows = client.get(f"/admin/export?campaign={campaign_id}").json
    assert len(rows) == 1
    assert rows[0]["link_id"] == in_campaign


def test_saving_a_note_persists_and_is_included_in_export(
    client, csrf, make_visit, database
):
    """Save an administrator note on a visit and expose it through the JSON export.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        database: SQLite connection to the current test installation.
    """
    visit = make_visit()
    result = client.post(
        f"/admin/visits/{visit['id']}/note",
        data={"csrf": csrf, "note": "Confirmed target"},
    )
    assert result.status_code == 302
    row = database.execute(
        "SELECT note FROM visits WHERE id=?", (visit["id"],)
    ).fetchone()
    assert row["note"] == "Confirmed target"
    exported = client.get("/admin/export").json[0]
    assert exported["note"] == "Confirmed target"


def test_note_on_an_unknown_visit_is_rejected(client, csrf):
    """Reject saving a note against a visit identifier that does not exist.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
    """
    result = client.post(
        "/admin/visits/does-not-exist/note", data={"csrf": csrf, "note": "x"}
    )
    assert result.status_code == 404


def test_qr_route_serves_an_svg_for_the_links_public_url(client, make_link):
    """Serve a black-on-white SVG QR code for an authenticated administrator.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    link = make_link()
    result = client.get(f"/admin/links/{link}/qr.svg")
    assert result.status_code == 200
    assert result.mimetype == "image/svg+xml"
    assert result.data.startswith(b"<svg")


def test_qr_route_requires_authentication(application):
    """Reject an unauthenticated request for a link's QR code.

    Args:
        application: Flask application configured with isolated storage and credentials.
    """
    anonymous = application.test_client()
    assert anonymous.get("/admin/links/anything/qr.svg").status_code == 302


def test_capture_notifies_a_configured_webhook_with_a_valid_signature(
    client, csrf, make_link, database
):
    """Deliver exactly one signed webhook request when a link is opened.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    saved = client.post(
        "/admin/setup/webhook",
        data={"csrf": csrf, "webhook_url": "https://hooks.example.com/notify"},
    )
    assert saved.status_code == 302
    secret = database.execute(
        "SELECT value FROM settings WHERE key='webhook_secret'"
    ).fetchone()["value"]
    link = make_link()
    response = MagicMock()
    response.__enter__.return_value.status = 200
    with patch(
        "flaretrace.visits.webhooks.urllib.request.urlopen", return_value=response
    ) as opener:
        client.get(f"/l/{link}")
    opener.assert_called_once()
    sent = opener.call_args.args[0]
    body = sent.data
    assert json.loads(body)["link_id"] == link
    assert json.loads(body)["event"] == "visit.created"
    assert sent.get_header("X-flaretrace-signature") == sign_payload(secret, body)


def test_capture_skips_the_network_when_no_webhook_is_configured(client, make_link):
    """Never contact the network from the visitor-facing route without a webhook URL.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    link = make_link()
    with patch("flaretrace.visits.webhooks.urllib.request.urlopen") as opener:
        client.get(f"/l/{link}")
    opener.assert_not_called()


def test_webhook_delivery_failure_never_breaks_the_capture_page(
    client, csrf, make_link
):
    """Keep serving the capture page even when webhook delivery raises.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
    """
    client.post(
        "/admin/setup/webhook",
        data={"csrf": csrf, "webhook_url": "https://hooks.example.com/notify"},
    )
    link = make_link()
    with patch(
        "flaretrace.visits.webhooks.urllib.request.urlopen",
        side_effect=OSError("unreachable"),
    ):
        result = client.get(f"/l/{link}")
    assert result.status_code == 200


def test_invalid_webhook_url_is_rejected_and_disabling_clears_it(client, csrf, database):
    """Reject a non-HTTP(S) webhook URL, and allow clearing it back to disabled.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        database: SQLite connection to the current test installation.
    """
    rejected = client.post(
        "/admin/setup/webhook", data={"csrf": csrf, "webhook_url": "javascript:alert(1)"}
    )
    assert rejected.status_code == 302
    assert (
        database.execute(
            "SELECT value FROM settings WHERE key='webhook_url'"
        ).fetchone()
        is None
    )
    client.post(
        "/admin/setup/webhook",
        data={"csrf": csrf, "webhook_url": "https://hooks.example.com/notify"},
    )
    cleared = client.post("/admin/setup/webhook", data={"csrf": csrf, "webhook_url": ""})
    assert cleared.status_code == 302
    row = database.execute(
        "SELECT value FROM settings WHERE key='webhook_url'"
    ).fetchone()
    assert row["value"] == ""
