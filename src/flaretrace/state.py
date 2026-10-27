"""Create private storage and authentication state for one Flask application."""

from dataclasses import dataclass
from pathlib import Path
import hmac
import os
import re
import secrets

from flask import current_app
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash


@dataclass(frozen=True)
class Authentication:
    """Credential verifiers and visitor-token signer; no plaintext password is retained.

    Attributes:
        username: Administrator login name validated during startup.
        password_hash: Werkzeug salted password verifier.
        credential_version: Session fingerprint invalidated when credentials change.
        signer: Time-limited visitor-token encoder bound to this installation.
    """

    username: str
    password_hash: str
    credential_version: str
    signer: URLSafeTimedSerializer


def private_value(directory, filename, factory):
    """Read a persisted secret or atomically create it with owner-only permissions.

    Args:
        directory: Installation storage directory, already created by startup.
        filename: Secret file name relative to that directory.
        factory: Zero-argument callable that generates a new secret string.

    Returns:
        Existing or newly generated secret without its trailing newline.
    """
    secret_path = directory / filename
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return secret_path.read_text().strip()
    secret = factory()
    with os.fdopen(descriptor, "w") as secret_file:
        secret_file.write(secret + "\n")
    return secret


def initialize_storage(application):
    """Create owner-only record and media directories for an application.

    Args:
        application: Flask application containing the DATA_DIR configuration.
    """
    storage_directory = Path(application.config["DATA_DIR"])
    storage_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    (storage_directory / "media").mkdir(exist_ok=True, mode=0o700)


def initialize_authentication(application):
    """Validate credentials and install only their verifiers in application state.

    Args:
        application: Flask application with storage and administrator configuration.

    Raises:
        ValueError: The username is invalid or the password is shorter than 12 characters.
    """
    storage_directory = Path(application.config["DATA_DIR"])
    application.secret_key = private_value(
        storage_directory, "session-key", lambda: secrets.token_hex(32)
    )
    username = application.config["ADMIN_USERNAME"]
    if not isinstance(username, str) or not re.fullmatch(
        r"[A-Za-z0-9_.@-]{1,80}", username
    ):
        raise ValueError(
            "ADMIN_USERNAME must contain 1–80 letters, digits, or ._@- characters"
        )
    password = application.config["ADMIN_PASSWORD"] or private_value(
        storage_directory, "admin-password", lambda: secrets.token_urlsafe(24)
    )
    if len(password) < 12:
        raise ValueError("ADMIN_PASSWORD must contain at least 12 characters")
    fingerprint = hmac.new(
        application.secret_key.encode(), (username + "\0" + password).encode(), "sha256"
    ).hexdigest()
    application.extensions["authentication"] = Authentication(
        username,
        generate_password_hash(password),
        fingerprint,
        URLSafeTimedSerializer(application.secret_key, salt="visitor-v1"),
    )


def authentication():
    """Return the active application's immutable credential and token-verification state.

    Returns:
        Immutable credential verifiers and visitor-token signer for the active app.
    """
    return current_app.extensions["authentication"]


def media_directory():
    """Return the active application's private upload directory as a Path.

    Returns:
        Path to the active installation’s private media directory.
    """
    return Path(current_app.config["DATA_DIR"]) / "media"
