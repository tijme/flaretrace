"""Install an editable consent template while preserving administrator edits and deletions."""

from hashlib import sha256
from importlib.resources import files

from .templates import validate_template

#: Stable library identifier used for the bundled consent template and form selection.
BUNDLED_TEMPLATE_ID = "human-verification-captcha"
#: Administrator-facing library label; the visitor page describes voluntary data sharing.
BUNDLED_TEMPLATE_NAME = "Human Verification Captcha"
#: Installation setting tracking the last bundled source, not the administrator's edits.
BUNDLED_TEMPLATE_VERSION = "bundled-template:" + BUNDLED_TEMPLATE_ID


def bundled_template_source():
    """Read the editable HTML packaged inside the Python distribution and Docker image.

    Returns:
        Original UTF-8 source after the normal template-size validation.
    """
    source = (
        files("flaretrace")
        .joinpath("landing_templates", "human-verification-captcha.html")
        .read_text(encoding="utf-8")
    )
    return validate_template(source)


def source_digest(source):
    """Fingerprint template source to distinguish bundled copies from administrator edits.

    Args:
        source: Complete UTF-8 HTML string from the package or database.

    Returns:
        SHA-256 hexadecimal digest of the exact source.
    """
    return sha256(source.encode("utf-8")).hexdigest()


def install_bundled_template(connection):
    """Seed the library once and refresh untouched copies when packaged HTML changes.

    Args:
        connection: Installation SQLite connection; the caller commits its changes.
    """
    source = bundled_template_source()
    version = source_digest(source)
    previous = connection.execute(
        "SELECT value FROM settings WHERE key=?", (BUNDLED_TEMPLATE_VERSION,)
    ).fetchone()
    if previous and previous["value"] == version:
        return
    if not previous:
        connection.execute(
            "INSERT OR IGNORE INTO template_library VALUES(?,?,?)",
            (BUNDLED_TEMPLATE_ID, BUNDLED_TEMPLATE_NAME, source),
        )
    else:
        refresh_untouched_template(connection, previous["value"], source)
    connection.execute(
        "INSERT OR REPLACE INTO settings VALUES(?,?)",
        (BUNDLED_TEMPLATE_VERSION, version),
    )


def refresh_untouched_template(connection, previous_version, source):
    """Refresh an unchanged library copy without restoring deletions or altering existing links.

    Args:
        connection: Installation SQLite connection used by the template installer.
        previous_version: Fingerprint of the previously shipped source.
        source: Replacement HTML from the newly installed package.
    """
    saved = connection.execute(
        "SELECT name,template_html FROM template_library WHERE id=?",
        (BUNDLED_TEMPLATE_ID,),
    ).fetchone()
    if (
        saved
        and saved["name"] == BUNDLED_TEMPLATE_NAME
        and source_digest(saved["template_html"]) == previous_version
    ):
        connection.execute(
            "UPDATE template_library SET template_html=? WHERE id=?",
            (source, BUNDLED_TEMPLATE_ID),
        )
