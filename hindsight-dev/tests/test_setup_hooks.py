"""Hook setup must keep each checkout usable after a linked worktree is removed."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2]
HOOK_MESSAGE = "setup-hooks regression hook"


@dataclass
class HookRepository:
    root: Path
    linked: Path
    env: dict[str, str]

    def git(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd if cwd is not None else self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=check,
            timeout=10,
        )

    def setup(self, cwd: Path) -> None:
        checkout = self.linked if cwd.is_relative_to(self.linked) else self.root
        subprocess.run(
            ["bash", str(checkout / "scripts/setup-hooks.sh")],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

    def assert_hook_blocks_commit(self) -> None:
        before = self.git("rev-parse", "HEAD").stdout.strip()
        commit = self.git("commit", "--allow-empty", "-m", "Must be refused by hook", check=False)
        assert commit.returncode != 0, commit.stdout + commit.stderr
        assert HOOK_MESSAGE in commit.stderr, commit.stdout + commit.stderr
        assert self.git("rev-parse", "HEAD").stdout.strip() == before


@pytest.fixture
def hook_repository(tmp_path: Path) -> HookRepository:
    # Keep inherited Git settings and user/system configuration outside the fixture.
    fixture_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    fixture_env["GIT_CONFIG_GLOBAL"] = os.devnull
    fixture_env["GIT_CONFIG_NOSYSTEM"] = "1"
    fixture_env["HOME"] = str(tmp_path)
    root = tmp_path / "main checkout"
    root.mkdir()
    repository = HookRepository(root, tmp_path / "linked checkout", fixture_env)
    repository.git("init", "--initial-branch=main")
    repository.git("config", "--local", "user.name", "Hook Test")
    repository.git("config", "--local", "user.email", "hook-test@example.invalid")
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
    repository.git("add", ".githooks/pre-commit", "scripts/setup-hooks.sh")
    repository.git("commit", "-m", "Seed hook fixture")
    repository.git("worktree", "add", "-b", "linked", str(repository.linked))
    return repository


def test_setup_from_linked_worktree_survives_removal(hook_repository: HookRepository) -> None:
    repository = hook_repository
    repository.setup(repository.linked)
    stored_path = repository.git("config", "--get", "core.hooksPath").stdout.strip()
    worktrees = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in repository.git("worktree", "list", "--porcelain").stdout.splitlines()
        if line.startswith("worktree ")
    ]
    assert len(worktrees) == 2
    for checkout in worktrees:
        resolved_hooks = (checkout / stored_path).resolve()
        for linked in worktrees[1:]:
            if linked != checkout:
                assert not resolved_hooks.is_relative_to(linked), f"{checkout} uses hooks in {linked}"
    repository.git("worktree", "remove", "--force", str(repository.linked))
    assert not repository.linked.exists()
    hooks = (repository.root / repository.git("rev-parse", "--git-path", "hooks").stdout.strip()).resolve()
    assert hooks.is_dir()
    assert (hooks / "pre-commit").is_file()
    assert os.access(hooks / "pre-commit", os.X_OK)
    repository.assert_hook_blocks_commit()


@pytest.mark.parametrize("subdirectory", [".", "scripts"])
def test_setup_from_main_checkout(hook_repository: HookRepository, subdirectory: str) -> None:
    repository = hook_repository
    repository.git("config", "--local", "core.hooksPath", str(repository.linked / ".githooks"))
    repository.setup(repository.root / subdirectory)
    assert repository.git("config", "--get", "core.hooksPath").stdout.strip() == ".githooks"
    assert repository.git("config", "--get", "core.hooksPath", cwd=repository.linked).stdout.strip() == ".githooks"
    repository.assert_hook_blocks_commit()
