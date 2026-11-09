"""Verify that the installed application finds its resources outside the checkout."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstalledPackageTests(unittest.TestCase):
    """Exercise installed package tests with isolated application resources.

    Attributes:
        directory: TemporaryDirectory owner for this test installation; cleaned up after use.
        environment: Child-process environment with isolated test settings.
    """

    def setUp(self):
        """Create isolated storage and clients for each test case."""
        self.directory = tempfile.TemporaryDirectory()
        self.environment = dict(os.environ)
        for name in (
            "DATA_DIR",
            "DOMAIN",
            "AUTO_TLS",
            "SITE_ADDRESS",
            "PUBLIC_BASE_URL",
            "TRUST_PROXY",
        ):
            self.environment.pop(name, None)
        self.environment["ADMIN_PASSWORD"] = "package-installation-test-password"

    def tearDown(self):
        """Release temporary resources created by this test case."""
        self.directory.cleanup()

    def test_templates_static_assets_and_schema_work_from_another_directory(self):
        """Verify templates static assets and schema work from another directory."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
from pathlib import Path
from flaretrace import create_app

app = create_app({"TESTING": True})
assert Path(app.config["DATA_DIR"]) == Path.cwd() / "data"
assert (Path.cwd() / "data/trace.sqlite3").is_file()
client = app.test_client()
assert client.get("/healthz").status_code == 200
assert client.get("/admin/login").status_code == 200
for asset in ("app.css", "admin.js", "capture.js", "theme.js", "mark.svg",
              "fonts/dm-sans-variable.ttf", "fonts/OFL.txt", "manifest.json",
              "sw.js", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-180.png"):
    response = client.get("/static/" + asset)
    assert response.status_code == 200, asset
    assert response.data, asset
    response.close()
response = client.get("/sw.js")
assert response.status_code == 200
assert response.data, "/sw.js"
""",
            ],
            cwd=self.directory.name,
            env=self.environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_module_entrypoint_preserves_legacy_cleanup_behavior(self):
        """Verify module entrypoint preserves legacy cleanup behavior."""
        result = subprocess.run(
            [sys.executable, "-m", "flaretrace", "cleanup"],
            cwd=self.directory.name,
            env=self.environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Automatic cleanup is disabled", result.stdout)
        self.assertTrue((Path(self.directory.name) / "data/trace.sqlite3").is_file())
