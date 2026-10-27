"""Exercise Python entrypoints without opening a development-server listener."""

import runpy
import sys
from unittest.mock import Mock

import flaretrace
from flaretrace import __main__


def test_development_entrypoint_uses_factory_and_loopback(
    monkeypatch, tmp_path, capsys
):
    """Verify development entrypoint uses factory and loopback.

    Args:
        monkeypatch: Pytest helper that restores replaced environment values or attributes.
        tmp_path: Pytest-provided temporary directory unique to this test.
        capsys: Pytest capture fixture used to inspect terminal output.
    """
    app = Mock(config={"DATA_DIR": str(tmp_path)})
    monkeypatch.setattr(__main__, "create_app", lambda: app)
    monkeypatch.setattr(sys, "argv", ["flaretrace"])
    __main__.main()
    app.run.assert_called_once_with(host="127.0.0.1", port=8000, debug=False)
    assert str(tmp_path / "admin-password") in capsys.readouterr().out


def test_wsgi_entrypoint_creates_an_application(monkeypatch):
    """Verify wsgi entrypoint creates an application.

    Args:
        monkeypatch: Pytest helper that restores replaced environment values or attributes.
    """
    app = Mock()
    monkeypatch.setattr(flaretrace, "create_app", lambda: app)
    assert runpy.run_module("flaretrace.wsgi")["app"] is app
