"""Retain the detailed synthetic capture/live/group regressions in normal CI."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.ui,
    pytest.mark.timeout(300),
    pytest.mark.skipif(
        os.environ.get("CI_BROWSER", "chromium") != "chromium",
        reason="Fake camera regression scenarios require Chromium",
    ),
]


@pytest.mark.parametrize("scenario", ["smoke", "admin_live", "groups"])
def test_synthetic_regression_scenario(
    scenario, live_server, application, ui_artifacts
):
    """Verify synthetic regression scenario.

    Args:
        scenario: Standalone synthetic browser regression script to execute.
        live_server: Local HTTP origin and database path for the temporary test installation.
        application: Flask application configured with isolated storage and credentials.
        ui_artifacts: Directory for this scenario’s traces, downloads, and failure screenshots.
    """
    root = Path(__file__).resolve().parents[2]
    environment = dict(
        os.environ,
        TRACE_TEST_URL=live_server,
        TRACE_TEST_PASSWORD="feature-test-password",
        TRACE_TEST_DATA_DIR=application.config["DATA_DIR"],
        TRACE_TEST_ARTIFACTS=str(ui_artifacts),
    )
    log_path = ui_artifacts / "scenario.log"
    # Stream to disk so even a timed-out browser process retains its diagnostics.
    with log_path.open("w") as log:
        result = subprocess.run(
            [sys.executable, str(root / "tests/browser" / (scenario + ".py"))],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=270,
        )
    assert result.returncode == 0, log_path.read_text()
