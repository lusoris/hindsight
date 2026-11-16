"""No test in this suite may patch an attribute of a standard-library module.

Every test shares the process with the session's provider-stub thread (uvicorn, started by
``hindsight_system_tests.server``), which calls into the standard library on its own event
loop. ``monkeypatch.setattr(waiting.asyncio, "sleep", mock)`` replaced ``asyncio.sleep`` for
that thread as well: its 0.1 s tick became a busy loop on the test's mock, and
``assert_not_awaited`` counted thousands of awaits that the code under test never made.
Patch the name the code under test looks up instead, e.g.
``monkeypatch.setattr(waiting, "asyncio", SimpleNamespace(sleep=mock))``.

The scan is static and imports nothing: it expands each patch target through the imports of
its file and fails when a segment of the patched object's dotted path names a standard-library
module (``hindsight_system_tests.waiting.asyncio``, ``time``, ``asyncio.Event``). A target rooted
in a local object (a fixture value, a ``SimpleNamespace``) is not checked. The first version
imported each target and tested for a module object; Codacy's Opengrep rule
``non-literal-import`` rejects a computed ``importlib.import_module``, and the path check needs
no import.
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


@dataclass(frozen=True)
class StdlibPatch:
    path: Path
    line: int
    target: str


def _imported_names(tree: ast.Module) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name.split(".")
                else:
                    root = alias.name.split(".")[0]
                    names[root] = [root]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = [*node.module.split("."), alias.name]
    return names


def _chain(expr: ast.expr) -> list[str] | None:
    if isinstance(expr, ast.Name):
        return [expr.id]
    if isinstance(expr, ast.Attribute):
        head = _chain(expr.value)
        return None if head is None else [*head, expr.attr]
    return None


def _patched_path(call: ast.Call, imported: dict[str, list[str]]) -> list[str] | None:
    """Dotted path of the object a patch call mutates; None when it is no patch call or local."""
    func = _chain(call.func) or []
    if not call.args or not func:
        return None
    is_setattr = func[-1] == "setattr"
    is_patch = func[-1] == "patch"
    is_patch_object = func[-2:] == ["patch", "object"]
    if not (is_setattr or is_patch or is_patch_object):
        return None
    target = call.args[0]
    # String form: monkeypatch.setattr("time.sleep", f) and mock.patch("asyncio.sleep") name
    # the attribute last; the object patched is everything before it.
    if (is_setattr and len(call.args) == 2) or is_patch:
        if isinstance(target, ast.Constant) and isinstance(target.value, str) and "." in target.value:
            return target.value.split(".")[:-1]
        return None
    chain = _chain(target)
    if chain is None or chain[0] not in imported:
        return None
    return [*imported[chain[0]], *chain[1:]]


def find_stdlib_patches(path: Path) -> list[StdlibPatch]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imported = _imported_names(tree)
    found: list[StdlibPatch] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _patched_path(node, imported)
        if dotted and any(segment in sys.stdlib_module_names for segment in dotted):
            found.append(StdlibPatch(path=path, line=node.lineno, target=".".join(dotted)))
    return found


def test_no_test_patches_a_stdlib_module() -> None:
    found = [patch for path in sorted(TESTS.rglob("*.py")) for patch in find_stdlib_patches(path)]
    assert found == [], "patch the code under test's own binding instead (see module docstring): " + ", ".join(
        f"{patch.path.relative_to(TESTS)}:{patch.line} patches {patch.target}" for patch in found
    )


_HEADER = "import asyncio\nimport time\nfrom unittest import mock\nfrom unittest.mock import patch\n"
_HEADER += "from hindsight_system_tests import waiting\n"


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setattr(waiting.asyncio, "sleep", sleep)',  # the fixture form that broke PR 58's CI
        'monkeypatch.setattr(asyncio, "sleep", sleep)',
        'monkeypatch.setattr(asyncio.Event, "wait", sleep)',
        'monkeypatch.setattr("time.sleep", lambda _: None)',
        'monkeypatch.setattr("hindsight_system_tests.waiting.asyncio.sleep", sleep)',
        'setattr(time, "monotonic", clock)',
        'patch("asyncio.sleep")',
        'mock.patch.object(waiting.time, "monotonic")',
    ],
)
def test_planted_stdlib_patch_is_found(tmp_path: Path, line: str) -> None:
    source = tmp_path / "test_planted.py"
    source.write_text(f"{_HEADER}\n\ndef test_x(monkeypatch, sleep, clock):\n    {line}\n")
    assert [patch.line for patch in find_stdlib_patches(source)] == [9]


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setattr(waiting, "asyncio", SimpleNamespace(sleep=sleep))',
        'monkeypatch.setattr(waiting, "time", SimpleNamespace(monotonic=clock))',
        'monkeypatch.setattr("hindsight_system_tests.waiting.asyncio", SimpleNamespace(sleep=sleep))',
        'monkeypatch.setattr(client.operations, "list_operations", sleep)',
        'patch.object(waiting, "wait_until_settled")',
    ],
)
def test_patching_the_code_under_test_binding_passes(tmp_path: Path, line: str) -> None:
    source = tmp_path / "test_planted.py"
    source.write_text(f"{_HEADER}\n\ndef test_x(monkeypatch, sleep, clock, client):\n    {line}\n")
    assert find_stdlib_patches(source) == []
