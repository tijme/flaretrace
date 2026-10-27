"""Report complete Python and native-comment documentation coverage for CI."""

import ast
from dataclasses import asdict
import json
from pathlib import Path

from tests.documentation.checks import audit_repository, python_files
from tests.documentation.native import audit_native, native_files


def main():
    """Write documentation diagnostics and fail when any maintained contract is missing.

    Raises:
        SystemExit: At least one source has an incomplete documentation contract.
    """
    root = Path(__file__).resolve().parents[2]
    paths = python_files(root)
    symbols = sum(
        sum(
            isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            for node in ast.walk(ast.parse(path.read_text()))
        )
        for path in paths
    )
    python_issues = audit_repository(root)
    native_issues = audit_native(root)
    issues = python_issues + native_issues
    output = root / "artifacts/ci/documentation"
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "python_files": len(paths),
        "python_symbols": symbols,
        "native_files": len(native_files(root)),
        "complete": not issues,
        "issues": [asdict(issue) for issue in issues],
    }
    (output / "python-native.json").write_text(json.dumps(report, indent=2) + "\n")
    javascript = json.loads((output / "javascript.json").read_text())
    complete = not issues and javascript["complete"]
    summary = (
        "# Documentation coverage\n\n"
        "| Source | Files | Documented symbols | Required contracts |\n"
        "| --- | ---: | ---: | --- |\n"
        f"| Python | {len(paths)} | {symbols} | {'100%' if not python_issues else 'Failed'} |\n"
        f"| JavaScript | {javascript['files']} | {javascript['symbols']} | {'100%' if javascript['complete'] else 'Failed'} |\n"
        f"| Native templates/configuration | {len(native_files(root))} | File and shell-function headers | {'100%' if not native_issues else 'Failed'} |\n\n"
        "Arguments, results, explicit Python exceptions, class attributes, and Python constants are checked. "
        "Named functions are limited to 40 executable statements; nested callables are measured separately. "
        "This is documentation-contract coverage, not a measure of prose quality or behavioral test coverage.\n"
    )
    (output / "summary.md").write_text(summary)
    print(summary)
    for issue in issues:
        print(f"{issue.path}:{issue.line}: {issue.symbol}: {issue.reason}")
    if not complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
