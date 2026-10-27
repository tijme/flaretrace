"""Test entrypoint dispatch, readiness probes, private configuration, and credential handling."""

import json
import runpy
from unittest.mock import Mock

import pytest

from flaretrace.runtime import cli
from flaretrace.runtime.options import parse_settings


def test_child_environment_uses_only_the_bundled_proxy():
    """Trust the loopback Caddy hop and pass the effective origin rather than legacy host guesses."""
    settings = parse_settings(["--domain", "trace.example.com", "--auto-tls"], {})
    environment = cli.application_environment(settings)
    assert environment["PUBLIC_BASE_URL"] == "https://trace.example.com"
    assert environment["TRUST_PROXY"] == "1" and environment["AUTO_TLS"] == "0"
    assert not environment["DOMAIN"] and not environment["SITE_ADDRESS"]
    assert (
        environment["DATA_DIR"] == "/data" and environment["ADMIN_USERNAME"] == "admin"
    )


def test_runtime_configuration_is_written_after_storage_preparation(
    tmp_path, monkeypatch
):
    """Write no plaintext credentials into the health manifest or generated Caddyfile.

    Args:
        tmp_path: Temporary directory for private runtime metadata and persistent storage.
        monkeypatch: Restores mocked bootstrap operations, privilege changes, and file paths.
    """
    settings = parse_settings(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--admin-password",
            "test-explicit-password",
        ],
        {},
    )
    manifest = tmp_path / "run/health.json"
    prepare = Mock()
    drop = Mock()
    owner = Mock()
    monkeypatch.setattr(cli, "HEALTH_FILE", manifest)
    monkeypatch.setattr(cli, "prepare_storage", prepare)
    monkeypatch.setattr(cli, "drop_privileges", drop)
    monkeypatch.setattr(cli, "correct_owner", owner)
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cli.os, "umask", Mock())
    configuration = cli.prepare_runtime(settings)
    prepare.assert_called_once_with(settings.data_directory, 10001, 10001, False)
    drop.assert_called_once_with(10001, 10001)
    owner.assert_called_once()
    assert (
        "test-explicit-password" not in manifest.read_text() + configuration.read_text()
    )
    assert json.loads(manifest.read_text())["generated_password"] is False
    assert json.loads(manifest.read_text())["admin_username"] == "admin"
    monkeypatch.setattr(cli.os, "geteuid", lambda: 10001)
    cli.prepare_runtime(settings)
    assert owner.call_count == 1


def test_serve_uses_loopback_gunicorn_and_the_bundled_caddy(monkeypatch):
    """Start the supervised proxy/backend pair without a Docker socket or host configuration.

    Args:
        monkeypatch: Restores mocked binary discovery, bootstrap, and supervisor entrypoints.
    """
    settings = parse_settings([], {})
    supervisor = Mock(return_value=23)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/local/bin/caddy")
    monkeypatch.setattr(
        cli, "prepare_runtime", lambda settings: "/run/flaretrace/Caddyfile"
    )
    monkeypatch.setattr(cli, "supervise", supervisor)
    assert cli.serve(settings) == 23
    commands, environment = supervisor.call_args.args
    assert "127.0.0.1:8000" in commands[0]
    assert commands[1] == [
        "/usr/local/bin/caddy",
        "run",
        "--config",
        "/run/flaretrace/Caddyfile",
        "--adapter",
        "caddyfile",
    ]
    assert environment["TRUST_PROXY"] == "1"
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        cli.serve(settings)


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (200, {"status": "ok"}, 0),
        (503, {"status": "ok"}, 1),
        (200, {"status": "failed"}, 1),
    ],
)
def test_readiness_probes_both_services(tmp_path, monkeypatch, status, body, expected):
    """Require real healthy responses from both the loopback backend and the public proxy.

    Args:
        tmp_path: Temporary directory containing the private health manifest.
        monkeypatch: Restores HTTP probe mocks and runtime metadata paths.
        status: HTTP status supplied by the synthetic readiness endpoint.
        body: JSON response supplied by the synthetic readiness endpoint.
        expected: Required healthcheck process exit status.
    """
    manifest = tmp_path / "health.json"
    manifest.write_text(json.dumps({"backend_port": 8001, "http_port": 8080}))
    response = Mock(status=status)
    response.read.return_value = json.dumps(body).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(cli, "HEALTH_FILE", manifest)
    monkeypatch.setattr(cli.urllib.request, "build_opener", lambda *handlers: opener)
    assert cli.healthcheck() == expected
    if expected == 0:
        assert opener.open.call_count == 2
        assert opener.open.call_args_list[0].args[0] == "http://127.0.0.1:8001/healthz"
        assert opener.open.call_args_list[1].args[0] == "http://127.0.0.1:8080/healthz"


def test_readiness_fails_closed_for_missing_corrupt_or_unavailable_state(
    tmp_path, monkeypatch
):
    """Treat absent metadata, malformed JSON, missing ports, and network errors as unhealthy.

    Args:
        tmp_path: Temporary directory used for malformed runtime metadata fixtures.
        monkeypatch: Restores HTTP error mocks and the manifest path after use.
    """
    manifest = tmp_path / "health.json"
    monkeypatch.setattr(cli, "HEALTH_FILE", manifest)
    assert cli.healthcheck() == 1
    for contents in ("invalid", "{}", "[]"):
        manifest.write_text(contents)
        assert cli.healthcheck() == 1
    manifest.write_text('{"backend_port":8000,"http_port":80}')
    opener = Mock()
    opener.open.side_effect = OSError("unavailable")
    monkeypatch.setattr(cli.urllib.request, "build_opener", lambda *handlers: opener)
    assert cli.healthcheck() == 1


def test_credentials_command_preserves_credentials_and_obeys_runtime_state(
    tmp_path, monkeypatch, capsys
):
    """Display the active login and reject stale passwords when explicit credentials apply.

    Args:
        tmp_path: Temporary storage containing a synthetic generated password and health manifest.
        monkeypatch: Restores runtime metadata paths after the test.
        capsys: Pytest terminal capture used to verify explicit credential-display output.
    """
    manifest = tmp_path / "health.json"
    monkeypatch.setattr(cli, "HEALTH_FILE", manifest)
    (tmp_path / "admin-password").write_text("synthetic-generated-password\n")
    settings = parse_settings(["credentials", "--data-dir", str(tmp_path)], {})
    assert cli.credentials(settings) == 0
    assert capsys.readouterr().out == (
        "Username: admin\nPassword: synthetic-generated-password\n"
    )
    manifest.write_text(
        json.dumps({"data_directory": str(tmp_path), "generated_password": True})
    )
    assert cli.credentials(parse_settings(["credentials"], {})) == 0
    assert capsys.readouterr().out == (
        "Username: admin\nPassword: synthetic-generated-password\n"
    )
    manifest.write_text(
        json.dumps(
            {
                "data_directory": str(tmp_path),
                "generated_password": True,
                "admin_username": "researcher",
            }
        )
    )
    assert cli.credentials(parse_settings(["credentials"], {})) == 0
    assert capsys.readouterr().out == (
        "Username: researcher\nPassword: synthetic-generated-password\n"
    )
    assert (tmp_path / "admin-password").read_text() == "synthetic-generated-password\n"
    manifest.write_text(json.dumps({"generated_password": False}))
    with pytest.raises(RuntimeError):
        cli.credentials(settings)
    manifest.unlink()
    with pytest.raises(RuntimeError):
        cli.credentials(
            parse_settings(["credentials", "--admin-password", "explicit-password"], {})
        )


@pytest.mark.parametrize(
    "command, handler",
    [("serve", "serve"), ("credentials", "credentials"), ("healthcheck", "healthcheck")],
)
def test_main_dispatches_container_operations(monkeypatch, command, handler):
    """Return the chosen operation's exit status without replacing the user's arguments.

    Args:
        monkeypatch: Restores the simulated operation handler after use.
        command: Explicit container command passed to main.
        handler: Module handler expected to implement that operation.
    """
    monkeypatch.setattr(cli, handler, Mock(return_value=23))
    assert cli.main([command]) == 23


def test_main_reports_startup_errors_and_module_exit_status(monkeypatch, capsys):
    """Report a clear failure and let the module entrypoint propagate the container status.

    Args:
        monkeypatch: Restores the simulated failed serving operation and module handler.
        capsys: Pytest capture fixture used to inspect the safe startup diagnostic.
    """
    monkeypatch.setattr(
        cli, "serve", Mock(side_effect=RuntimeError("storage inaccessible"))
    )
    assert cli.main([]) == 1
    assert "storage inaccessible" in capsys.readouterr().err
    monkeypatch.setattr(cli, "main", lambda: 23)
    with pytest.raises(SystemExit) as failure:
        runpy.run_module("flaretrace.runtime", run_name="__main__")
    assert failure.value.code == 23
