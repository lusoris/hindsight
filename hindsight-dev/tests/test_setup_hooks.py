"""Hook setup must keep each checkout usable after a linked worktree is removed."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2]
HOOK_MESSAGE = "setup-hooks regression hook"


def _git(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


def _setup(checkout: Path, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(
        ["bash", str(checkout / "scripts/setup-hooks.sh")],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


def _assert_hook_blocks_commit(root: Path, env: dict[str, str]) -> None:
    before = _git(root, env, "rev-parse", "HEAD").stdout.strip()
    commit = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Must be refused by hook"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert commit.returncode != 0, commit.stdout + commit.stderr
    assert HOOK_MESSAGE in commit.stderr, commit.stdout + commit.stderr
    assert _git(root, env, "rev-parse", "HEAD").stdout.strip() == before


def _make_hook_repository(root: Path, linked: Path) -> dict[str, str]:
    # Keep inherited Git settings and user/system configuration outside the fixture.
    fixture_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    fixture_env["GIT_CONFIG_GLOBAL"] = os.devnull
    fixture_env["GIT_CONFIG_NOSYSTEM"] = "1"
    fixture_env["HOME"] = str(root.parent)
    root.mkdir()
    _git(root, fixture_env, "init", "--initial-branch=main")
    _git(root, fixture_env, "config", "--local", "user.name", "Hook Test")
    _git(root, fixture_env, "config", "--local", "user.email", "hook-test@example.invalid")
    hooks = root / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{HOOK_MESSAGE}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    scripts = root / "scripts"
    scripts.mkdir()
    # The dispatcher can point at the baseline script for the negative control.
    script_source = Path(os.environ.get("SETUP_HOOKS_SCRIPT", str(SOURCE_ROOT / "scripts/setup-hooks.sh")))
    shutil.copy2(script_source, scripts / "setup-hooks.sh")
    _git(root, fixture_env, "add", ".githooks/pre-commit", "scripts/setup-hooks.sh")
    _git(root, fixture_env, "commit", "-m", "Seed hook fixture")
    _git(root, fixture_env, "worktree", "add", "-b", "linked", str(linked))
    return fixture_env


def test_setup_from_linked_worktree_survives_removal(tmp_path: Path) -> None:
    root = tmp_path / "main checkout"
    linked = tmp_path / "linked checkout"
    env = _make_hook_repository(root, linked)
    _setup(linked, linked, env)
    stored_path = _git(root, env, "config", "--get", "core.hooksPath").stdout.strip()
    worktrees = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in _git(root, env, "worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 2
    for checkout in worktrees:
        resolved_hooks = (checkout / stored_path).resolve()
        for other_linked in worktrees[1:]:
            if other_linked != checkout:
                assert not resolved_hooks.is_relative_to(other_linked), f"{checkout} uses hooks in {other_linked}"
    _git(root, env, "worktree", "remove", "--force", str(linked))
    assert not linked.exists()
    hooks = (root / _git(root, env, "rev-parse", "--git-path", "hooks").stdout.strip()).resolve()
    assert hooks.is_dir()
    assert (hooks / "pre-commit").is_file()
    assert (hooks / "pre-commit").stat().st_mode & 0o111
    _assert_hook_blocks_commit(root, env)


@pytest.mark.parametrize("subdirectory", [".", "scripts"])
def test_setup_from_main_checkout(tmp_path: Path, subdirectory: str) -> None:
    root = tmp_path / "main checkout"
    linked = tmp_path / "linked checkout"
    env = _make_hook_repository(root, linked)
    _git(root, env, "config", "--local", "core.hooksPath", str(linked / ".githooks"))
    _setup(root, root / subdirectory, env)
    assert _git(root, env, "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"
    assert _git(linked, env, "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"
    _assert_hook_blocks_commit(root, env)
