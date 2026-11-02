"""requirements.txt (the packaged plugin) and pyproject.toml declare the same dependencies.

Dify installs the plugin from requirements.txt, while CI and developers install it
from pyproject.toml. A floor added to one file only (for example a security floor)
leaves the other install path able to resolve the vulnerable version.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent


def _requirements_txt() -> dict[str, str]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    reqs = [Requirement(line) for line in (raw.split("#", 1)[0].strip() for raw in lines) if line]
    return {canonicalize_name(r.name): str(r.specifier) for r in reqs}


def _pyproject() -> dict[str, str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    reqs = [Requirement(dep) for dep in deps]
    return {canonicalize_name(r.name): str(r.specifier) for r in reqs}


def test_requirements_txt_matches_pyproject() -> None:
    assert _requirements_txt() == _pyproject()
