"""Compensating process-start checks for every Bandit exclusion in .codacy.yml."""

import ast
from pathlib import Path

import pytest
import yaml

SOURCE_ROOT = Path(__file__).resolve().parents[2]

# In Bandit-excluded files, process module names may appear only as the base of an allowed
# attribute; importlib uses are restricted to ALLOWED_IMPORTLIB_CALLS.
# Bare references and other dynamic imports fail the check.
# Their tests must run git and shell wrappers; this shared check preserves the argv-list,
# direct-call, and no-shell contract on which each .codacy.yml Bandit exclusion rests.
ALLOWED_PROCESS_ATTRS = {"subprocess": {"run", "CompletedProcess"}, "os": {"environ", "devnull", "pathsep"}}

# The excluded scanner test loads a hyphen-named script by path; no other importlib use is allowed.
ALLOWED_IMPORTLIB_CALLS = {"importlib.util.spec_from_file_location", "importlib.util.module_from_spec"}


def _is_argv_list(node: ast.expr) -> bool:
    """A list literal, or list literals joined with +."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_argv_list(node.left) and _is_argv_list(node.right)
    return isinstance(node, ast.List)


def process_start_problems(source: str) -> list[str]:
    """Return violations of the process-start contract for Bandit-excluded source.

    Processes start only through a direct subprocess.run call with an argv list and no shell keyword.
    The subprocess and os module names appear only as the base of an allowed attribute. Other attributes, imports
    that rename them, subprocess.run used without being called, and dynamic imports via __import__ fail.
    Importlib names may only be the base of an attribute in ALLOWED_IMPORTLIB_CALLS; aliases and from-imports fail.
    """
    tree = ast.parse(source)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    # Track each base node so an allowed attribute cannot excuse a separate bare reference.
    attribute_bases = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ALLOWED_PROCESS_ATTRS
    }
    # Match each two-level loader attribute so it cannot excuse another importlib reference.
    importlib_bases = {
        id(node.value.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "importlib"
        and ast.unparse(node) in ALLOWED_IMPORTLIB_CALLS
    }
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in ALLOWED_PROCESS_ATTRS and id(node) not in attribute_bases:
                problems.append(f"line {node.lineno}: bare reference to {node.id}")
            elif node.id == "__import__":
                problems.append(f"line {node.lineno}: __import__ is not allowed")
            elif node.id == "importlib" and id(node) not in importlib_bases:
                problems.append(f"line {node.lineno}: importlib use is not allowed")
        if isinstance(node, ast.ImportFrom) and node.module in ALLOWED_PROCESS_ATTRS:
            problems.append(f"line {node.lineno}: from {node.module} import")
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "importlib":
            problems.append(f"line {node.lineno}: from {node.module} import")
        elif isinstance(node, ast.Import):
            problems += [
                f"line {node.lineno}: import {alias.name} as {alias.asname}"
                for alias in node.names
                if (alias.name in ALLOWED_PROCESS_ATTRS or alias.name.split(".")[0] == "importlib") and alias.asname
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
        ("sp = subprocess", "bare reference to subprocess"),
        ('getattr(subprocess, "Popen")(["git"])', "bare reference to subprocess"),
        ('getattr(os, "system")("git")', "bare reference to os"),
        ('__import__("subprocess")', "line 1: __import__ is not allowed"),
        ('importlib.import_module("subprocess")', "line 1: importlib use is not allowed"),
        ('importlib.__import__("subprocess")', "line 1: importlib use is not allowed"),
        ("m = importlib", "line 1: importlib use is not allowed"),
        ("u = importlib.util", "line 1: importlib use is not allowed"),
        ("from importlib import import_module", "line 1: from importlib import"),
        ("from importlib.util import find_spec", "line 1: from importlib.util import"),
        ("import importlib as il", "line 1: import importlib as il"),
        ("import importlib.util as iu", "line 1: import importlib.util as iu"),
        (
            'importlib.util.module_from_spec(spec)\nimportlib.import_module("subprocess")',
            "line 2: importlib use is not allowed",
        ),
        ("importlib.util.module_from_spec(spec)\nm = importlib", "line 2: importlib use is not allowed"),
        ("importlib.util.module_from_spec(spec)\nu = importlib.util", "line 2: importlib use is not allowed"),
    ],
)
def test_process_start_problems_rejects_unsafe_source(source: str, expected: str) -> None:
    """Planted unsafe calls, imports, and aliases must be detected by the compensating check."""
    assert any(expected in problem for problem in process_start_problems(source))


def test_process_start_problems_accepts_argv_list() -> None:
    """Argv lists, return annotations, and environment access may use the allowed attributes."""
    assert process_start_problems("subprocess.run([git, *args], check=True)") == []
    source = """def run() -> subprocess.CompletedProcess[str]:
    return subprocess.run([git, *args], env=os.environ, check=False)
"""
    assert process_start_problems(source) == []


def test_process_start_problems_accepts_importlib_loader() -> None:
    """The scanner's path-based loader may use only the two allowed importlib attributes."""
    assert process_start_problems("import importlib") == []
    source = """import importlib.util
import sys

spec = importlib.util.spec_from_file_location("check_scanner_suppressions", CHECKER_PATH)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
sys.modules["check_scanner_suppressions"] = checker
spec.loader.exec_module(checker)
"""
    assert process_start_problems(source) == []
