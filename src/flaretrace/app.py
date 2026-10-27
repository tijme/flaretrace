"""Assemble Flaretrace's independently testable configuration, services, and routes."""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import configure_application
from .database.connection import initialize_database
from .limits import initialize_limits
from .routes import register_routes
from .security import check_csrf, security_headers, template_context
from .state import initialize_authentication, initialize_storage
from .utilities import now, safe_referrer, valid_destination
from .visits.presentation import format_bytes, format_time

#: Compatibility imports retained for callers of the original application module.
__all__ = ["create_app", "now", "safe_referrer", "valid_destination"]


def cleanup_records():
    """Return zero for legacy cleanup callers; records are deleted only explicitly.

    Returns:
        Zero; automatic evidence deletion is intentionally disabled.
    """
    return 0


def create_app(test_config=None):
    """Build an isolated Flask application without starting a server.

    Args:
        test_config: Optional mapping overriding environment-derived Flask settings.

    Returns:
        Configured Flask application. Its cleanup_records compatibility method is
        a no-op; storage and authentication state are independent per instance.

    Raises:
        ValueError: Public-origin settings or administrator credentials are invalid.
        OSError: Private installation storage cannot be created or read.
    """
    application = Flask(__name__)
    configure_application(application, test_config)
    initialize_storage(application)
    initialize_authentication(application)
    initialize_limits(application)
    if application.config["TRUST_PROXY"]:
        application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1)
    initialize_database(application)
    application.before_request(check_csrf)
    application.after_request(security_headers)
    application.context_processor(template_context)
    application.jinja_env.filters.update(
        display_time=format_time, file_size=format_bytes
    )
    application.cleanup_records = cleanup_records
    register_routes(application)
    return application
