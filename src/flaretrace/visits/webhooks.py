"""Best-effort, signed HTTP notification sent when a tracked link is opened."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request

from flaretrace.utilities import now


def build_test_payload():
    """Build the notification body used to verify a configured webhook endpoint.

    Returns:
        JSON-serializable mapping identifying this as a manual connectivity test,
        distinct from a real visit.created notification.
    """
    return {"event": "webhook.test", "sent_at": now()}


def build_webhook_payload(link, visit_id, created_at):
    """Build the bounded, JSON-serializable notification body for one visit.

    Args:
        link: Stored link row that was just opened.
        visit_id: Newly created visit identifier.
        created_at: Visit creation timestamp in ISO 8601 UTC format.

    Returns:
        JSON-serializable mapping describing the click, without visitor evidence.
    """
    return {
        "event": "visit.created",
        "link_id": link["id"],
        "link_name": link["name"],
        "visit_id": visit_id,
        "created_at": created_at,
    }


def sign_payload(secret, body):
    """Compute an HMAC-SHA256 signature so receivers can verify the sender.

    Args:
        secret: Installation-specific webhook signing secret.
        body: Encoded JSON request body being signed.

    Returns:
        Lowercase hex-encoded signature for the X-Flaretrace-Signature header.
    """
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send_webhook(url, secret, payload, timeout=3):
    """Deliver one signed JSON notification to an administrator-configured endpoint.

    Args:
        url: Administrator-configured HTTP(S) webhook destination.
        secret: Installation-specific webhook signing secret.
        payload: JSON-serializable notification body.
        timeout: Maximum seconds to wait for the request to complete.

    Raises:
        OSError: The network request failed, timed out, or returned an error status.
        ValueError: The payload could not be encoded as JSON.
    """
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Flaretrace-Signature": sign_payload(secret, body),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 400:
            raise OSError(f"Webhook endpoint returned status {response.status}.")


def dispatch_webhook(url, secret, link, visit_id, created_at):
    """Notify an administrator-configured endpoint without ever failing the visit.

    Args:
        url: Administrator-configured HTTP(S) webhook destination; empty to skip.
        secret: Installation-specific webhook signing secret.
        link: Stored link row that was just opened.
        visit_id: Newly created visit identifier.
        created_at: Visit creation timestamp in ISO 8601 UTC format.
    """
    if not url:
        return
    try:
        send_webhook(url, secret, build_webhook_payload(link, visit_id, created_at))
    except (OSError, ValueError):
        pass
