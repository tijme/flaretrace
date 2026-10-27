"""Regress stale-marker permission failures and verify private, non-root container storage."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from flaretrace.runtime import storage


def test_stale_marker_never_bypasses_critical_file_ownership(tmp_path):
    """Detect the reported UID mismatch even when an apparently valid ownership marker exists.

    Args:
        tmp_path: Dedicated test directory whose files belong to the host test account.
    """
    marker = tmp_path / storage.OWNERSHIP_MARKER
    expected_uid = os.getuid() + 1
    marker.write_text(f"{expected_uid}:{os.getgid()}\n")
    secret = tmp_path / "session-key"
    secret.write_text("preserved signing key")
    assert storage.ownership_scan_required(
        tmp_path, [secret], expected_uid, os.getgid()
    )
    marker.write_text(f"{os.getuid()}:{os.getgid()}\n")
    assert not storage.ownership_scan_required(
        tmp_path, [secret], os.getuid(), os.getgid()
    )
    marker.write_text("old ownership policy")
    assert storage.ownership_scan_required(tmp_path, [secret], os.getuid(), os.getgid())
    marker.unlink()
    assert storage.ownership_scan_required(tmp_path, [secret], os.getuid(), os.getgid())


def test_root_bootstrap_repairs_without_replacing_secrets(tmp_path, monkeypatch):
    """Repair a restored tree once, retain evidence and credentials, and enforce private modes.

    Args:
        tmp_path: Temporary storage containing synthetic credentials and nested evidence.
        monkeypatch: Restores mocked root identity and ownership operations after the test.
    """
    directory = tmp_path / "data"
    directory.mkdir()
    secret = directory / "session-key"
    secret.write_text("original-secret")
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (directory / "untrusted-link").symlink_to(outside)
    monkeypatch.setattr(storage.os, "geteuid", lambda: 0)
    chown = Mock()
    monkeypatch.setattr(storage.os, "chown", chown)
    storage.prepare_storage(directory, 10001, 10001)
    assert secret.read_text() == "original-secret"
    assert secret.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / storage.OWNERSHIP_MARKER).read_text() == "10001:10001\n"
    assert all(
        call.args[0] not in {outside, directory / "untrusted-link"}
        for call in chown.call_args_list
    )
    assert all(
        call.kwargs == {"follow_symlinks": False} for call in chown.call_args_list
    )


def test_matching_bootstrap_skips_large_media_scan(tmp_path, monkeypatch):
    """Avoid recursive scans when the marker and every startup-critical path are correct.

    Args:
        tmp_path: Dedicated storage owned by the host test account.
        monkeypatch: Restores fake root identity and scan assertions after the test.
    """
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / storage.OWNERSHIP_MARKER).write_text(f"{os.getuid()}:{os.getgid()}\n")
    scan = Mock()
    monkeypatch.setattr(storage, "repair_tree", scan)
    monkeypatch.setattr(storage.os, "geteuid", lambda: 0)
    storage.prepare_storage(directory, os.getuid(), os.getgid())
    scan.assert_not_called()
    storage.prepare_storage(directory, os.getuid(), os.getgid(), force=True)
    scan.assert_called_once()


@pytest.mark.parametrize(
    "location", ["/", "/tmp", "/home", "/root", "/var", "/app", "relative-data"]
)
def test_storage_rejects_broad_locations(location):
    """Never recursively repair a filesystem root or an ambiguous relative directory.

    Args:
        location: Broad or relative path that cannot safely hold this installation's data.
    """
    with pytest.raises(ValueError):
        storage.validate_directory(Path(location))


@pytest.mark.parametrize(
    "name", ["session-key", "media", "tls", storage.OWNERSHIP_MARKER]
)
def test_critical_symlinks_are_rejected(tmp_path, name):
    """Reject redirected credential and directory paths before modifying their external targets.

    Args:
        tmp_path: Dedicated directory containing a synthetic link and external target.
        name: Startup-critical file or directory that must never be a symlink.
    """
    directory = tmp_path / "data"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        storage.prepare_storage(directory, 10001, 10001)


def test_symlinked_root_and_nonregular_secret_are_rejected(tmp_path):
    """Prevent a linked root or directory masquerading as a credential from entering repair.

    Args:
        tmp_path: Dedicated directory containing only synthetic path-type fixtures.
    """
    (tmp_path / "target").mkdir()
    (tmp_path / "linked").symlink_to(tmp_path / "target", target_is_directory=True)
    with pytest.raises(ValueError):
        storage.validate_directory(tmp_path / "linked")
    (tmp_path / "session-key").mkdir()
    with pytest.raises(ValueError):
        storage.critical_paths(tmp_path)


def test_nonroot_launch_requires_accessible_storage(tmp_path, monkeypatch):
    """Accept accessible non-root storage and fail clearly when permissions prevent access.

    Args:
        tmp_path: Dedicated writable storage for the non-root test launch.
        monkeypatch: Restores access checks and synthetic effective identity after use.
    """
    monkeypatch.setattr(storage.os, "geteuid", lambda: 10001)
    storage.prepare_storage(tmp_path, 10001, 10001)
    monkeypatch.setattr(storage.os, "access", lambda *arguments: False)
    with pytest.raises(PermissionError):
        storage.prepare_storage(tmp_path, 10001, 10001)


def test_privileges_are_dropped_before_services_start(monkeypatch):
    """Clear supplementary groups and permanently switch both GID and UID from root.

    Args:
        monkeypatch: Restores mocked privilege-changing OS calls after their order is verified.
    """
    calls = []
    monkeypatch.setattr(storage.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        storage.os, "setgroups", lambda groups: calls.append(("groups", groups))
    )
    monkeypatch.setattr(
        storage.os, "setgid", lambda group: calls.append(("gid", group))
    )
    monkeypatch.setattr(storage.os, "setuid", lambda user: calls.append(("uid", user)))
    storage.drop_privileges(10001, 10002)
    assert calls == [("groups", []), ("gid", 10002), ("uid", 10001)]
    monkeypatch.setattr(storage.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(storage.os, "getegid", lambda: 10002)
    storage.drop_privileges(10001, 10002)
    with pytest.raises(PermissionError):
        storage.drop_privileges(10003, 10002)
    with pytest.raises(PermissionError):
        storage.drop_privileges(10001, 10004)
