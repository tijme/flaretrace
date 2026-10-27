"""Gunicorn entrypoint: gunicorn flaretrace.wsgi:app."""

from . import create_app

app = create_app()
