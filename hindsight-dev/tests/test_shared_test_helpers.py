"""The shared integration test helpers fail with explicit raises, never a bare ``assert``.

``hindsight-integrations/_shared/integration_testing`` is imported by path from several
integrations' tests, but it sits outside any ``tests/`` directory. Codacy therefore runs
Bandit on it, and every bare ``assert`` there is a new B101 (high) issue that fails the
Codacy gate. pytest also rewrites asserts only in test modules, so in these helpers an
``assert`` would fail without the compared values, and ``python -O`` would strip it.

The test-dev job's change filter covers ``hindsight-integrations/_shared/**``, so a helper
added or edited there runs this check on its pull request.
"""

import ast
from pathlib import Path

import pytest

HELPERS_DIR = Path(__file__).resolve().parents[2] / "hindsight-integrations" / "_shared" / "integration_testing"


def _helper_modules() -> list[Path]:
    return sorted(HELPERS_DIR.glob("*.py"))


def bare_assert_lines(source: str) -> list[int]:
    """Line numbers of every ``assert`` statement in ``source``."""
    return sorted(node.lineno for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Assert))


def test_helpers_dir_exists() -> None:
    # Guards the parametrised test below against silently collecting nothing after a move.
    assert _helper_modules(), f"no helper modules under {HELPERS_DIR}"


@pytest.mark.parametrize("module", _helper_modules(), ids=lambda p: p.name)
def test_helper_has_no_bare_assert(module: Path) -> None:
    lines = bare_assert_lines(module.read_text(encoding="utf-8"))
    assert not lines, (
        f"{module.relative_to(HELPERS_DIR.parents[2])} uses bare assert on lines {lines}; "
        "raise AssertionError explicitly, as config_contract.py and mcp_e2e.py do"
    )


def test_bare_assert_lines_finds_asserts() -> None:
    # Planted case: the scanner itself must report an assert, including one nested in a method.
    source = "def f(x):\n    assert x\n\nclass C:\n    def m(self):\n        assert self, 'msg'\n"
    assert bare_assert_lines(source) == [2, 6]


def test_bare_assert_lines_ignores_explicit_raise() -> None:
    source = "def f(x):\n    if not x:\n        raise AssertionError('x')\n# assert in a comment\n"
    assert bare_assert_lines(source) == []
