"""Prevent missing Python docstrings, incomplete contracts, and oversized functions."""

from pathlib import Path

import pytest

from tests.documentation.checks import python_issues, audit_repository
from tests.documentation.native import audit_native, native_issues


def test_repository_python_documentation_is_complete():
    """Require complete documentation for maintained Python application and test code."""
    issues = audit_repository(Path(__file__).resolve().parents[2])
    assert not issues, "\n".join(
        f"{issue.path}:{issue.line}: {issue.symbol}: {issue.reason}" for issue in issues
    )


def test_repository_native_documentation_is_complete():
    """Require native headers on templates, shell scripts, SQL, and deployment configuration."""
    issues = audit_native(Path(__file__).resolve().parents[2])
    assert not issues, "\n".join(
        f"{issue.path}:{issue.line}: {issue.reason}" for issue in issues
    )


@pytest.mark.parametrize(
    "header",
    [
        "<!-- Editable visitor page and privacy copy. -->",
        "{# Administrator template and form context. #}",
    ],
)
def test_html_headers_accept_standalone_and_jinja_comments(header):
    """Require proper documentation without placing Jinja syntax inside standalone HTML.

    Args:
        header: Native HTML or Jinja comment documenting the maintained template.
    """
    assert native_issues(header + "\n<p>Example</p>", "page.html") == []


@pytest.mark.parametrize(
    "source, expected",
    [
        ("def missing():\n    pass\n", "docstring"),
        (
            '"""Describe this example module."""\ndef use(value):\n    """Validate one input value."""\n    pass\n',
            "argument",
        ),
        (
            '"""Describe this example module."""\ndef result():\n    """Compute a sample result."""\n    return 1\n',
            "returned",
        ),
        (
            '"""Describe this example module."""\nclass State:\n    """Hold sample reader state."""\n    value: int\n',
            "attribute",
        ),
        ('"""TODO explain this later."""\n', "docstring"),
        ('"""Describe this example module."""\nLIMIT = 40\n', "constant"),
        (
            '"""Describe this example module."""\ndef fail():\n    """Reject an invalid option."""\n    raise ValueError("Invalid option")\n',
            "exception",
        ),
        (
            '"""Describe this example module."""\ndef items():\n    """Read all stored records."""\n    yield 1\n',
            "yielded",
        ),
        (
            '"""Describe this example module."""\ndef label():\n    """Assign a sample label."""\n    x = 1\n',
            "single-letter",
        ),
        (
            '"""Describe this example module."""\ndef label():\n    """Set a sample label.\n\n    Args:\n        old_name: Removed argument.\n    """\n    pass\n',
            "nonexistent",
        ),
        (
            '"""Describe this example module."""\ndef oversized():\n    """Perform too many operations."""\n'
            + "    work()\n" * 41,
            "40 executable",
        ),
    ],
)
def test_python_documentation_rejects_incomplete_contracts(source, expected):
    """Reject missing module, callable, parameter, result, attribute, and placeholder docs.

    Args:
        source: Deliberately incomplete Python fixture source.
        expected: Required diagnostic word proving the intended check failed.
    """
    issues = python_issues(source)
    assert any(expected in issue.reason for issue in issues)


def test_python_documentation_accepts_complete_callable():
    """Accept a concrete module summary and a fully described pure helper."""
    source = '''"""Normalize labels supplied by an administrator."""
def label(value):
    """Trim a supplied label.

    Args:
        value: Original label text.

    Returns:
        Label with surrounding whitespace removed.
    """
    return value.strip()
'''
    assert python_issues(source) == []


@pytest.mark.parametrize(
    "filename, source",
    [
        ("deploy.yaml", "services: {}"),
        ("page.html", "<p>Missing header</p>"),
        ("schema.sql", "CREATE TABLE missing(id TEXT);"),
        ("style.css", "body {color: red}"),
        ("tool.sh", "#!/bin/sh\n# Run a useful tool.\nrun() { :; }\n"),
    ],
)
def test_native_documentation_rejects_missing_contracts(filename, source):
    """Reject missing language-native headers and incomplete shell-function contracts.

    Args:
        filename: Synthetic source path selecting the native comment syntax.
        source: Deliberately incomplete source text used to test the checker.
    """
    assert native_issues(source, filename)


def test_native_documentation_accepts_a_complete_shell_function():
    """Accept a shebang, concrete file header, and explicit shell input/output contract."""
    source = """#!/bin/sh
# Run isolated database readiness checks.
# Check the current installation database.
# Arguments: None.
# Outputs: Readiness information on stdout.
# Returns: 0 when ready, 1 on failure.
ready() { :; }
"""
    assert native_issues(source, "ready.sh") == []


def test_nested_result_does_not_require_outer_returns_section():
    """Attribute a nested callable's return contract only to that callable."""
    source = '''"""Register a nested constant-value helper."""
def register():
    """Define a helper without returning it."""
    def helper():
        """Return the registered constant.

        Returns:
            Integer used by this sample.
        """
        return 1
'''
    assert python_issues(source) == []
