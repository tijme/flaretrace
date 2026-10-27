"""Limits operations for the self-hosted Flaretrace workspace."""

from __future__ import annotations

from collections import defaultdict, deque
import threading
import time


from flask import abort, current_app, request

from flaretrace.links.templates import MAX_TEMPLATE_REQUEST_BYTES


def initialize_limits(application):
    """Give an application its own thread-safe, in-memory request windows.

    Args:
        application: Flask application that owns the limiter state.
    """
    application.extensions["request_rate_lock"] = threading.Lock()
    application.extensions["request_rate_buckets"] = defaultdict(deque)
    application.before_request(template_request_limits)


def template_request_limits():
    """Allow encoded template forms before CSRF parsing; retain default limits on other routes."""
    if request.endpoint == "edit_saved_template" and request.method == "POST":
        request.max_content_length = MAX_TEMPLATE_REQUEST_BYTES
        request.max_form_memory_size = MAX_TEMPLATE_REQUEST_BYTES


def limited(group, limit, seconds=60):
    """Consume one request allowance in a thread-safe, per-peer sliding window.

    Args:
        group: Rate-limit category whose allowance is independent of other categories.
        limit: Maximum number of records or requests accepted for this operation.
        seconds: Width of the rate-limit window in seconds.
    """
    key = (group, request.remote_addr or "unknown")
    stamp = time.monotonic()
    with current_app.extensions["request_rate_lock"]:
        bucket = current_app.extensions["request_rate_buckets"][key]
        while bucket and bucket[0] < stamp - seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            abort(429, "Too many requests. Please try again later.")
        bucket.append(stamp)
        if len(current_app.extensions["request_rate_buckets"]) > 4000:
            for old_key in list(current_app.extensions["request_rate_buckets"]):
                if (
                    not current_app.extensions["request_rate_buckets"][old_key]
                    or current_app.extensions["request_rate_buckets"][old_key][-1]
                    < stamp - 900
                ):
                    del current_app.extensions["request_rate_buckets"][old_key]
