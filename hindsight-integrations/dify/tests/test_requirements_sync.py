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

# name, extras, specifier, marker, url: every field a declaration can carry.
type RequirementKey = tuple[str, tuple[str, ...], str, str, str]


def _normalized(reqs: list[Requirement]) -> list[RequirementKey]:
    """Sorted list, not a dict, so a duplicate declaration stays visible."""
    return sorted(
        (
            canonicalize_name(r.name),
            tuple(sorted(r.extras)),
            str(r.specifier),
            str(r.marker) if r.marker else "",
            r.url or "",
        )
        for r in reqs
    )


def _requirements_txt() -> list[RequirementKey]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    reqs = [Requirement(line) for line in (raw.split("#", 1)[0].strip() for raw in lines) if line]
    return _normalized(reqs)


def _pyproject() -> list[RequirementKey]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return _normalized([Requirement(dep) for dep in deps])


def test_requirements_txt_matches_pyproject() -> None:
    assert _requirements_txt() == _pyproject()
