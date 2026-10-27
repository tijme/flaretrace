"""Render a private Caddy configuration from validated container listener settings."""

import json

from flaretrace.links.templates import MAX_TEMPLATE_REQUEST_BYTES


def reverse_proxy(backend_port):
    """Allow bounded template forms through Caddy; Flask enforces each route's own limit.

    Args:
        backend_port: Private Gunicorn port bound only to the container loopback interface.

    Returns:
        Caddyfile directives that preserve original request hosts and sanitize forwarded headers.
    """
    return f"request_body {{\n        max_size {MAX_TEMPLATE_REQUEST_BYTES}\n    }}\n    reverse_proxy 127.0.0.1:{backend_port}"


def caddy_configuration(settings):
    """Render HTTP or automatically managed HTTPS without external config-file mounts.

    Args:
        settings: Validated RuntimeSettings; paths are quoted and hostnames reject directives.

    Returns:
        Complete Caddyfile, including HTTP readiness and custom-port HTTPS redirects.
    """
    storage = json.dumps(str(settings.data_directory / "tls"))
    mode = "disable_redirects" if settings.auto_tls else "off"
    global_options = f"""{{
    admin off
    http_port {settings.http_port}
    https_port {settings.https_port}
    auto_https {mode}
    storage file_system {{
        root {storage}
    }}
}}
"""
    upstream = reverse_proxy(settings.backend_port)
    if not settings.auto_tls:
        return global_options + f"\n:{settings.http_port} {{\n    {upstream}\n}}\n"
    suffix = "" if settings.https_port == 443 else f":{settings.https_port}"
    origin = f"https://{settings.domain}{suffix}"
    issuer = (
        "tls internal"
        if settings.tls_issuer == "internal"
        else "tls {\n        issuer acme {\n            dir https://acme-v02.api.letsencrypt.org/directory\n        }\n    }"
    )
    return (
        global_options
        + f"""
http://:{settings.http_port} {{
    handle /healthz {{
        reverse_proxy 127.0.0.1:{settings.backend_port}
    }}
    handle {{
        redir {origin}{{uri}} 308
    }}
}}
https://{settings.domain}:{settings.https_port} {{
    {issuer}
    {upstream}
}}
"""
    )
