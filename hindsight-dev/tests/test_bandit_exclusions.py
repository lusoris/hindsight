"""Allow-list imports, module access, builtins, attributes and process calls in Bandit exclusions.

Only direct subprocess.run calls with one argv list and approved keywords may start processes.
sys.modules is available solely as a subscript assignment target for the path-based test loader.
"""

import ast
import builtins
from pathlib import Path

import pytest
import yaml

SOURCE_ROOT = Path(__file__).resolve().parents[2]

# The former process-module deny-list missed other import paths to os. These are
# exactly the imports used by the two excluded files; aliases cannot hide their roots.
ALLOWED_IMPORTS = {"datetime", "importlib.util", "json", "os", "pytest", "re", "shlex", "shutil", "subprocess", "sys"}

# Only these named imports support the fixtures without exposing module aliases or wildcard imports.
ALLOWED_FROM_IMPORTS = {("__future__", "annotations"), ("dataclasses", "dataclass"), ("pathlib", "Path")}

# Bound every imported module, including the two-level loader, to measured attributes;
# allowing only os/subprocess attributes left shutil.os and similar paths unchecked.
ALLOWED_MODULE_ATTRIBUTES = {
    "datetime": {"UTC", "date", "datetime", "timedelta"},
    "importlib": {"util"},
    "importlib.util": {"module_from_spec", "spec_from_file_location"},
    "json": {"dumps", "loads"},
    "os": {"devnull", "environ", "pathsep"},
    "pytest": {"mark"},
    "re": {"search"},
    "shlex": {"quote", "split"},
    "shutil": {"copy2", "copyfile", "which"},
    "subprocess": {"CompletedProcess", "run"},
    "sys": {"executable", "modules"},
}

# The loader registers its module with a plain assignment; all other contexts can
# read or expose sys.modules and recover unrestricted modules.
ALLOWED_SYS_MODULES_ASSIGNMENTS = (ast.Assign,)

# These are the measured builtin names; dynamic execution and namespace lookup
# must require explicit review rather than escaping a list of known dangerous calls.
ALLOWED_BUILTINS = {"RuntimeError", "all", "any", "dict", "int", "len", "list", "str"}

# All 67 measured attribute names are needed by the fixtures. Checking every
# attribute also closes paths through local objects, beyond imported-module roots.
ALLOWED_ATTRIBUTE_NAMES = {
    "CompletedProcess",
    "MALFORMED_NOSEC",
    "MARKER_RE",
    # Explicit + keeps the name from matching scanner-suppressions lint; ruff format joins adjacent implicit literals.
    "NO" + "SEC_COMMENT_ONLY",
    "Problem",
    "UNKNOWN_BANDIT_ID",
    "UTC",
    "args",
    "check_text",
    "chmod",
    "command",
    "copy2",
    "copyfile",
    "count",
    "cwd",
    "date",
    "datetime",
    "devnull",
    "dumps",
    "encode",
    "environ",
    "err",
    "exec_module",
    "executable",
    "exists",
    "findall",
    "get",
    "index",
    "isoformat",
    "items",
    "join",
    "loader",
    "loads",
    "lower",
    "lstrip",
    "main",
    "mark",
    "message",
    "mkdir",
    "module_from_spec",
    "modules",
    "now",
    "parametrize",
    "parent",
    "parents",
    "path",
    "pathsep",
    "quote",
    "read_text",
    "readouterr",
    "replace",
    "resolve",
    "returncode",
    "run",
    "search",
    "spec_from_file_location",
    "split",
    "splitlines",
    "startswith",
    "stderr",
    "stdout",
    "symlink_to",
    "timedelta",
    "util",
    "which",
    "write_bytes",
    "write_text",
}

# Only the measured run keywords are permitted. The earlier shell/**kwargs check
# missed executable= and positional Popen arguments, including positional shell.
ALLOWED_RUN_KEYWORDS = {"capture_output", "check", "cwd", "env", "text", "timeout"}


def _is_argv_list(node: ast.expr) -> bool:
    """A list literal, or list literals joined with +."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_argv_list(node.left) and _is_argv_list(node.right)
    return isinstance(node, ast.List)


def process_start_problems(source: str) -> list[str]:
    """Return violations of the allow-list contract for Bandit-excluded source.

    Imports and from-import pairs, module attributes, builtins and all attribute
    names must be approved explicitly. Module names cannot escape as bare values;
    sys.modules can only be a subscript assignment target. Process starts require
    a direct subprocess.run call, exactly one argv list and only approved keywords.
    """
    tree = ast.parse(source)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    # Track individual nodes: a permitted use cannot excuse a separate bare module
    # reference, including an alias of the intermediate importlib.util module.
    attribute_bases = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and ast.unparse(node.value) in ALLOWED_MODULE_ATTRIBUTES
        and node.attr in ALLOWED_MODULE_ATTRIBUTES[ast.unparse(node.value)]
    }
    modules_assignment_targets = {
        id(target.value)
        for node in ast.walk(tree)
        if isinstance(node, ALLOWED_SYS_MODULES_ASSIGNMENTS)
        for target in node.targets
        if isinstance(target, ast.Subscript) and ast.unparse(target.value) == "sys.modules"
    }
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and hasattr(builtins, node.id) and node.id not in ALLOWED_BUILTINS:
            problems.append(f"line {node.lineno}: builtin {node.id} is not allowed")
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = ast.unparse(node)
            if name in ALLOWED_MODULE_ATTRIBUTES and id(node) not in attribute_bases:
                problems.append(f"line {node.lineno}: bare reference to {name}")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.level or alias.asname or (node.module, alias.name) not in ALLOWED_FROM_IMPORTS:
                    module = "." * node.level + (node.module or "")
                    problems.append(f"line {node.lineno}: from {module} import {alias.name} is not allowed")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS or alias.asname:
                    name = f"{alias.name} as {alias.asname}" if alias.asname else alias.name
                    problems.append(f"line {node.lineno}: import {name} is not allowed")
        elif isinstance(node, ast.Attribute):
            base = ast.unparse(node.value)
            name = f"{base}.{node.attr}"
            if node.attr not in ALLOWED_ATTRIBUTE_NAMES or (
                base in ALLOWED_MODULE_ATTRIBUTES and node.attr not in ALLOWED_MODULE_ATTRIBUTES[base]
            ):
                problems.append(f"line {node.lineno}: {name} is not allowed")
            if name == "sys.modules" and id(node) not in modules_assignment_targets:
                problems.append(f"line {node.lineno}: sys.modules is only allowed as a subscript assignment target")
            if name == "subprocess.run" and id(node) not in called:
                problems.append(f"line {node.lineno}: subprocess.run used without a direct call")
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run":
            if len(node.args) != 1 or not _is_argv_list(node.args[0]):
                problems.append(f"line {node.lineno}: subprocess.run requires exactly one argv list")
            if any(keyword.arg not in ALLOWED_RUN_KEYWORDS for keyword in node.keywords):
                problems.append(f"line {node.lineno}: subprocess.run keyword is not allowed")
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
        ('subprocess.run("git status", shell=True)', "subprocess.run requires exactly one argv list"),
        ('subprocess.run("git status", shell=True)', "subprocess.run keyword is not allowed"),
        ('subprocess.run(["git"], **kw)', "subprocess.run keyword is not allowed"),
        ('subprocess.Popen(["git"])', "subprocess.Popen is not allowed"),
        ('os.system("git")', "os.system is not allowed"),
        ("from subprocess import run", "from subprocess import"),
        ("import subprocess as sp", "import subprocess as sp"),
        ("runner = subprocess.run", "subprocess.run used without a direct call"),
        ("sp = subprocess", "bare reference to subprocess"),
        ('getattr(subprocess, "Popen")(["git"])', "bare reference to subprocess"),
        ('getattr(os, "system")("git")', "bare reference to os"),
        ('__import__("subprocess")', "line 1: builtin __import__ is not allowed"),
        ('importlib.import_module("subprocess")', "line 1: importlib.import_module is not allowed"),
        ('importlib.__import__("subprocess")', "line 1: importlib.__import__ is not allowed"),
        ("m = importlib", "line 1: bare reference to importlib"),
        ("u = importlib.util", "line 1: bare reference to importlib.util"),
        ("from importlib import import_module", "line 1: from importlib import"),
        ("from importlib.util import find_spec", "line 1: from importlib.util import"),
        ("import importlib as il", "line 1: import importlib as il"),
        ("import importlib.util as iu", "line 1: import importlib.util as iu"),
        (
            'importlib.util.module_from_spec(spec)\nimportlib.import_module("subprocess")',
            "line 2: importlib.import_module is not allowed",
        ),
        ("importlib.util.module_from_spec(spec)\nm = importlib", "line 2: bare reference to importlib"),
        ("importlib.util.module_from_spec(spec)\nu = importlib.util", "line 2: bare reference to importlib.util"),
        ('from os.path import os as host_os\nhost_os.system("x")', "from os.path import os is not allowed"),
        ('import shutil\nshutil.os.system("x")', "shutil.os is not allowed"),
        ('import sys\nsys.modules["os"].system("x")', "sys.modules is only allowed as a subscript assignment target"),
        ('exec("x")', "builtin exec is not allowed"),
        ('eval("1")', "builtin eval is not allowed"),
        ('compile("x", "f", "exec")', "builtin compile is not allowed"),
        ('vars()["x"]', "builtin vars is not allowed"),
        ('globals()["x"]', "builtin globals is not allowed"),
        (
            'subprocess.run(["git"], -1, None, None, None, None, None, True, True)',
            "subprocess.run requires exactly one argv list",
        ),
        ('subprocess.run(["git"], executable="/bin/sh")', "subprocess.run keyword is not allowed"),
        ("import builtins", "import builtins"),
        ("from . import x", "from . import x is not allowed"),
        ("from os import *", "from os import * is not allowed"),
        ('checker.subprocess.run(["git"])', "checker.subprocess is not allowed"),
        ("x.__globals__", "x.__globals__ is not allowed"),
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
    assert process_start_problems("import importlib.util") == []
    source = """import importlib.util
import sys

spec = importlib.util.spec_from_file_location("check_scanner_suppressions", CHECKER_PATH)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
sys.modules["check_scanner_suppressions"] = checker
spec.loader.exec_module(checker)
"""
    assert process_start_problems(source) == []


def test_process_start_problems_accepts_sys_modules_assignment() -> None:
    """The loader can register a module without gaining read access to the module registry."""
    assert process_start_problems('import sys\nsys.modules["check_scanner_suppressions"] = module') == []
