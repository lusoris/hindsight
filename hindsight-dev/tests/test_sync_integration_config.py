"""Integration packages must carry identical copies of the shared user config helper.

Because coding-agent integration packages are released to PyPI independently, they cannot
depend on an internal shared package. Instead, `scripts/sync-integration-config.py` copies
`hindsight-integrations/_shared/user_config.py` into each integration package with a
generated header. These tests verify the synchronisation logic, drift detection across
line endings and content edits, wheel inclusion, and caller wiring.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync-integration-config.py"


def _load_sync_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_integration_config", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves the module through sys.modules, so register it before running it.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sync_mod = _load_sync_module()


def test_real_repo_in_sync():
    """The real repo targets must match the shared source."""
    assert sync_mod.main(["--check"]) == 0


def test_tmp_tree_lifecycle(tmp_path: Path, capsys):
    """In a fresh tree holding only source, check fails on all targets, write succeeds, then check passes."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")

    # --check returns 1 and stderr names all four targets
    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    for target in sync_mod.TARGETS:
        assert str(target) in captured.err

    # write mode returns 0 and creates them
    assert sync_mod.main(["--root", str(tmp_path)]) == 0
    for target in sync_mod.TARGETS:
        assert (tmp_path / target).is_file()

    # --check then returns 0
    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 0


def test_planted_drift_detected(tmp_path: Path, capsys):
    """Drift in one target copy is caught and restored without touching other targets."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")
    assert sync_mod.main(["--root", str(tmp_path)]) == 0
    capsys.readouterr()

    openhands_target = Path("hindsight-integrations/openhands/hindsight_openhands/_user_config.py")
    with open(tmp_path / openhands_target, "a", encoding="utf-8") as f:
        f.write("x = 1\n")

    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert str(openhands_target) in captured.err
    for target in sync_mod.TARGETS:
        if target != openhands_target:
            assert str(target) not in captured.err

    # write mode restores it
    assert sync_mod.main(["--root", str(tmp_path)]) == 0
    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 0


def test_crlf_drift_detected(tmp_path: Path):
    """Line-ending drift (CRLF) in a target fails check mode."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")
    assert sync_mod.main(["--root", str(tmp_path)]) == 0

    aider_target = Path("hindsight-integrations/aider/hindsight_aider/_user_config.py")
    target_path = tmp_path / aider_target
    with open(target_path, "r", encoding="utf-8", newline="") as f:
        content = f.read()
    crlf_content = content.replace("\n", "\r\n")
    with open(target_path, "w", encoding="utf-8", newline="") as f:
        f.write(crlf_content)

    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 1


def test_source_edit_fails_check(tmp_path: Path, capsys):
    """Editing the source file makes all target copies out of date."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")
    assert sync_mod.main(["--root", str(tmp_path)]) == 0
    capsys.readouterr()

    # Change one line of the tmp source
    content = tmp_src.read_text(encoding="utf-8")
    tmp_src.write_text(content + "\n# source edit\n", encoding="utf-8")

    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    for target in sync_mod.TARGETS:
        assert str(target) in captured.err


def test_missing_source(tmp_path: Path):
    """When the shared source is missing, main returns exit code 2."""
    assert sync_mod.main(["--check", "--root", str(tmp_path)]) == 2


def test_interrupted_write_cleans_up_and_preserves_target(tmp_path: Path, monkeypatch):
    """An interrupted write leaves target byte-for-byte unchanged and cleans up temp files."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")
    assert sync_mod.main(["--root", str(tmp_path)]) == 0

    target = sync_mod.TARGETS[0]
    target_path = tmp_path / target
    original_bytes = target_path.read_bytes()

    # Modify source so a write would be triggered
    tmp_src.write_text(tmp_src.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")

    def failing_replace(src, dst):
        raise OSError("simulated write failure during replace")

    monkeypatch.setattr(sync_mod.os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated write failure"):
        sync_mod.main(["--root", str(tmp_path)])

    assert target_path.read_bytes() == original_bytes
    temp_files = [p for p in target_path.parent.iterdir() if p != target_path]
    assert temp_files == []


def test_write_keeps_target_mode(tmp_path: Path):
    """A rewritten target keeps its mode and a new one gets 0666 minus the umask, not 0600.

    The atomic write goes through NamedTemporaryFile, which creates its file 0600.
    """
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")

    previous_umask = os.umask(0o022)
    try:
        assert sync_mod.main(["--root", str(tmp_path)]) == 0
    finally:
        os.umask(previous_umask)
    for target in sync_mod.TARGETS:
        assert stat.S_IMODE((tmp_path / target).stat().st_mode) == 0o644

    kept = tmp_path / sync_mod.TARGETS[0]
    kept.chmod(0o640)
    tmp_src.write_text(tmp_src.read_text(encoding="utf-8") + "\n# change\n", encoding="utf-8")
    assert sync_mod.main(["--root", str(tmp_path)]) == 0
    assert stat.S_IMODE(kept.stat().st_mode) == 0o640


def test_concurrent_run_older_source_does_not_win(tmp_path: Path, monkeypatch):
    """When source changes during first render pass, sync detects drift and applies newest source."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    initial_text = (REPO_ROOT / src_rel).read_text(encoding="utf-8")
    tmp_src.write_text(initial_text, encoding="utf-8")

    new_source_text = initial_text + "\n# updated concurrent source\n"
    real_render = sync_mod.render
    first_call = True

    def racing_render(text: str) -> str:
        nonlocal first_call
        if first_call:
            first_call = False
            tmp_src.write_text(new_source_text, encoding="utf-8")
        return real_render(text)

    monkeypatch.setattr(sync_mod, "render", racing_render)

    assert sync_mod.main(["--root", str(tmp_path)]) == 0
    expected_content = real_render(new_source_text)
    for target in sync_mod.TARGETS:
        target_path = tmp_path / target
        assert target_path.read_text(encoding="utf-8") == expected_content


def test_source_that_never_settles_fails(tmp_path: Path, monkeypatch, capsys):
    """If source continues changing on every pass, main returns 1 and stderr names the rerun."""
    src_rel = sync_mod.SOURCE
    tmp_src = tmp_path / src_rel
    tmp_src.parent.mkdir(parents=True, exist_ok=True)
    tmp_src.write_text((REPO_ROOT / src_rel).read_text(encoding="utf-8"), encoding="utf-8")

    counter = 0
    real_render = sync_mod.render

    def mutating_render(text: str) -> str:
        nonlocal counter
        counter += 1
        tmp_src.write_text(f"# constantly mutating source pass {counter}\n", encoding="utf-8")
        return real_render(text)

    monkeypatch.setattr(sync_mod, "render", mutating_render)

    assert sync_mod.main(["--root", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "rerun scripts/sync-integration-config.py" in captured.err


def test_every_target_ships_in_wheel(tmp_path: Path):
    """Each target package's built wheel must contain its _user_config.py copy."""
    uv_bin = shutil.which("uv")
    assert uv_bin is not None, "uv executable not found on PATH"

    for target in sync_mod.TARGETS:
        package_dir = REPO_ROOT / target.parent.parent
        out_dir = tmp_path / target.parent.parent.name
        # check=False so a failed build shows uv's stderr instead of a bare CalledProcessError.
        build = subprocess.run(
            [uv_bin, "build", "--wheel", "--out-dir", str(out_dir), str(package_dir)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert build.returncode == 0, f"uv build {package_dir} failed:\n{build.stderr}"
        wheels = list(out_dir.glob("*.whl"))
        assert len(wheels) == 1, f"expected 1 wheel in {out_dir}, found {len(wheels)}"
        with zipfile.ZipFile(wheels[0]) as zf:
            assert f"{target.parent.name}/_user_config.py" in zf.namelist()


def test_every_config_imports_user_config():
    """Each package config.py next to a target must import from ._user_config."""
    for target in sync_mod.TARGETS:
        config_file = REPO_ROOT / target.parent / "config.py"
        assert config_file.is_file(), f"missing config file {config_file}"
        text = config_file.read_text(encoding="utf-8")
        assert "._user_config" in text, f"{config_file} does not import from ._user_config"
