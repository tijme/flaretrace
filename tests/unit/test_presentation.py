"""Exercise readable visit summaries across normal, missing, and malformed data."""

import json

import pytest

from flaretrace.visits.presentation import (
    browser_name,
    device_name,
    event_description,
    format_bytes,
    format_time,
    mapping,
    present_visit,
    text,
)


@pytest.mark.parametrize(
    "value, expected", [(None, {}), ([], {}), ({"a": 1}, {"a": 1})]
)
def test_mapping(value, expected):
    """Verify malformed metadata becomes an empty mapping.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        expected: Expected normalized value or public response for this parametrized case.
    """
    assert mapping(value) == expected


def test_text_bounds_and_defaults():
    """Verify text bounds and defaults."""
    assert text("  Reported  ") == "Reported"
    assert text(None) == "Not reported"
    assert text("", "Fallback") == "Fallback"
    assert len(text("x" * 1000)) == 240


@pytest.mark.parametrize(
    "value, mode, expected",
    [
        ("2026-10-25T14:15:16Z", "clock", "14:15:16"),
        ("2026-10-25T15:15:16+01:00", "date", "25 Oct 2026"),
        ("2026-10-25T14:15:16", "full", "25 Oct 2026 · 14:15:16 UTC"),
        (None, "full", "Time unavailable"),
        ("invalid", "full", "Time unavailable"),
    ],
)
def test_times(value, mode, expected):
    """Verify UTC conversion and invalid timestamp fallbacks.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        mode: Requested display format for the stored timestamp.
        expected: Expected normalized value or public response for this parametrized case.
    """
    assert format_time(value, mode) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, "Size unavailable"),
        (None, "Size unavailable"),
        (-1, "Size unavailable"),
        (float("inf"), "Size unavailable"),
        (0, "0 B"),
        (1024, "1.0 KiB"),
        (1024**2, "1.0 MiB"),
        (1024**4, "1024.0 GiB"),
    ],
)
def test_sizes(value, expected):
    """Verify binary size units and invalid byte-count fallbacks.

    Args:
        value: Input value to normalize or validate; malformed input follows the documented
            fallback.
        expected: Expected normalized value or public response for this parametrized case.
    """
    assert format_bytes(value) == expected


@pytest.mark.parametrize(
    "agent, expected",
    [
        ("Edg/126.0", "Edge 126"),
        ("FxiOS/120.0", "Firefox 120"),
        ("OPR/95.0 Chrome/100.0", "Opera 95"),
        ("CriOS/120.0", "Chrome 120"),
        ("Version/17.0 Safari/605.1", "Safari 17"),
        ("unknown", "Unknown browser"),
    ],
)
def test_browser_labels(agent, expected):
    """Verify browser labels.

    Args:
        agent: Browser user-agent text supplied with the visit.
        expected: Expected normalized value or public response for this parametrized case.
    """
    assert browser_name(agent) == expected


@pytest.mark.parametrize(
    "agent, expected",
    [
        ("iPad", "iPad"),
        ("iPhone", "iPhone"),
        ("Android", "Android device"),
        ("Windows", "Windows computer"),
        ("Macintosh", "Mac computer"),
        ("Linux", "Linux computer"),
    ],
)
def test_device_labels(agent, expected):
    """Verify device labels.

    Args:
        agent: Browser user-agent text supplied with the visit.
        expected: Expected normalized value or public response for this parametrized case.
    """
    assert device_name(agent, None) == expected


def test_unknown_device_uses_reported_platform():
    """Verify unknown device uses reported platform."""
    assert device_name("", "Other platform") == "Other platform"
    assert device_name("", None) == "Unknown device"


@pytest.mark.parametrize(
    "kind, payload, title",
    [
        ("notice_shown", {}, "Sharing activated"),
        ("browser", {}, "Device details received"),
        ("location", {}, "Location received"),
        (
            "permission",
            {"feature": "location", "state": "denied", "reason": "permission_denied"},
            "Location access declined",
        ),
        (
            "permission",
            {"feature": "camera", "state": "unexpected"},
            "Camera access reported",
        ),
        ("capture_started", {"facing": "rear"}, "Rear camera started"),
        (
            "capture_error",
            {"facing": "front", "reason": "NotFoundError"},
            "Front camera could not finish",
        ),
        (
            "diagnostic",
            {
                "stage": "upload",
                "outcome": "error",
                "mediaKind": "video",
                "facing": "rear",
            },
            "Rear video failed",
        ),
        ("diagnostic", {"outcome": "unsupported"}, "Video recording not supported"),
        (
            "diagnostic",
            {"outcome": "success", "mediaKind": "photo", "bytes": 1024},
            "Photo uploaded",
        ),
        ("finished", {"reason": "declined"}, "Sharing declined"),
        ("finished", {"reason": "stopped"}, "Sharing stopped"),
        ("finished", {"reason": "page_hidden"}, "Sharing ended in the background"),
        ("finished", {"reason": "complete"}, "Session completed"),
        ("finished", {}, "Session ended"),
        ("custom_event", {}, "Custom event"),
    ],
)
def test_event_titles(kind, payload, title):
    """Verify event titles.

    Args:
        kind: Validated event category recorded alongside the JSON payload.
        payload: Validated JSON-compatible event details.
        title: Expected human-readable event title shown by the inspector.
    """
    assert event_description(kind, payload)[0] == title


def test_redundant_successful_recording_event_is_not_displayed():
    """Verify redundant successful recording event is not displayed."""
    assert (
        event_description("diagnostic", {"stage": "recording", "outcome": "success"})
        is None
    )


@pytest.mark.parametrize(
    "reason, finished, interacted, status",
    [
        ("declined", 1, 0, "Sharing declined"),
        ("stopped", 1, 1, "Stopped"),
        ("page_hidden", 1, 1, "Stopped"),
        ("complete", 1, 1, "Session completed"),
        ("unknown", 1, 1, "Session ended"),
        (None, 0, 1, "Sharing started"),
        (None, 0, 0, "Request only"),
    ],
)
def test_session_outcomes(reason, finished, interacted, status):
    """Verify session outcomes.

    Args:
        reason: Public completion or permission-error code supplied by the test case.
        finished: Whether the persisted visit has been explicitly closed.
        interacted: Whether browser metadata was shared for the stored visit.
        status: Expected public HTTP status or inspector state for this fixture.
    """
    visit = {
        "user_agent": "",
        "referrer": "",
        "created_at": "2026-10-25T00:00:00Z",
        "finished": finished,
        "interacted": interacted,
    }
    events = (
        [
            {
                "id": 1,
                "created_at": visit["created_at"],
                "kind": "finished",
                "payload": json.dumps({"reason": reason}),
            }
        ]
        if reason
        else []
    )
    summary = present_visit(visit, {}, events, [])
    assert summary["status"] == status
    assert summary["timeline"][-1]["title"] == "Link requested"


@pytest.mark.parametrize(
    "state, label",
    [
        ("granted", "Allowed · nothing received"),
        ("denied", "Declined"),
        ("error", "Unavailable"),
        ("unsupported", "Not supported"),
        ("blocked", "Blocked"),
    ],
)
def test_permission_summaries_and_malformed_events(state, label):
    """Verify permission summaries and malformed events.

    Args:
        state: Synthetic permission or recording lifecycle state.
        label: Expected readable label rendered for the supplied state.
    """
    visit = {
        "user_agent": "ExampleBot",
        "referrer": "https://example.com",
        "created_at": "now",
        "finished": 0,
        "interacted": 1,
    }
    events = [
        {
            "id": 3,
            "created_at": "now",
            "kind": "permission",
            "payload": json.dumps({"feature": "location", "state": state}),
        },
        {
            "id": 2,
            "created_at": "now",
            "kind": "permission",
            "payload": '{"feature": []}',
        },
        {"id": 1, "created_at": "now", "kind": "unknown", "payload": "broken"},
    ]
    result = present_visit(
        visit, {"browser": {"viewport": {"width": True, "height": 100}}}, events, []
    )
    assert result["location"]["label"] == label
    assert result["viewport"] == "Not reported"
    assert result["possible_bot"]


def test_received_media_location_and_ip_summary():
    """Verify received media location and ip summary."""
    visit = {
        "user_agent": "Chrome/120",
        "referrer": "",
        "created_at": "now",
        "finished": 1,
        "interacted": 1,
    }
    details = {
        "browser": {
            "viewport": {"width": 390, "height": 844},
            "connection": {"effectiveType": "4g"},
        },
        "location": {"latitude": 1},
        "ip_info": {
            "city": "City",
            "region": "Region",
            "country": "Country",
            "org": "ISP",
            "asn": 42,
        },
    }
    result = present_visit(
        visit,
        details,
        [],
        [{"kind": "photo", "size": 100}, {"kind": "video", "size": 200}],
    )
    assert result["location"]["label"] == result["camera"]["label"] == "Received"
    assert (result["photos"], result["videos"], result["media_bytes"]) == (1, 1, 300)
    assert result["ip_place"] == "City, Region, Country"
    assert result["isp"] == "ISP" and result["asn"] == "42"
