"""Register HTTP handlers without adding prefixes to established endpoint names."""

from . import authentication, capture, links, settings, system, templates, visits


def register_routes(application):
    """Attach public and authenticated views while keeping existing URL names stable.

    Args:
        application: Flask application receiving its HTTP route table.
    """
    for module in (authentication, capture, links, settings, system, templates, visits):
        for path, handler_name, methods in module.ROUTES:
            application.add_url_rule(
                path, view_func=getattr(module, handler_name), methods=methods
            )
    for status_code in (400, 403, 404, 409, 410, 413, 415, 429):
        application.register_error_handler(status_code, system.error_page)
