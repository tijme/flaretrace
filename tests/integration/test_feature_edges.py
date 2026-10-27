"""Feature regressions for error handling, stored data, and transactional cleanup."""

import json
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from itsdangerous import URLSafeTimedSerializer


@pytest.mark.parametrize(
    "path",
    [
        "/admin/links/missing",
        "/admin/templates/missing",
        "/admin/templates/missing/content",
        "/admin/templates/missing/preview",
        "/admin/visits/missing",
        "/admin/visit-groups/missing",
        "/admin/media/missing",
    ],
)
def test_missing_private_records_return_not_found(client, path):
    """Verify missing private records return not found.

    Args:
        client: HTTP test client with an authenticated administrator session.
        path: Filesystem path identifying the source or test artifact.
    """
    assert client.get(path).status_code == 404


def test_secure_transport_headers(application):
    """Verify secure transport headers.

    Args:
        application: Flask application configured with isolated storage and credentials.
    """
    result = application.test_client().get("/healthz", base_url="https://localhost")
    assert result.status_code == 200
    assert result.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_library_and_content_previews_do_not_create_visits(
    client, csrf, make_link, database
):
    """Verify library and content previews do not create visits.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_link: Fixture factory for creating a research link with chosen settings.
        database: SQLite connection to the current test installation.
    """
    saved = client.post(
        "/admin/templates/new",
        data={
            "csrf": csrf,
            "name": "Feature page",
            "template_html": "<p>Evidence page</p>",
        },
    )
    identifier = saved.location.rsplit("/", 1)[-1]
    link = make_link(template_id=identifier)
    for path in (
        "/admin/templates",
        "/admin/templates/" + identifier,
        "/admin/templates/" + identifier + "/content",
        "/admin/links/" + link + "/template/content",
    ):
        result = client.get(path)
        assert result.status_code == 200
        assert "Evidence page" in result.text or "Feature page" in result.text
    assert database.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0


def test_valid_signed_token_for_deleted_record_is_not_found(application, client):
    """Verify valid signed token for deleted record is not found.

    Args:
        application: Flask application configured with isolated storage and credentials.
        client: HTTP test client with an authenticated administrator session.
    """
    token = URLSafeTimedSerializer(application.secret_key, salt="visitor-v1").dumps(
        "missing"
    )
    result = client.post(
        "/api/visits/missing/events",
        json={"kind": "finished", "data": {}},
        headers={"X-Visit-Token": token},
    )
    assert result.status_code == 404


def test_landing_and_event_rate_limits(client, make_link, make_visit, database):
    """Verify landing and event rate limits.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_link: Fixture factory for creating a research link with chosen settings.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        database: SQLite connection to the current test installation.
    """
    link = make_link()
    for _ in range(40):
        assert client.get("/l/" + link).status_code == 200
    assert client.get("/l/" + link).status_code == 429
    visit = make_visit(link, environ_base={"REMOTE_ADDR": "192.0.2.20"})
    database.executemany(
        "INSERT INTO events(visit_id,created_at,kind,payload) VALUES(?,?,'browser','{}')",
        [(visit["id"], "now")] * 80,
    )
    database.commit()
    result = client.post(
        "/api/visits/" + visit["id"] + "/events",
        json={"kind": "browser", "data": {}},
        headers={"X-Visit-Token": visit["token"]},
    )
    assert result.status_code == 429


@pytest.mark.parametrize(
    "mime, kind, blob, status",
    [
        ("image/jpeg", "photo", b"", 400),
        ("image/jpeg", "photo", b"invalid", 415),
        ("video/webm", "video", b"invalid", 415),
        ("video/mp4", "video", b"invalid", 415),
    ],
)
def test_media_rejects_empty_and_mismatched_payloads(
    client, make_visit, mime, kind, blob, status
):
    """Verify media rejects empty and mismatched payloads.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        mime: MIME type declared by the synthetic media upload.
        kind: Validated event category recorded alongside the JSON payload.
        blob: Synthetic file bytes used to exercise upload signature validation.
        status: Expected public HTTP status or inspector state for this fixture.
    """
    visit = make_visit()
    result = client.post(
        f"/api/visits/{visit['id']}/media?kind={kind}&facing=front",
        data=blob,
        content_type=mime,
        headers={"X-Visit-Token": visit["token"]},
    )
    assert result.status_code == status


def test_failed_media_insert_rolls_back_and_removes_file(
    client, make_visit, database, application
):
    """Verify failed media insert rolls back and removes file.

    Args:
        client: HTTP test client with an authenticated administrator session.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        database: SQLite connection to the current test installation.
        application: Flask application configured with isolated storage and credentials.
    """
    visit = make_visit()
    database.execute(
        "CREATE TRIGGER fail_media BEFORE INSERT ON media BEGIN SELECT RAISE(FAIL,'simulated disk failure'); END"
    )
    database.commit()
    path = f"/api/visits/{visit['id']}/media?kind=photo&facing=front"
    with pytest.raises(sqlite3.IntegrityError):
        client.post(
            path,
            data=b"\xff\xd8\xfftest",
            content_type="image/jpeg",
            headers={"X-Visit-Token": visit["token"]},
        )
    assert database.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    assert not list((Path(application.config["DATA_DIR"]) / "media").iterdir())
    database.execute("DROP TRIGGER fail_media")
    database.commit()
    assert (
        client.post(
            path,
            data=b"\xff\xd8\xfftest",
            content_type="image/jpeg",
            headers={"X-Visit-Token": visit["token"]},
        ).status_code
        == 201
    )


def test_failed_deletion_preserves_record(client, csrf, make_visit, database):
    """Verify failed deletion preserves record.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        database: SQLite connection to the current test installation.
    """
    visit = make_visit()
    database.execute(
        "CREATE TRIGGER fail_delete BEFORE DELETE ON visits BEGIN SELECT RAISE(FAIL,'simulated failure'); END"
    )
    database.commit()
    with pytest.raises(sqlite3.IntegrityError):
        client.post(f"/admin/visits/{visit['id']}/delete", data={"csrf": csrf})
    assert (
        database.execute(
            "SELECT COUNT(*) FROM visits WHERE id=?", (visit["id"],)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "payload", [b"invalid json", b"[]", b'{"success": false}', b"x" * 65537]
)
def test_failed_ip_lookup_does_not_change_record(
    client, csrf, make_visit, database, payload
):
    """Verify failed ip lookup does not change record.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        database: SQLite connection to the current test installation.
        payload: Validated JSON-compatible event details.
    """
    visit = make_visit(environ_base={"REMOTE_ADDR": "8.8.8.8"})
    response = MagicMock()
    response.__enter__.return_value.read.return_value = payload
    with patch(
        "flaretrace.visits.enrichment.urllib.request.urlopen", return_value=response
    ):
        result = client.post(f"/admin/visits/{visit['id']}/enrich", data={"csrf": csrf})
    assert result.status_code == 302
    assert (
        database.execute(
            "SELECT ip_info FROM visits WHERE id=?", (visit["id"],)
        ).fetchone()[0]
        is None
    )


def test_private_ip_never_contacts_lookup_provider(client, csrf, make_visit):
    """Verify private ip never contacts lookup provider.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
    """
    visit = make_visit()
    with patch("flaretrace.visits.enrichment.urllib.request.urlopen") as lookup:
        assert (
            client.post(
                f"/admin/visits/{visit['id']}/enrich", data={"csrf": csrf}
            ).status_code
            == 302
        )
    lookup.assert_not_called()


def test_ip_lookup_reuses_cached_result_for_another_click(
    client, csrf, make_visit, database
):
    """Verify ip lookup reuses cached result for another click.

    Args:
        client: HTTP test client with an authenticated administrator session.
        csrf: Session-bound token required by administrator POST forms.
        make_visit: Fixture factory for opening a link and retrieving a signed visit token.
        database: SQLite connection to the current test installation.
    """
    first = make_visit(environ_base={"REMOTE_ADDR": "8.8.8.8"})
    second = make_visit(environ_base={"REMOTE_ADDR": "8.8.8.8"})
    database.execute(
        "UPDATE visits SET ip_info=? WHERE id=?",
        (json.dumps({"country": "Example"}), first["id"]),
    )
    database.commit()
    with patch("flaretrace.visits.enrichment.urllib.request.urlopen") as lookup:
        assert (
            client.post(
                f"/admin/visits/{second['id']}/enrich", data={"csrf": csrf}
            ).status_code
            == 302
        )
    lookup.assert_not_called()
    assert (
        json.loads(
            database.execute(
                "SELECT ip_info FROM visits WHERE id=?", (second["id"],)
            ).fetchone()[0]
        )["country"]
        == "Example"
    )
