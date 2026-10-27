"""Shared local-container and GitHub Actions test runner."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

#: Repository root resolved independently of the caller’s working directory.
ROOT = Path(__file__).resolve().parents[2]
#: Shared CI report directory mounted by Docker or Podman.
OUTPUT = ROOT / "artifacts/ci"


def command(*arguments, environment=None):
    """Propagate failures from a test tool without changing its exit status.

    Args:
        environment: Environment overrides passed to the child process; None inherits the parent.
        arguments: Executable and argument values passed to the child process.
    """
    print("+", " ".join(map(str, arguments)), flush=True)
    subprocess.run(list(map(str, arguments)), cwd=ROOT, env=environment, check=True)


def quality():
    """Check source syntax without requiring writable source directories."""
    for directory in ("src", "tests"):
        for path in (ROOT / directory).rglob("*.py"):
            ast.parse(path.read_text(), filename=str(path))
    for path in (ROOT / "src/flaretrace/static").glob("*.js"):
        command("node", "--check", path)


def python_tests(category):
    """Keep unit/feature measurements separate, including child processes.

    Args:
        category: Independent Python suite to run: unit or feature.
    """
    stage = OUTPUT / category
    stage.mkdir(parents=True, exist_ok=True)
    for previous in stage.glob(".coverage*"):
        if previous.is_file():
            previous.unlink()
    environment = dict(
        os.environ,
        COVERAGE_FILE=str(stage / ".coverage"),
        COVERAGE_RCFILE=str(ROOT / "pyproject.toml"),
        PYTHONWARNINGS="ignore:unclosed database:ResourceWarning",
    )
    directory = "unit" if category == "unit" else "integration"
    command(
        sys.executable,
        "-m",
        "pytest",
        ROOT / "tests" / directory,
        "-q",
        "-o",
        "cache_dir=" + str(stage / "pytest-cache"),
        "--junitxml=" + str(stage / "junit.xml"),
        "--cov=flaretrace",
        "--cov-branch",
        "--cov-fail-under=0",
        "--cov-report=term-missing:skip-covered",
        "--cov-report=json:" + str(stage / "coverage.json"),
        environment=environment,
    )


def coverage_report():
    """Require 90% independently for statements, branches, and combined coverage.

    Raises:
        SystemExit: The supplied input or runtime state violates this operation’s contract.
    """
    from coverage import Coverage

    files = [OUTPUT / stage / ".coverage" for stage in ("unit", "feature")]
    if not all(path.is_file() for path in files):
        raise SystemExit("Run both unit and feature suites before combining coverage.")
    output = OUTPUT / "coverage"
    output.mkdir(parents=True, exist_ok=True)
    measured = Coverage(
        data_file=str(output / ".coverage"), config_file=str(ROOT / "pyproject.toml")
    )
    # A previous combined report must never contribute to a fresh coverage gate.
    measured.erase()
    measured.combine([str(path) for path in files], strict=True, keep=True)
    measured.save()
    combined = measured.report()
    measured.xml_report(outfile=str(output / "coverage.xml"))
    measured.html_report(directory=str(output / "html"))
    measured.json_report(outfile=str(output / "coverage.json"))
    totals = json.loads((output / "coverage.json").read_text())["totals"]
    lines = 100 * totals["covered_lines"] / totals["num_statements"]
    branches = 100 * totals["covered_branches"] / max(1, totals["num_branches"])
    summary = (
        "# Python coverage\n\n| Metric | Result | Required |\n| --- | ---: | ---: |\n"
        f"| Statements | {lines:.2f}% | 90% |\n| Branches | {branches:.2f}% | 90% |\n"
        f"| Combined | {combined:.2f}% | 90% |\n"
    )
    (output / "summary.md").write_text(summary)
    print(summary, flush=True)
    if min(lines, branches, combined) < 90:
        raise SystemExit(
            "Coverage gate failed: statements and branches must each reach 90%."
        )


def javascript():
    """Javascript for the isolated ci checks."""
    (OUTPUT / "javascript").mkdir(parents=True, exist_ok=True)
    command("npm", "test")


def documentation():
    """Require complete source documentation and test the documentation checkers themselves."""
    stage = OUTPUT / "documentation"
    stage.mkdir(parents=True, exist_ok=True)
    command(
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_documentation.py",
        "-q",
        "-o",
        "cache_dir=" + str(stage / "pytest-cache"),
        "--junitxml=" + str(stage / "python-junit.xml"),
    )
    command(
        "node",
        "--test",
        "--test-isolation=none",
        "tests/documentation/javascript.test.mjs",
    )
    command("node", "tests/documentation/run.mjs")
    command(sys.executable, "-m", "tests.documentation")


def ui(browser):
    """Ui for the isolated ci checks.

    Args:
        browser: Playwright browser instance controlled by this scenario.
    """
    stage = OUTPUT / "ui" / browser
    stage.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, CI_BROWSER=browser, CI_UI_ARTIFACTS=str(stage))
    command(
        sys.executable,
        "-m",
        "pytest",
        ROOT / "tests/ui",
        "-q",
        "-o",
        "cache_dir=" + str(stage / "pytest-cache"),
        "--junitxml=" + str(stage / "junit.xml"),
        environment=environment,
    )


def main():
    """Run the run checks and propagate any failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=(
            "all",
            "quality",
            "unit",
            "feature",
            "javascript",
            "ui",
            "coverage",
            "documentation",
            "image",
            "image-seed",
        ),
    )
    parser.add_argument(
        "--browser", choices=("all", "chromium", "firefox", "webkit"), default="all"
    )
    options = parser.parse_args()
    os.chdir(ROOT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if options.stage == "image-seed":
        command(sys.executable, "-m", "tests.ci.container_fixtures")
        return
    if options.stage == "image":
        stage = OUTPUT / "image"
        stage.mkdir(parents=True, exist_ok=True)
        command(
            sys.executable,
            "-m",
            "pytest",
            "tests/image",
            "-q",
            "-o",
            "cache_dir=" + str(stage / "pytest-cache"),
            "--junitxml=" + str(stage / "junit.xml"),
        )
        return
    if options.stage in {"all", "quality", "unit"}:
        quality()
    if options.stage in {"all", "documentation"}:
        documentation()
    if options.stage in {"all", "unit"}:
        python_tests("unit")
    if options.stage in {"all", "feature"}:
        python_tests("feature")
    if options.stage in {"all", "coverage"}:
        coverage_report()
    if options.stage in {"all", "javascript"}:
        javascript()
    if options.stage in {"all", "ui"}:
        for browser in (
            ["chromium", "firefox", "webkit"]
            if options.browser == "all"
            else [options.browser]
        ):
            ui(browser)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
