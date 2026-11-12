"""Enforce lockfile-pinned tool invocations in scripts/hooks/lint.sh.

Prevents tool drift where unpinned, ambient, or registry-fetched binaries are executed.
Incident dates: 2026-11-08 and 2026-11-12.
- Prettier: npx without local node_modules assumes --yes without a TTY or in CI,
  downloading latest unpinned prettier (3.10.0 vs pinned 3.7.4) and rewriting files.
  A node_modules left over from an older lock drifts the same way, so the binary's
  version must also match package-lock.json.
- Ruff: uv run --no-project from an integration directory picks up an integration-local
  .venv or PATH ruff rather than the repository root lockfile pin (0.14.9).
"""

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
LINT_SH = SOURCE_ROOT / "scripts" / "hooks" / "lint.sh"
LOCKED_PRETTIER = "3.7.4"


def _require(tool: str) -> str:
    """Absolute path of a tool the tests start themselves, resolved before any stub directory joins PATH."""
    path = shutil.which(tool)
    if path is None:
        raise RuntimeError(f"{tool} must be on PATH to run these tests")
    return path


GIT = _require("git")
BASH = _require("bash")

ALLOWED_RUFF_PREFIXES = (
    "uv run --frozen ruff check",
    "uv run --frozen ruff format",
    "uv run --frozen --no-sync ruff check",
    "uv run --frozen --no-sync --project $REPO_ROOT ruff check",
    "uv run --frozen --no-sync --project $REPO_ROOT ruff format",
)

ALLOWED_PRETTIER_PREFIXES = ("$PRETTIER --write",)

# The only attributes this module may use on the modules that start processes. .codacy.yml excludes this file from
# Bandit (B404 import, B603 call: the tests must run git and lint.sh), so
# test_this_module_starts_processes_only_with_argv_lists keeps the claim that exclusion rests on.
ALLOWED_PROCESS_ATTRS = {"subprocess": {"run", "CompletedProcess"}, "os": {"environ", "devnull", "pathsep"}}


def _recorder(version: str, python: str = sys.executable) -> str:
    """A stub tool: appends its call as one JSON line, so a run's tool calls read back as typed records.

    It answers `--version` with `version`, which is how lint.sh asks prettier what it is.

    The stub is a /bin/sh launcher that execs `python` on the recording program. It used to name `python` in its
    `#!` line, but that line has no quoting: the kernel ends the interpreter path at the first space or tab, so an
    interpreter under such a path (a checkout or virtualenv in a directory with a space) never started. shlex.quote
    keeps the path one word; "$0" hands the program the stub's own path for the record, and "$@" every argument as is.
    """
    program = f"""import json, os, sys
stub, args = sys.argv[1], sys.argv[2:]
with open(os.environ["DEV_SCRIPT_CALLS"], "a") as f:
    record = {{"command": os.path.basename(stub), "path": os.path.abspath(stub), "args": args, "cwd": os.getcwd()}}
    f.write(json.dumps(record) + "\\n")
if args == ["--version"]:
    print({version!r})
"""
    return f'#!/bin/sh\nexec {shlex.quote(python)} -c {shlex.quote(program)} "$0" "$@"\n'


@dataclass
class RecordedCall:
    command: str
    path: str
    args: list[str]
    cwd: str


def _read_calls(env: dict[str, str]) -> list[RecordedCall]:
    calls_file = Path(env["DEV_SCRIPT_CALLS"])
    if not calls_file.exists():
        return []
    return [RecordedCall(**json.loads(line)) for line in calls_file.read_text().splitlines()]


def _lint_env(tmp_path: Path) -> dict[str, str]:
    """Environment for one hermetic lint.sh run: recording stubs first on PATH, no user or system git config.

    prettier and ruff are stubbed too, so a lookup of either on PATH is recorded instead of reaching a real
    binary; lint.sh may only run prettier by its node_modules path and ruff through uv.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for cmd in ("uv", "npx", "npm", "ruff", "prettier"):
        stub = bin_dir / cmd
        stub.write_text(_recorder("0.0.0-from-path"))
        stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["DEV_SCRIPT_CALLS"] = str(tmp_path / "calls.jsonl")
    return env


def _make_repo(repo: Path, env: dict[str, str]) -> Path:
    """A git repository with one empty commit (lint.sh diffs against HEAD) and a copy of lint.sh."""
    repo.mkdir()
    subprocess.run([GIT, "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(
        [GIT, "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com"]
        + ["commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=env,
    )
    hooks_dir = repo / "scripts" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(LINT_SH, hooks_dir / "lint.sh")
    return repo


def _install_prettier(repo: Path, version: str) -> Path:
    """A root node_modules prettier stub reporting `version`, beside a package-lock.json pinning LOCKED_PRETTIER."""
    prettier_bin = repo / "node_modules" / ".bin" / "prettier"
    prettier_bin.parent.mkdir(parents=True)
    prettier_bin.write_text(_recorder(version))
    prettier_bin.chmod(0o755)
    lock = {"packages": {"node_modules/prettier": {"version": LOCKED_PRETTIER}}}
    (repo / "package-lock.json").write_text(json.dumps(lock))
    return prettier_bin


def _run_lint(repo: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(repo / "scripts" / "hooks" / "lint.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _code_lines(script_text: str) -> list[str]:
    """Non-comment lines of a shell script, backslash continuations joined so one command is one line."""
    joined = script_text.replace("\\\n", " ")
    return [line for line in joined.splitlines() if not line.lstrip().startswith("#")]


def _run_task_commands(script_text: str) -> list[str]:
    """The command of every run_task line: its words after the task name and directory, shell quotes removed."""
    return [
        " ".join(shlex.split(line)[3:]) for line in _code_lines(script_text) if line.lstrip().startswith("run_task ")
    ]


def test_recorder_stub_runs_under_an_interpreter_path_with_a_space(tmp_path: Path) -> None:
    """A stub whose Python lives under a path with a space starts, records itself, and forwards arguments intact.

    The stubs once named their interpreter in a `#!` line, which cannot quote, so under such a path they never ran.
    """
    python = tmp_path / "py with space" / "python"
    python.parent.mkdir()
    python.symlink_to(sys.executable)
    stub = tmp_path / "tool"
    stub.write_text(_recorder("9.9.9", python=str(python)))
    stub.chmod(0o755)
    env = {**os.environ, "DEV_SCRIPT_CALLS": str(tmp_path / "calls.jsonl")}

    version = subprocess.run(
        [str(stub), "--version"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10, check=False
    )
    spaced = subprocess.run(
        [str(stub), "a b", "", "$HOME"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10, check=False
    )

    assert (version.returncode, version.stdout) == (0, "9.9.9\n"), version
    assert (spaced.returncode, spaced.stdout) == (0, ""), spaced
    cwd = str(tmp_path.resolve())
    assert _read_calls(env) == [
        RecordedCall(command="tool", path=str(stub), args=["--version"], cwd=cwd),
        RecordedCall(command="tool", path=str(stub), args=["a b", "", "$HOME"], cwd=cwd),
    ]


def test_lint_sh_invocation_allowlist() -> None:
    """Every ruff/prettier command in lint.sh uses an allowed pinned prefix; npx and ruff outside run_task are forbidden."""
    script_text = LINT_SH.read_text()
    code_lines = _code_lines(script_text)
    # Comments may name npx to explain why it is gone; only code lines must not run it.
    npx_lines = [line for line in code_lines if re.search(r"\bnpx\b", line)]
    assert npx_lines == [], f"lint.sh runs forbidden npx: {npx_lines}"
    # The prefix checks below see only run_task commands, so a ruff call anywhere else would escape them.
    stray_ruff = [
        line for line in code_lines if re.search(r"\bruff\b", line) and not line.lstrip().startswith("run_task ")
    ]
    assert stray_ruff == [], f"lint.sh mentions ruff outside run_task: {stray_ruff}"

    commands = _run_task_commands(script_text)
    ruff_commands = [cmd for cmd in commands if "ruff" in cmd.lower()]
    prettier_commands = [cmd for cmd in commands if "prettier" in cmd.lower()]

    assert len(ruff_commands) >= 5, f"Expected ruff commands, found {len(ruff_commands)}"
    assert len(prettier_commands) >= 4, f"Expected prettier commands, found {len(prettier_commands)}"

    for cmd in ruff_commands:
        assert cmd.startswith("uv run --frozen"), f"Ruff command does not start with 'uv run --frozen': {cmd!r}"
        assert any(cmd.startswith(prefix) for prefix in ALLOWED_RUFF_PREFIXES), (
            f"Ruff command {cmd!r} does not match allowed prefixes: {ALLOWED_RUFF_PREFIXES}"
        )
        assert "--no-project" not in cmd, f"Ruff command contains forbidden '--no-project': {cmd!r}"

    for cmd in prettier_commands:
        assert cmd.startswith("$PRETTIER"), f"Prettier command does not start with '$PRETTIER': {cmd!r}"
        assert any(cmd.startswith(prefix) for prefix in ALLOWED_PRETTIER_PREFIXES), (
            f"Prettier command {cmd!r} does not match allowed prefixes: {ALLOWED_PRETTIER_PREFIXES}"
        )


def test_lint_sh_refuses_when_prettier_missing(tmp_path: Path) -> None:
    """lint.sh must refuse immediately before running uv sync or any tasks when prettier is missing."""
    env = _lint_env(tmp_path)
    repo = _make_repo(tmp_path / "repo", env)

    result = _run_lint(repo, env, timeout=10)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    # The version check further down also refuses a missing binary ("is unknown"), so asserting only the path let
    # the existence check be deleted unnoticed; the message pins which check fired.
    assert "node_modules/.bin/prettier not found or not executable" in output, output
    assert _read_calls(env) == []


def test_lint_sh_refuses_a_prettier_the_lock_does_not_pin(tmp_path: Path) -> None:
    """A node_modules prettier at another version than package-lock.json is refused before uv sync or any task."""
    env = _lint_env(tmp_path)
    repo = _make_repo(tmp_path / "repo", env)
    _install_prettier(repo, "3.10.0")

    result = _run_lint(repo, env, timeout=10)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "3.10.0" in output and LOCKED_PRETTIER in output, output
    assert [call.args for call in _read_calls(env)] == [["--version"]]


def test_lint_sh_runs_pinned_tools(tmp_path: Path) -> None:
    """lint.sh runs only the root node_modules prettier, and ruff only through uv with the root lockfile.

    The checkout path holds a space on purpose: run_task once expanded its command unquoted, which split
    "$PRETTIER" (the task then failed) and "--project $REPO_ROOT" (uv got the wrong project) under such a path.
    """
    env = _lint_env(tmp_path)
    repo = _make_repo(tmp_path / "check out", env)

    # Directories required by lint.sh tasks
    for d in (
        "hindsight-control-plane/src",
        "hindsight-clients/typescript",
        "hindsight-all-npm",
        "hindsight-api-slim",
        "hindsight-dev",
        "hindsight-embed",
    ):
        (repo / d).mkdir(parents=True, exist_ok=True)
    (repo / "hindsight-control-plane" / "src" / "dummy.ts").write_text("// dummy\n")
    (repo / "ruff.toml").write_text("")
    (repo / ".prettierrc.json").write_text("{}")
    (repo / ".gitignore").write_text("")

    # Scripts lint.sh calls by path exist as no-op stubs
    scripts_dir = repo / "scripts"
    for s in (
        "check-osv-config.sh",
        "check-scanner-suppressions.sh",
        "sync-integration-config.py",
        "check-codacy-config.py",
    ):
        stub = scripts_dir / s
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)

    # Integrations: one touched Node and one touched Python integration
    node_int = repo / "hindsight-integrations" / "test-node"
    node_int.mkdir(parents=True)
    (node_int / "package.json").write_text("{}\n")
    (node_int / "index.ts").write_text("// node\n")

    py_int = repo / "hindsight-integrations" / "test-py"
    py_int.mkdir(parents=True)
    (py_int / "pyproject.toml").write_text('[project]\nname="test-py"\n')
    (py_int / "main.py").write_text("# py\n")

    prettier_bin = _install_prettier(repo, LOCKED_PRETTIER)

    result = _run_lint(repo, env, timeout=15)

    assert result.returncode == 0, f"lint.sh failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    records = _read_calls(env)

    # Every prettier call came from the fake root node_modules binary, none from PATH
    prettier_calls = [r for r in records if r.command == "prettier"]
    assert all(r.path == str(prettier_bin.resolve()) for r in prettier_calls), prettier_calls
    write_calls = [r for r in prettier_calls if "--write" in r.args]
    assert len(write_calls) >= 4, f"Expected at least 4 prettier --write calls, got {len(write_calls)}: {write_calls}"

    # Neither npx nor a ruff found on PATH ran: ruff is reachable only through uv and the root lockfile
    ambient = [r for r in records if r.command in ("npx", "ruff")]
    assert ambient == [], f"lint.sh ran npx or ruff from PATH: {ambient}"
    ruff_calls = [r for r in records if r.command == "uv" and "ruff" in r.args]
    assert all("--frozen" in r.args and "--no-project" not in r.args for r in ruff_calls), ruff_calls

    # Integration ruff tasks name the repository root as one --project argument, space included
    int_ruff_calls = [r for r in ruff_calls if "test-py" in r.cwd]
    assert len(int_ruff_calls) == 2, f"Expected 2 integration ruff calls, got {len(int_ruff_calls)}: {int_ruff_calls}"
    for call in int_ruff_calls:
        assert call.args[call.args.index("--project") + 1] == str(repo.resolve()), call.args


def _is_argv_list(node: ast.expr) -> bool:
    """A list literal, or list literals joined with +."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_argv_list(node.left) and _is_argv_list(node.right)
    return isinstance(node, ast.List)


def test_this_module_starts_processes_only_with_argv_lists() -> None:
    """Compensating check for the Bandit exclusion of this file in .codacy.yml.

    Processes start only through a direct subprocess.run call with an argv list and no shell keyword; any other
    attribute of subprocess or os, an import that renames them, or subprocess.run used without being called fails.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
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
    assert problems == [], problems
