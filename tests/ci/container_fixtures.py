"""Seed disposable image-test volumes with the precise stale-ownership failure seen in production."""

import os
from pathlib import Path

#: Deterministic synthetic signing key used to prove bootstrap never regenerates existing secrets.
SIGNING_KEY = "synthetic-preserved-session-key-0123456789abcdef0123456789abcdef"
#: Deterministic generated credential used only by isolated production-image regression tests.
ADMIN_PASSWORD = "synthetic-preserved-admin-password"


def seed_storage(directory):
    """Create only a new disposable fixture tree, including an intentionally misleading marker.

    Args:
        directory: Empty image-test volume mount; never pass live installation storage.

    Raises:
        ValueError: The fixture directory is nonempty and might contain unrelated data.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError(
            "image-test seed volume must be empty; use a fresh CI Compose project"
        )
    (directory / "media").mkdir()
    for name, contents in (
        ("session-key", SIGNING_KEY),
        ("admin-password", ADMIN_PASSWORD),
        (".ownership-v1", "10001:10001"),
        (".ownership-v2", "10001:10001"),
        ("media/synthetic.txt", "preserved evidence"),
    ):
        path = directory / name
        path.write_text(contents + "\n")
        os.chmod(path, 0o600)
        os.chown(path, 1000, 1000)
    os.chown(directory, 10001, 10001)
    os.chmod(directory, 0o700)


if __name__ == "__main__":
    seed_storage(Path("/seed"))
