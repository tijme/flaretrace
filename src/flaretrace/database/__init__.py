"""The SQLite schema bundled with Flaretrace."""

from importlib.resources import files

#: Packaged SQLite schema path used to initialize an installation.
SCHEMA = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
