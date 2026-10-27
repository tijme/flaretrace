"""Repair container storage without trusting stale ownership markers or replacing secrets."""

import os
from pathlib import Path
import stat

#: Marker for this ownership policy; an existing marker never bypasses critical-file checks.
OWNERSHIP_MARKER = ".ownership-v2"
#: Files required for startup or SQLite recovery, including legacy initialization markers.
CRITICAL_FILES = (
    "session-key",
    "admin-password",
    "trace.sqlite3",
    "trace.sqlite3-wal",
    "trace.sqlite3-shm",
    ".ownership-v1",
    OWNERSHIP_MARKER,
)
#: Private persistent directories owned by the non-root application and Caddy processes.
PRIVATE_DIRECTORIES = ("media", "tls")


def validate_directory(directory):
    """Reject broad filesystem roots and symlinked persistent-data locations before repair.

    Args:
        directory: Explicit persistent data directory as an absolute Path.

    Raises:
        ValueError: The directory is broad, relative, or reached through a symbolic link.
    """
    forbidden = {"/", "/home", "/root", "/tmp", "/var", "/usr", "/app", "/run", "/etc"}
    if (
        not directory.is_absolute()
        or str(directory) in forbidden
        or directory.resolve() != directory
    ):
        raise ValueError(
            "--data-dir must be a dedicated absolute directory, not a filesystem root or symlink"
        )


def critical_paths(directory):
    """List startup-critical paths without traversing the potentially large media tree.

    Args:
        directory: Validated persistent root directory.

    Returns:
        Root, private subdirectories, and existing credential/database/marker paths.

    Raises:
        ValueError: A critical path is a symlink or has an unexpected filesystem type.
    """
    paths = [directory, *(directory / name for name in PRIVATE_DIRECTORIES)]
    for name in CRITICAL_FILES:
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"critical storage file must be a regular file: {path}")
        if path.exists():
            paths.append(path)
    for path in paths:
        if path.is_symlink():
            raise ValueError(
                f"critical storage paths must not be symbolic links: {path}"
            )
    return paths


def correct_owner(path, user_id, group_id):
    """Change ownership only when needed, without dereferencing filesystem symlinks.

    Args:
        path: Regular file or directory beneath validated application storage.
        user_id: Numeric non-root serving UID.
        group_id: Numeric non-root serving GID.
    """
    metadata = path.lstat()
    if not stat.S_ISLNK(metadata.st_mode) and (metadata.st_uid, metadata.st_gid) != (
        user_id,
        group_id,
    ):
        os.chown(path, user_id, group_id, follow_symlinks=False)


def repair_tree(directory, user_id, group_id):
    """Repair a dedicated application tree without following links outside that tree.

    Args:
        directory: Validated, dedicated persistent-data directory.
        user_id: Numeric non-root serving UID.
        group_id: Numeric non-root serving GID.
    """
    for current, directories, filenames in os.walk(directory, followlinks=False):
        for name in (".", *directories, *filenames):
            correct_owner(Path(current) / name, user_id, group_id)


def ownership_scan_required(directory, paths, user_id, group_id):
    """Detect a missing, stale, or misleading marker using real critical-file ownership.

    Args:
        directory: Validated persistent-data directory.
        paths: Startup-critical regular files and directories.
        user_id: Expected serving UID.
        group_id: Expected serving GID.

    Returns:
        True when a complete ownership repair is needed, even if a marker exists.
    """
    marker = directory / OWNERSHIP_MARKER
    if not marker.is_file() or marker.read_text() != f"{user_id}:{group_id}\n":
        return True
    return any(
        (path.stat().st_uid, path.stat().st_gid) != (user_id, group_id)
        for path in paths
    )


def prepare_storage(directory, user_id, group_id, force=False):
    """Initialize private storage and recover ownership after restores or previous UID changes.

    Args:
        directory: Dedicated persistent-data directory; contents and secrets are preserved.
        user_id: Non-root account that will run Flask and Caddy.
        group_id: Non-root group that will run Flask and Caddy.
        force: Whether to request a complete scan even when critical paths are correct.

    Raises:
        ValueError: Storage points at a broad location or critical symlink.
        PermissionError: A non-root launch cannot access existing protected storage.
    """
    validate_directory(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in PRIVATE_DIRECTORIES:
        path = directory / name
        if path.is_symlink():
            raise ValueError(
                f"private storage directory must not be a symbolic link: {path}"
            )
        path.mkdir(exist_ok=True, mode=0o700)
    paths = critical_paths(directory)
    if os.geteuid() == 0:
        if force or ownership_scan_required(directory, paths, user_id, group_id):
            repair_tree(directory, user_id, group_id)
        for path in paths:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        marker = directory / OWNERSHIP_MARKER
        marker.write_text(f"{user_id}:{group_id}\n")
        correct_owner(marker, user_id, group_id)
        os.chmod(marker, 0o600)
    elif any(not os.access(path, os.R_OK | os.W_OK) for path in paths):
        raise PermissionError(
            "storage is inaccessible; start with the image's bootstrap user or repair ownership first"
        )


def drop_privileges(user_id, group_id):
    """Permanently discard root privileges before either network service starts.

    Args:
        user_id: Non-root serving UID selected by the container arguments.
        group_id: Non-root serving GID selected by the container arguments.

    Raises:
        PermissionError: A non-root launch does not match its explicit UID/GID settings.
    """
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(group_id)
        os.setuid(user_id)
    elif os.geteuid() != user_id or os.getegid() != group_id:
        raise PermissionError(
            "non-root launches must set --uid and --gid to their actual account"
        )
