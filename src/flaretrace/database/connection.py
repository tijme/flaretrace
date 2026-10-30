"""Manage request-scoped SQLite connections and backwards-compatible schema upgrades."""

from pathlib import Path
import secrets
import sqlite3

from flask import current_app, g

from . import SCHEMA
from flaretrace.visits.groups import migrate_groups
from flaretrace.links.bundled import install_bundled_template


def get_database():
    """Return this application context's SQLite connection with foreign keys enabled.

    Returns:
        Request-scoped SQLite connection; Flask closes it during context teardown.
    """
    if "database" not in g:
        connection = sqlite3.connect(
            Path(current_app.config["DATA_DIR"]) / "trace.sqlite3", timeout=15
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        g.database = connection
    return g.database


def close_database(error=None):
    """Close the context's connection, including requests that raised an exception.

    Args:
        error: Optional Flask teardown exception; closing does not suppress it.
    """
    connection = g.pop("database", None)
    if connection is not None:
        connection.close()


def add_missing_columns(connection):
    """Add fields introduced after the initial schema without altering saved records.

    Args:
        connection: SQLite connection with existing base tables.
    """
    additions = [
        ("links", "template_html", "TEXT NOT NULL DEFAULT ''"),
        ("links", "public_slug", "TEXT"),
        ("visits", "ip_info", "TEXT"),
        ("links", "template_id", "TEXT"),
        ("visits", "group_id", "TEXT REFERENCES visit_groups(id)"),
        ("links", "max_visits", "INTEGER"),
        ("links", "campaign_id", "TEXT REFERENCES campaigns(id)"),
        ("visits", "is_bot", "INTEGER NOT NULL DEFAULT 0"),
        ("visits", "note", "TEXT NOT NULL DEFAULT ''"),
    ]
    for table_name, column_name, declaration in additions:
        columns = {
            column["name"]
            for column in connection.execute(f"PRAGMA table_info({table_name})")
        }
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}"
            )


def import_inline_templates(connection):
    """Move legacy per-link markup into the reusable template library without duplicates.

    Args:
        connection: SQLite connection with template_id and template_library available.
    """
    legacy_links = connection.execute(
        "SELECT id,template_html FROM links WHERE template_id IS NULL AND template_html != ''"
    ).fetchall()
    for link in legacy_links:
        existing = connection.execute(
            "SELECT id FROM template_library WHERE template_html=?",
            (link["template_html"],),
        ).fetchone()
        template_id = existing["id"] if existing else secrets.token_hex(8)
        if not existing:
            connection.execute(
                "INSERT INTO template_library VALUES(?,?,?)",
                (template_id, "Imported " + link["id"], link["template_html"]),
            )
        connection.execute(
            "UPDATE links SET template_id=? WHERE id=?", (template_id, link["id"])
        )


def initialize_database(application):
    """Register connection teardown and upgrade this installation before serving traffic.

    Args:
        application: Flask application with a writable DATA_DIR setting.
    """
    application.teardown_appcontext(close_database)
    with application.app_context():
        connection = get_database()
        connection.executescript(SCHEMA)
        add_missing_columns(connection)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS visits_group ON visits(group_id,created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS visits_pending_group ON visits(created_at) WHERE group_id IS NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS links_campaign ON links(campaign_id)"
        )
        import_inline_templates(connection)
        install_bundled_template(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS links_public_slug ON links(COALESCE(public_slug,id))"
        )
        connection.commit()
        migrate_groups(connection)
