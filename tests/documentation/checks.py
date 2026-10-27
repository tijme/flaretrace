"""Audit Python documentation contracts without importing or executing application code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import re

#: Largest permitted executable statement count, excluding nested callable bodies.
MAX_FUNCTION_STATEMENTS = 40
#: Locally maintained Python trees; dependencies, build outputs, and assets are excluded.
PYTHON_DIRECTORIES = ("src", "tests", "deploy")


@dataclass(frozen=True)
class DocumentationIssue:
    """One source-level documentation or readability failure.

    Attributes:
        path: Repository-relative source file or synthetic test-fixture name.
        line: One-based source line containing the undocumented symbol.
        symbol: Module, class, function, argument, or attribute being checked.
        reason: Concrete action required to satisfy the documented contract.
    """

    path: str
    line: int
    symbol: str
    reason: str


def section(documentation, heading):
    """Read one Google-style docstring section without confusing later sections.

    Args:
        documentation: Cleaned Python docstring text.
        heading: Section name such as Args, Returns, Yields, or Attributes.

    Returns:
        Section body, or an empty string when the heading is absent.
    """
    lines = documentation.splitlines()
    marker = heading + ":"
    if marker not in lines:
        return ""
    start = lines.index(marker) + 1
    following = lines[start:]
    stop = next(
        (
            index
            for index, line in enumerate(following)
            if re.match(r"^[A-Za-z][A-Za-z ]*:$", line)
        ),
        len(following),
    )
    return "\n".join(following[:stop]).strip()


def described_fields(documentation, heading):
    """Extract named fields that have a nonempty human-readable explanation.

    Args:
        documentation: Cleaned Python docstring text.
        heading: Args or Attributes section name.

    Returns:
        Field names mapped to their explanatory text; leading stars are removed.
    """
    body = section(documentation, heading)
    pattern = r"^\s*(\*{0,2}[A-Za-z_]\w*)(?:\s*\([^)]*\))?:\s*(\S.*)$"
    return {
        match[1].lstrip("*"): match[2]
        for match in re.finditer(pattern, body, re.MULTILINE)
    }


def own_nodes(function):
    """Walk a callable body without attributing nested functions' behavior to it.

    Args:
        function: Python function or async-function syntax node.

    Yields:
        Syntax nodes owned by this callable, excluding its documentation string.
    """
    pending = list(function.body)
    if (
        pending
        and isinstance(pending[0], ast.Expr)
        and isinstance(pending[0].value, ast.Constant)
        and isinstance(pending[0].value.value, str)
    ):
        pending.pop(0)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            pending.extend(ast.iter_child_nodes(node))


def argument_names(function):
    """List explicit callable arguments while omitting implicit instance/class receivers.

    Args:
        function: Python function or async-function syntax node.

    Returns:
        Positional, keyword-only, variadic, and keyword-variadic argument names.
    """
    arguments = [
        argument.arg
        for argument in function.args.posonlyargs
        + function.args.args
        + function.args.kwonlyargs
    ]
    arguments += [
        argument.arg
        for argument in (function.args.vararg, function.args.kwarg)
        if argument
    ]
    return [argument for argument in arguments if argument not in {"self", "cls"}]


def class_attributes(class_node):
    """Collect declared dataclass fields and instance attributes assigned by methods.

    Args:
        class_node: Python class syntax node.

    Returns:
        Attribute names belonging to the class, including test-fixture state.
    """
    attributes = {
        node.target.id
        for node in class_node.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for node in ast.walk(class_node):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            attributes.add(node.attr)
    return attributes


def documentation_summary(documentation):
    """Reject absent docstrings and placeholder summaries while accepting concise prose.

    Args:
        documentation: Parsed docstring, or None when documentation is missing.

    Returns:
        True when the first line contains a concrete, non-placeholder sentence.
    """
    if not documentation:
        return False
    first_line = documentation.strip().splitlines()[0]
    return len(first_line.split()) >= 3 and not re.search(
        r"\b(TODO|FIXME|TBD)\b", first_line
    )


def function_issues(node, documentation):
    """Check callable parameter, result, exception, and size contracts.

    Args:
        node: Function syntax node, including private helpers and test methods.
        documentation: Cleaned function docstring, or an empty string.

    Yields:
        Symbol/reason pairs describing missing contracts or readability violations.
    """
    fields = described_fields(documentation, "Args")
    for argument in argument_names(node):
        if argument not in fields:
            yield argument, "Document this argument in an Args section."
    for unexpected in set(fields) - set(argument_names(node)):
        yield unexpected, "Remove or correct documentation for a nonexistent argument."
    nodes = list(own_nodes(node))
    yields_values = any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in nodes)
    returns_value = any(
        isinstance(item, ast.Return)
        and item.value is not None
        and not (isinstance(item.value, ast.Constant) and item.value.value is None)
        for item in nodes
    )
    if yields_values and not section(documentation, "Yields"):
        yield node.name, "Explain yielded values in a Yields section."
    elif returns_value and not section(documentation, "Returns"):
        yield node.name, "Explain the returned value in a Returns section."
    exceptions = {
        item.exc.func.id
        for item in nodes
        if isinstance(item, ast.Raise)
        and isinstance(item.exc, ast.Call)
        and isinstance(item.exc.func, ast.Name)
    }
    for exception in sorted(
        exceptions - set(described_fields(documentation, "Raises"))
    ):
        yield exception, "Explain this explicitly raised exception in a Raises section."
    if sum(isinstance(item, ast.stmt) for item in nodes) > MAX_FUNCTION_STATEMENTS:
        yield (
            node.name,
            f"Extract focused helpers; this function exceeds {MAX_FUNCTION_STATEMENTS} executable statements.",
        )


def python_issues(source, path="<source>"):
    """Audit one Python module without executing imports, fixtures, or startup hooks.

    Args:
        source: Complete Python source text.
        path: File label included in failure reports.

    Returns:
        List of documentation/readability issues with exact source locations.
    """
    module = ast.parse(source, filename=str(path))
    issues = []
    lines = source.splitlines()
    for node in ast.walk(module):
        name = (
            node.id
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
            else node.arg
            if isinstance(node, ast.arg)
            else None
        )
        if name and len(name) == 1 and name != "_":
            issues.append(
                DocumentationIssue(
                    str(path),
                    node.lineno,
                    name,
                    "Use a descriptive name instead of a single-letter binding.",
                )
            )
    for node in module.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and (
                target.id.isupper() or target.id == "__all__"
            ):
                preceding = lines[node.lineno - 2].strip() if node.lineno > 1 else ""
                if not preceding.startswith("#: "):
                    issues.append(
                        DocumentationIssue(
                            str(path),
                            node.lineno,
                            target.id,
                            "Explain this module constant with a preceding #: attribute comment.",
                        )
                    )
    for node in ast.walk(module):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        documentation = ast.get_docstring(node) or ""
        name = getattr(node, "name", "<module>")
        line = getattr(node, "lineno", 1)
        if not documentation_summary(documentation):
            issues.append(
                DocumentationIssue(
                    str(path),
                    line,
                    name,
                    "Add a concrete module/class/callable docstring.",
                )
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(
                DocumentationIssue(str(path), line, symbol, reason)
                for symbol, reason in function_issues(node, documentation)
            )
        if isinstance(node, ast.ClassDef):
            documented = described_fields(documentation, "Attributes")
            issues.extend(
                DocumentationIssue(
                    str(path),
                    line,
                    attribute,
                    "Document this attribute in the class Attributes section.",
                )
                for attribute in sorted(class_attributes(node) - set(documented))
            )
    return issues


def python_files(root):
    """Discover maintained Python files, automatically including newly added modules.

    Args:
        root: Repository root directory as a Path.

    Returns:
        Sorted source paths, excluding generated caches and installation metadata.
    """
    return sorted(
        path
        for directory in PYTHON_DIRECTORIES
        for path in (root / directory).rglob("*.py")
        if not any(
            part.endswith(".egg-info") or part == "__pycache__" for part in path.parts
        )
    )


def audit_repository(root):
    """Check every maintained Python source and test module at a repository root.

    Args:
        root: Repository root directory as a Path.

    Returns:
        Complete issue list; an empty result represents 100% required documentation.
    """
    return [
        issue
        for path in python_files(root)
        for issue in python_issues(path.read_text(), path.relative_to(root))
    ]
