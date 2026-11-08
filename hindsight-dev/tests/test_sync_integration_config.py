"""Integration packages must carry identical copies of the shared user config helper.

Because coding-agent integration packages are released to PyPI independently, they cannot
depend on an internal shared package. Instead, `scripts/sync-integration-config.py` copies
`hindsight-integrations/_shared/user_config.py` into each integration package with a
generated header. These tests verify the synchronisation logic, drift detection across
line endings and content edits, wheel inclusion, and caller wiring.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync-integration-config.py"


def _load_sync_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_integration_config", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
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


def test_every_target_ships_in_wheel():
    """Each target copy's parent directory must be declared in hatchling wheel packages."""
    for target in sync_mod.TARGETS:
        pyproject_path = REPO_ROOT / target.parent.parent / "pyproject.toml"
        assert pyproject_path.is_file(), f"missing pyproject.toml at {pyproject_path}"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        wheel_packages = (
            data.get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
            .get("packages", [])
        )
        assert target.parent.name in wheel_packages, (
            f"target parent dir {target.parent.name!r} not in {pyproject_path} wheel packages: {wheel_packages}"
        )


def test_every_config_imports_user_config():
    """Each package config.py next to a target must import from ._user_config."""
    for target in sync_mod.TARGETS:
        config_file = REPO_ROOT / target.parent / "config.py"
        assert config_file.is_file(), f"missing config file {config_file}"
        text = config_file.read_text(encoding="utf-8")
        assert "._user_config" in text, f"{config_file} does not import from ._user_config"
