"""Flaretrace application package and Flask application factory."""

from .app import create_app

#: Supported public imports exposed by this module.
__all__ = ["create_app"]
