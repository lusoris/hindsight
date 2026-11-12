"""Compensating process-start checks for every Bandit exclusion in .codacy.yml."""

import ast
from pathlib import Path

import pytest
import yaml

SOURCE_ROOT = Path(__file__).resolve().parents[2]

# The only attributes Bandit-excluded files may use on modules that start processes.
# Their tests must run git and shell wrappers; this shared check preserves the argv-list,
# direct-call, and no-shell contract on which each .codacy.yml Bandit exclusion rests.
ALLOWED_PROCESS_ATTRS = {"subprocess": {"run", "CompletedProcess"}, "os": {"environ", "devnull", "pathsep"}}


def _is_argv_list(node: ast.expr) -> bool:
    """A list literal, or list literals joined with +."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_argv_list(node.left) and _is_argv_list(node.right)
    return isinstance(node, ast.List)


def process_start_problems(source: str) -> list[str]:
    """Return violations of the process-start contract for Bandit-excluded source.

    Processes start only through a direct subprocess.run call with an argv list and no shell keyword; any other
    attribute of subprocess or os, an import that renames them, or subprocess.run used without being called fails.
    """
    tree = ast.parse(source)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ALLOWED_PROCESS_ATTRS:
            problems.append(f"line {node.lineno}: from {node.module} import")
        elif isinstance(node, ast.Import):
            problems += [
                f"line {node.lineno}: import {alias.name} as {alias.asname}"
                for alias in node.names
                if alias.name in ALLOWED_PROCESS_ATTRS and alias.asname
            ]
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in ALLOWED_PROCESS_ATTRS
        ):
            name = f"{node.value.id}.{node.attr}"
            if node.attr not in ALLOWED_PROCESS_ATTRS[node.value.id]:
                problems.append(f"line {node.lineno}: {name} is not allowed")
            elif name == "subprocess.run" and id(node) not in called:
                problems.append(f"line {node.lineno}: subprocess.run used without a direct call")
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run":
            if not node.args or not _is_argv_list(node.args[0]):
                problems.append(f"line {node.lineno}: subprocess.run without an argv list")
            if any(keyword.arg in ("shell", None) for keyword in node.keywords):
                problems.append(f"line {node.lineno}: subprocess.run with shell= or **kwargs")
    return problems


def _bandit_excluded_paths() -> list[str]:
    """Read the concrete files whose Bandit exclusions need the shared process-start check."""
    config = yaml.safe_load((SOURCE_ROOT / ".codacy.yml").read_text(encoding="utf-8"))
    return config["engines"]["bandit"]["exclude_paths"]


def test_bandit_exclusions_are_concrete_python_files() -> None:
    """The source inspector can only cover concrete Python files, never exclusion globs."""
    for path in _bandit_excluded_paths():
        assert path.endswith(".py"), path
        assert not any(char in path for char in "*?[{"), path
        assert (SOURCE_ROOT / path).is_file(), path


@pytest.mark.parametrize("path", _bandit_excluded_paths(), ids=_bandit_excluded_paths())
def test_bandit_excluded_module_starts_processes_only_with_argv_lists(path: str) -> None:
    """Every declared Bandit exclusion must retain the process-start safety contract."""
    text = (SOURCE_ROOT / path).read_text(encoding="utf-8")
    assert process_start_problems(text) == []


@pytest.mark.parametrize(
    "source,expected",
    [
        ('subprocess.run("git status", shell=True)', "subprocess.run without an argv list"),
        ('subprocess.run("git status", shell=True)', "subprocess.run with shell= or **kwargs"),
        ('subprocess.run(["git"], **kw)', "subprocess.run with shell= or **kwargs"),
        ('subprocess.Popen(["git"])', "subprocess.Popen is not allowed"),
        ('os.system("git")', "os.system is not allowed"),
        ("from subprocess import run", "from subprocess import"),
        ("import subprocess as sp", "import subprocess as sp"),
        ("runner = subprocess.run", "subprocess.run used without a direct call"),
    ],
)
def test_process_start_problems_rejects_unsafe_source(source: str, expected: str) -> None:
    """Planted unsafe calls, imports, and aliases must be detected by the compensating check."""
    assert any(expected in problem for problem in process_start_problems(source))


def test_process_start_problems_accepts_argv_list() -> None:
    """An argv list may include resolved executable names and expanded argument lists."""
    assert process_start_problems("subprocess.run([git, *args], check=True)") == []
