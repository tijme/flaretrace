"""Check native documentation headers for maintained templates and deployment files."""

from pathlib import Path
import re

from tests.documentation.checks import DocumentationIssue

#: File types whose own comment syntax carries their file-level context.
COMMENT_MARKERS = {
    ".sh": "#",
    ".yaml": "#",
    ".yml": "#",
    ".toml": "#",
    ".sql": "--",
    ".css": "/*",
    ".html": "{#",
}


def native_files(root):
    """Discover editable configuration and template sources, excluding generated assets.

    Args:
        root: Repository root as a Path.

    Returns:
        Sorted maintained sources that support native comment headers.
    """
    candidates = [
        path
        for directory in ("src", "scripts", "deploy", ".github")
        for path in (root / directory).rglob("*")
        if path.is_file()
    ]
    candidates += [root / "compose.yaml", root / "pyproject.toml"]
    return sorted(
        path
        for path in candidates
        if path.suffix in COMMENT_MARKERS or path.name in {"Dockerfile", "Caddyfile"}
    )


def native_issues(source, path):
    """Require a native file header and argument/result contracts for shell functions.

    Args:
        source: Complete maintained configuration or template text.
        path: Path or label determining the source language.

    Returns:
        Source-located header or shell-contract issues; an empty list means complete.
    """
    path = Path(path)
    lines = source.splitlines()
    marker = COMMENT_MARKERS.get(path.suffix, "#")
    content = [
        line.strip()
        for line in lines[:6]
        if line.strip()
        and not line.startswith("#!")
        and not line.startswith("# syntax=")
    ]
    if path.suffix == ".html" and content and content[0].startswith("<!--"):
        marker = "<!--"
    issues = []
    if not content or not content[0].startswith(marker) or len(content[0].split()) < 4:
        issues.append(
            DocumentationIssue(
                str(path),
                1,
                "<module>",
                "Add a concrete file header using this language’s native comment syntax.",
            )
        )
    if path.suffix == ".sh":
        for index, line in enumerate(lines):
            match = re.match(r"^([A-Za-z_]\w*)\(\)\s*\{", line)
            if not match:
                continue
            preceding = []
            cursor = index - 1
            while cursor >= 0 and lines[cursor].startswith("#"):
                preceding.append(lines[cursor])
                cursor -= 1
            documentation = "\n".join(reversed(preceding))
            if not all(
                heading in documentation
                for heading in ("Arguments:", "Outputs:", "Returns:")
            ):
                issues.append(
                    DocumentationIssue(
                        str(path),
                        index + 1,
                        match[1],
                        "Document shell purpose, Arguments, Outputs, and Returns before this function.",
                    )
                )
    return issues


def audit_native(root):
    """Audit all maintained native-comment sources at a repository root.

    Args:
        root: Repository root directory as a Path.

    Returns:
        Complete list of native file-header and shell-contract failures.
    """
    return [
        issue
        for path in native_files(root)
        for issue in native_issues(path.read_text(), path.relative_to(root))
    ]
