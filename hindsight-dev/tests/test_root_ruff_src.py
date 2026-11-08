"""The root ruff.toml must list every Python package as an isort source root.

Codacy runs `ruff check --config <root config> <files>` from the repository root, and the root
pyproject.toml extends ruff.toml, so that config is ruff.toml. isort decides
what is first-party from the config's `src` roots, so a package missing from that list has its
own modules (hindsight_api, tests, ...) treated as third-party there. An import block that
`scripts/hooks/lint.sh` sorted correctly from the package's own directory is then reported as
I001 on every pull request that touches it, and the Codacy gate allows zero new issues.

A new package directory gets added without anyone thinking of ruff.toml, so assert over every
pyproject.toml below the root instead of trusting the list to be kept up to date by hand.
"""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Package roots deliberately left out of `src`, with the reason. The integrations import
# hindsight_client as a published dependency and sort it with their third-party block, so
# making it first-party from the root would report all of those blocks as I001.
NOT_SOURCE_ROOTS = {"hindsight-clients/python"}

# Directories that must never become source roots themselves: their children are named
# after the frameworks they wrap (hindsight-integrations/litellm, .../crewai), so a root
# here would turn `import litellm` into a first-party import.
CONTAINER_DIRS = {"hindsight-integrations", "hindsight-clients", "hindsight-extensions"}


def _src_entries() -> list[str]:
    return tomllib.loads((REPO_ROOT / "ruff.toml").read_text())["src"]


def _expand(entry: str) -> set[Path]:
    """The directories one `src` entry names when ruff resolves it from the repository root."""
    # Plain entries are joined rather than globbed: on Python 3.11, `Path.glob(".")` raises
    # IndexError on the empty pattern it reduces to.
    if not any(char in entry for char in "*?["):
        candidate = REPO_ROOT / entry
        return {candidate} if candidate.is_dir() else set()
    return {path for path in REPO_ROOT.glob(entry) if path.is_dir()}


def _src_roots() -> set[Path]:
    """The directories ruff expands every root `src` entry to."""
    return {root for entry in _src_entries() for root in _expand(entry)}


def _package_roots() -> set[str]:
    """Every directory below the root holding a pyproject.toml, relative to the root."""
    # Packages sit at the top level or one container deep (hindsight-integrations/<name>);
    # depth 3 is headroom. Fixed-depth globs instead of `git ls-files`: Codacy's Bandit
    # reports every subprocess import and call (B404, B603), and its gate allows no new
    # issue. Dot directories (.venv, .claude/worktrees) and node_modules are not packages.
    roots: set[str] = set()
    for pattern in ("*/pyproject.toml", "*/*/pyproject.toml", "*/*/*/pyproject.toml"):
        for path in REPO_ROOT.glob(pattern):
            parts = path.relative_to(REPO_ROOT).parent.parts
            if not any(part.startswith(".") or part == "node_modules" for part in parts):
                roots.add("/".join(parts))
    return roots


def _relative(roots: set[Path]) -> set[str]:
    return {str(root.relative_to(REPO_ROOT)) for root in roots}


def test_every_package_is_a_source_root() -> None:
    packages = _package_roots()
    assert packages, "no pyproject.toml found below the repository root"
    missing = sorted(packages - _relative(_src_roots()) - NOT_SOURCE_ROOTS)
    assert not missing, (
        "These packages are not in the root ruff.toml `src`, so Codacy reports their own "
        f"imports as third-party (I001): {missing}. Add each directory to `src`."
    )


def test_left_out_packages_stay_out() -> None:
    included = sorted(_relative(_src_roots()) & NOT_SOURCE_ROOTS)
    assert not included, (
        f"{included} is a source root in ruff.toml, but the integrations sort its module with "
        "their third-party imports. Remove it from `src`, or move it out of NOT_SOURCE_ROOTS "
        "together with re-sorting the integrations."
    )


def test_container_directories_are_not_source_roots() -> None:
    containers = sorted(_relative(_src_roots()) & CONTAINER_DIRS)
    assert not containers, (
        f"{containers} became a source root (a `hindsight-*` glob does this). Its children are "
        "named after third-party frameworks, so their imports would turn first-party. "
        "List the package directories instead."
    )


def test_every_src_entry_matches_a_directory() -> None:
    # "src" is ruff's default second entry, kept for runs that pass `--config` from a package
    # with a src layout; there is no src/ at the root.
    stale = sorted(entry for entry in _src_entries() if entry != "src" and not _expand(entry))
    assert not stale, f"ruff.toml `src` names directories that do not exist: {stale}"


def test_root_pyproject_extends_ruff_toml() -> None:
    # Codacy's runner takes the first root config it lists (pyproject.toml, ruff.toml or
    # .ruff.toml) as `--config`. Measured on Codacy, that was not ruff.toml: its I001 on
    # hindsight_api/admin/cli.py follows ruff's default line length of 88, and it put
    # tomllib outside the standard library. Extending ruff.toml, and setting nothing
    # else here, keeps one source for every root entry point.
    ruff_table = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text()).get("tool", {}).get("ruff")
    assert ruff_table == {"extend": "ruff.toml"}, (
        f"The root pyproject.toml [tool.ruff] is {ruff_table!r}. It must be exactly "
        '`extend = "ruff.toml"`, or Codacy can lint with ruff defaults instead of ruff.toml.'
    )
