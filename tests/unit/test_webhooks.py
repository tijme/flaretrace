"""Exercise webhook payload construction, signing, and best-effort delivery."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from flaretrace.visits.webhooks import (
    build_test_payload,
    build_webhook_payload,
    dispatch_webhook,
    send_webhook,
    sign_payload,
)


def test_test_payload_identifies_itself_as_a_manual_test():
    """Build a distinct, bounded body that is never confused with a real visit event."""
    payload = build_test_payload()
    assert payload["event"] == "webhook.test"
    assert set(payload) == {"event", "sent_at"}


def test_payload_contains_only_bounded_link_and_visit_identifiers():
    """Build a notification body without any visitor evidence fields."""
    link = {"id": "link-1", "name": "Field test"}
    payload = build_webhook_payload(link, "visit-1", "2026-01-01T00:00:00+00:00")
    assert payload == {
        "event": "visit.created",
        "link_id": "link-1",
        "link_name": "Field test",
        "visit_id": "visit-1",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_signature_is_a_deterministic_hmac_sha256_of_the_exact_body():
    """Compute a verifiable signature matching an independent HMAC computation."""
    body = json.dumps({"a": 1}).encode()
    signature = sign_payload("topsecret", body)
    expected = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert signature == expected
    assert hmac.compare_digest(signature, expected)


def test_signature_changes_with_either_secret_or_body():
    """Produce a different signature when the secret or the body content changes."""
    base = sign_payload("secret-a", b'{"x":1}')
    assert sign_payload("secret-b", b'{"x":1}') != base
    assert sign_payload("secret-a", b'{"x":2}') != base


def test_send_webhook_posts_signed_json_and_accepts_success():
    """Post the signed JSON body to the configured URL and accept a 2xx response."""
    response = MagicMock()
    response.__enter__.return_value.status = 200
    with patch(
        "flaretrace.visits.webhooks.urllib.request.urlopen", return_value=response
    ) as opener:
        send_webhook("https://example.com/hook", "secret", {"a": 1})
    request = opener.call_args.args[0]
    assert request.full_url == "https://example.com/hook"
    assert request.get_method() == "POST"
    body = request.data
    assert json.loads(body) == {"a": 1}
    assert request.get_header("X-flaretrace-signature") == sign_payload(
        "secret", body
    )
    assert request.get_header("Content-type") == "application/json"


def test_send_webhook_raises_for_an_error_status():
    """Treat a non-2xx/3xx response as a failed delivery."""
    response = MagicMock()
    response.__enter__.return_value.status = 500
    with patch(
        "flaretrace.visits.webhooks.urllib.request.urlopen", return_value=response
    ):
        with pytest.raises(OSError):
            send_webhook("https://example.com/hook", "secret", {"a": 1})


def test_dispatch_webhook_skips_network_entirely_when_no_url_is_configured():
    """Never contact the network when no webhook URL is configured."""
    with patch("flaretrace.visits.webhooks.urllib.request.urlopen") as opener:
        dispatch_webhook("", "secret", {"id": "link-1", "name": "x"}, "visit-1", "now")
    opener.assert_not_called()


def test_dispatch_webhook_swallows_network_failures():
    """Never propagate a delivery failure back to the visitor-facing request."""
    with patch(
        "flaretrace.visits.webhooks.urllib.request.urlopen",
        side_effect=OSError("unreachable"),
    ):
        dispatch_webhook(
            "https://example.com/hook",
            "secret",
            {"id": "link-1", "name": "x"},
            "visit-1",
            "now",
        )


def test_dispatch_webhook_delivers_when_configured():
    """Deliver exactly one request when a webhook URL is configured."""
    response = MagicMock()
    response.__enter__.return_value.status = 204
    with patch(
        "flaretrace.visits.webhooks.urllib.request.urlopen", return_value=response
    ) as opener:
        dispatch_webhook(
            "https://example.com/hook",
            "secret",
            {"id": "link-1", "name": "Field test"},
            "visit-1",
            "2026-01-01T00:00:00+00:00",
        )
    opener.assert_called_once()
