"""Validate and atomically persist the four bounded photo/video captures per visit."""

import secrets

from flask import abort

from flaretrace.utilities import now

#: Accepted MIME types mapped to their capture kind and on-disk extension.
MEDIA_FORMATS = {
    "image/jpeg": ("photo", ".jpg"),
    "video/webm": ("video", ".webm"),
    "video/mp4": ("video", ".mp4"),
}


def validate_media(kind, facing, mime, contents):
    """Validate a capture's type, camera direction, nonempty body, and file signature.

    Args:
        kind: Photo or video capture kind supplied by the visitor runtime.
        facing: Front or rear camera identifier.
        mime: Request MIME type without optional codec parameters.
        contents: Uploaded bytes, already bounded by Flask's request-size limit.

    Returns:
        Allowed file extension for the validated capture.

    Raises:
        BadRequest: The uploaded body is empty.
        UnsupportedMediaType: Kind, facing, MIME type, or signature is invalid.
    """
    if (
        mime not in MEDIA_FORMATS
        or MEDIA_FORMATS[mime][0] != kind
        or facing not in {"front", "rear"}
    ):
        abort(415, "Unsupported capture format.")
    if not contents:
        abort(400, "The capture is empty.")
    signatures = {
        "image/jpeg": contents.startswith(b"\xff\xd8\xff"),
        "video/webm": contents.startswith(b"\x1aE\xdf\xa3"),
        "video/mp4": contents[4:8] == b"ftyp",
    }
    if not signatures[mime]:
        abort(415, "The capture does not match its media type.")
    return MEDIA_FORMATS[mime][1]


def check_upload_limit(connection, visit_id, kind, facing, size):
    """Reject closed sessions, duplicate captures, and exceeded session upload limits.

    Args:
        connection: SQLite connection already holding the upload write transaction.
        visit_id: Visit receiving this capture.
        kind: Photo or video capture kind.
        facing: Front or rear camera identifier.
        size: Number of uploaded bytes.

    Raises:
        Conflict: The session is closed or the same kind/facing was already saved.
        RequestEntityTooLarge: Four files or 20 MiB would be exceeded.
    """
    visit = connection.execute(
        "SELECT finished FROM visits WHERE id=?", (visit_id,)
    ).fetchone()
    if not visit or visit["finished"]:
        abort(409, "This capture session is already closed.")
    existing = connection.execute(
        "SELECT kind,facing,size FROM media WHERE visit_id=?", (visit_id,)
    ).fetchall()
    if (
        len(existing) >= 4
        or sum(media["size"] for media in existing) + size > 20 * 1024 * 1024
    ):
        abort(413, "Session upload limit reached.")
    if any(media["kind"] == kind and media["facing"] == facing for media in existing):
        abort(409, "This capture has already been uploaded.")


def save_media(connection, directory, visit_id, kind, facing, mime, contents):
    """Commit a capture and its metadata together, removing orphan files on failure.

    Args:
        connection: SQLite connection with no active write transaction.
        directory: Private media directory as a Path.
        visit_id: Visit receiving the file.
        kind: Photo or video capture kind.
        facing: Front or rear camera identifier.
        mime: JPEG, WebM, or MP4 request MIME type.
        contents: Uploaded file bytes.

    Returns:
        Generated media identifier after the transaction commits.
    """
    extension = validate_media(kind, facing, mime, contents)
    connection.execute("BEGIN IMMEDIATE")
    filename = None
    try:
        check_upload_limit(connection, visit_id, kind, facing, len(contents))
        media_id = secrets.token_hex(16)
        filename = media_id + extension
        with (directory / filename).open("xb") as media_file:
            media_file.write(contents)
        connection.execute(
            "INSERT INTO media(id,visit_id,created_at,kind,facing,mime,filename,size) VALUES(?,?,?,?,?,?,?,?)",
            (media_id, visit_id, now(), kind, facing, mime, filename, len(contents)),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        if filename:
            (directory / filename).unlink(missing_ok=True)
        raise
    return media_id
