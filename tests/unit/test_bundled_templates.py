"""Verify bundled-template installation, package updates, and preservation of administrator choices."""

import sqlite3

import pytest

from flaretrace.links import bundled


@pytest.fixture
def template_database():
    """Create the small SQLite schema required by the standalone template installer.

    Yields:
        In-memory connection with named columns and no persistent user data.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE template_library(id TEXT PRIMARY KEY, name TEXT, template_html TEXT);"
    )
    yield connection
    connection.close()


def test_bundled_source_has_capture_control_and_editable_policies():
    """Retain the bundled widget's capture entry point and editable collection disclosures."""
    source = bundled.bundled_template_source()
    assert '<!doctype html>' in source
    assert 'id="enable-button"' in source
    assert 'aria-controls="captcha-challenge"' in source
    assert "<!-- EDIT PRIVACY TEXT HERE." in source
    assert "<!-- EDIT TERMS TEXT HERE." in source
    assert 'id="privacy-content"' in source and 'id="terms-content"' in source
    assert "Browser and device information:" in source
    assert "IP and network information:" in source
    assert "precise location where device access is granted" in source
    assert "photos and video recordings captured during verification" in source
    assert "Custom verification, not a Google service." in source


def test_installation_is_idempotent(template_database):
    """Install the bundled template once under its stable identifier and library name.

    Args:
        template_database: Disposable SQLite connection used by the installer.
    """
    bundled.install_bundled_template(template_database)
    bundled.install_bundled_template(template_database)
    rows = template_database.execute("SELECT * FROM template_library").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == bundled.BUNDLED_TEMPLATE_ID
    assert rows[0]["name"] == "Human Verification Captcha"
    assert rows[0]["template_html"] == bundled.bundled_template_source()


@pytest.mark.parametrize("change", ["edit", "rename", "delete"])
def test_package_updates_preserve_administrator_choices(
    template_database, monkeypatch, change
):
    """Keep edited, renamed, and deleted library entries when the shipped file changes.

    Args:
        template_database: Disposable SQLite connection containing the original bundled copy.
        monkeypatch: Restores the simulated new package source after the test.
        change: Administrator action that must survive installation updates.
    """
    bundled.install_bundled_template(template_database)
    if change == "edit":
        template_database.execute(
            "UPDATE template_library SET template_html='<p>Local privacy terms</p>'"
        )
    elif change == "rename":
        template_database.execute(
            "UPDATE template_library SET name='Our research page'"
        )
    else:
        template_database.execute("DELETE FROM template_library")
    before = template_database.execute("SELECT * FROM template_library").fetchall()
    updated = (
        bundled.bundled_template_source() + "\n<!-- Revised bundled privacy copy. -->"
    )
    monkeypatch.setattr(bundled, "bundled_template_source", lambda: updated)
    bundled.install_bundled_template(template_database)
    assert (
        template_database.execute("SELECT * FROM template_library").fetchall() == before
    )


def test_package_updates_refresh_an_untouched_copy(template_database, monkeypatch):
    """Load revised repository HTML on upgrade when no administrator has customized the copy.

    Args:
        template_database: Disposable SQLite connection containing an untouched bundled copy.
        monkeypatch: Restores the packaged template reader after simulating an upgrade.
    """
    bundled.install_bundled_template(template_database)
    updated = (
        bundled.bundled_template_source() + "\n<!-- Updated privacy information. -->"
    )
    monkeypatch.setattr(bundled, "bundled_template_source", lambda: updated)
    bundled.install_bundled_template(template_database)
    row = template_database.execute(
        "SELECT template_html FROM template_library"
    ).fetchone()
    assert row["template_html"] == updated
