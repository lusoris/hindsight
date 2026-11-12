"""Enforce lockfile-pinned tool invocations in scripts/hooks/lint.sh.

Prevents tool drift where unpinned, ambient, or registry-fetched binaries are executed.
Incident dates: 2026-11-08 and 2026-11-12.
- Prettier: npx without local node_modules assumes --yes without a TTY or in CI,
  downloading latest unpinned prettier (3.10.0 vs pinned 3.7.4) and rewriting files.
- Ruff: uv run --no-project from an integration directory picks up an integration-local
  .venv or PATH ruff rather than the repository root lockfile pin (0.14.9).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
LINT_SH = SOURCE_ROOT / "scripts" / "hooks" / "lint.sh"

ALLOWED_RUFF_PREFIXES = (
    "uv run --frozen ruff check",
    "uv run --frozen ruff format",
    "uv run --frozen --no-sync ruff check",
    "uv run --frozen --no-sync --project $REPO_ROOT ruff check",
    "uv run --frozen --no-sync --project $REPO_ROOT ruff format",
)

ALLOWED_PRETTIER_PREFIXES = ("$PRETTIER --write",)

# Every stub tool appends one JSON line with these fields, so a run's tool calls read back as typed records.
RECORDER = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["DEV_SCRIPT_CALLS"], "a") as f:
    f.write(json.dumps({{"command": os.path.basename(sys.argv[0]), "path": os.path.abspath(sys.argv[0]), "args": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
sys.exit(0)
"""


@dataclass
class RecordedCall:
    command: str
    path: str
    args: list[str]
    cwd: str


def _read_calls(calls_file: Path) -> list[RecordedCall]:
    if not calls_file.exists():
        return []
    return [RecordedCall(**json.loads(line)) for line in calls_file.read_text().splitlines()]


def _install_stubs(bin_dir: Path) -> None:
    bin_dir.mkdir()
    for cmd in ("uv", "npx", "npm", "ruff"):
        stub = bin_dir / cmd
        stub.write_text(RECORDER)
        stub.chmod(0o755)


def _extract_run_task_commands(script_text: str) -> list[str]:
    """Extract command strings passed as third argument to run_task."""
    pattern = r'run_task\s+(?:"[^"]+"|\S+)\s+(?:"[^"]+"|\S+)\s+"([^"]+)"'
    return [m.group(1) for m in re.finditer(pattern, script_text)]


def test_lint_sh_invocation_allowlist() -> None:
    """Every ruff/prettier command in lint.sh must use allowed pinned prefixes, and npx is forbidden."""
    script_text = LINT_SH.read_text()
    # Comments may name npx to explain why it is gone; only code lines must not run it.
    code_lines = [line for line in script_text.splitlines() if not line.lstrip().startswith("#")]
    npx_lines = [line for line in code_lines if re.search(r"\bnpx\b", line)]
    assert npx_lines == [], f"lint.sh runs forbidden npx: {npx_lines}"

    commands = _extract_run_task_commands(script_text)
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
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    hooks_dir = repo / "scripts" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(LINT_SH, hooks_dir / "lint.sh")

    calls_file = tmp_path / "calls.jsonl"
    bin_dir = tmp_path / "bin"
    _install_stubs(bin_dir)

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    old_path = env.get("PATH", "")
    env["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
    env["DEV_SCRIPT_CALLS"] = str(calls_file)

    result = subprocess.run(
        ["bash", str(hooks_dir / "lint.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "node_modules/.bin/prettier" in combined_output
    assert _read_calls(calls_file) == []


def test_lint_sh_runs_pinned_tools(tmp_path: Path) -> None:
    """lint.sh runs only root node_modules prettier and uv with --frozen --project for integration ruff."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
        env=env,
    )

    hooks_dir = repo / "scripts" / "hooks"
    hooks_dir.mkdir(parents=True)
    shutil.copy2(LINT_SH, hooks_dir / "lint.sh")

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
    scripts_dir.mkdir(parents=True, exist_ok=True)
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

    calls_file = tmp_path / "calls.jsonl"
    bin_dir = tmp_path / "bin"
    _install_stubs(bin_dir)

    # Root node_modules/.bin/prettier binary stub
    prettier_bin = repo / "node_modules" / ".bin" / "prettier"
    prettier_bin.parent.mkdir(parents=True)
    prettier_bin.write_text(RECORDER)
    prettier_bin.chmod(0o755)

    old_path = env.get("PATH", "")
    env["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
    env["DEV_SCRIPT_CALLS"] = str(calls_file)

    result = subprocess.run(
        ["bash", str(hooks_dir / "lint.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, f"lint.sh failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    records = _read_calls(calls_file)

    # Every prettier call came from the fake root node_modules binary, none from PATH
    prettier_calls = [r for r in records if r.command == "prettier"]
    assert len(prettier_calls) >= 4, f"Expected at least 4 prettier calls, got {len(prettier_calls)}: {prettier_calls}"
    assert all(r.path == str(prettier_bin.resolve()) for r in prettier_calls)

    # Assert no npx call was recorded
    npx_calls = [r for r in records if r.command == "npx"]
    assert len(npx_calls) == 0, f"Forbidden npx was called: {npx_calls}"

    # Assert recorded uv calls for integration ruff tasks contain --frozen and --project, and not --no-project
    int_ruff_calls = [r for r in records if r.command == "uv" and "ruff" in r.args and "test-py" in r.cwd]
    assert len(int_ruff_calls) == 2, f"Expected 2 integration ruff calls, got {len(int_ruff_calls)}: {int_ruff_calls}"
    for call in int_ruff_calls:
        assert "--frozen" in call.args
        assert "--project" in call.args
        assert "--no-project" not in call.args
