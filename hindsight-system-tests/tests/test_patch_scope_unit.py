"""No test in this suite may patch an attribute of a standard-library module.

Every test shares the process with the session's provider-stub thread (uvicorn, started by
``hindsight_system_tests.server``), which calls into the standard library on its own event
loop. ``monkeypatch.setattr(waiting.asyncio, "sleep", mock)`` replaced ``asyncio.sleep`` for
that thread as well: its 0.1 s tick became a busy loop on the test's mock, and
``assert_not_awaited`` counted thousands of awaits that the code under test never made.
Patch the name the code under test looks up instead, e.g.
``monkeypatch.setattr(waiting, "asyncio", SimpleNamespace(sleep=mock))``.

The scan is static and imports nothing: it expands each patch target through the import
bindings visible at the call and fails when a segment of the patched object's dotted path
names a standard-library module (``hindsight_system_tests.waiting.asyncio``, ``time``,
``asyncio.Event``). A target rooted in a local object (a fixture value, a ``SimpleNamespace``)
is not checked. The first version imported each target and tested for a module object;
Codacy's Opengrep rule ``non-literal-import`` rejects a computed ``importlib.import_module``,
and the path check needs no import. The second version read one file-wide import map and only
positional targets of ``setattr``/``patch``/``patch.object`` called by those names: a keyword
``target=``, an aliased ``patch`` import and ``patch.multiple`` slipped through, and a test-local
``time = SimpleNamespace(...)`` was flagged as the ``time`` module. Bindings are now resolved per
scope, the way Python resolves the name at that call. The third version walked a scope's nodes
in reverse source order and kept the last binding it met, so ``asyncio = object(); import
asyncio`` hid the import. When one scope binds a name more than once, the scan now fails closed:
an import beats any other binding and a standard-library import beats any other import, wherever
each sits, so ``import asyncio; asyncio = object()`` is flagged as well.
"""

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pytest

TESTS = Path(__file__).parent

# A name's binding in one scope: the dotted path it was imported from, or None when the scope
# binds it to something else (an assignment, a parameter, a def) and the import is shadowed.
Bindings = dict[str, list[str] | None]

_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_COMPREHENSION = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPE = (*_FUNCTION, ast.ClassDef, *_COMPREHENSION)


@dataclass(frozen=True)
class StdlibPatch:
    path: Path
    line: int
    target: str


class _Patcher(Enum):
    ATTRIBUTE = "attribute"  # setattr/delattr: "pkg.mod.attr" string or (object, name)
    PATCH = "patch"  # mock.patch: "pkg.mod.attr" string only
    OBJECT = "object"  # mock.patch.object: (object, name) only
    MULTIPLE = "multiple"  # mock.patch.multiple: "pkg.mod" string or object, attributes as kwargs


def _inner_parts(scope: ast.AST) -> list[ast.AST]:
    """Children a scope evaluates in its own namespace."""
    if isinstance(scope, ast.Lambda):
        return [scope.body]
    if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return list(scope.body)
    if isinstance(scope, _COMPREHENSION):
        first, *rest = scope.generators
        results = [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
        return [*results, first.target, *first.ifs, *rest]
    raise TypeError(f"not a scope: {type(scope).__name__}")


def _outer_parts(scope: ast.AST) -> list[ast.AST]:
    """Children of a nested scope that its enclosing scope evaluates: decorators, defaults, bases."""
    if isinstance(scope, _FUNCTION):
        defaults = [*scope.args.defaults, *(d for d in scope.args.kw_defaults if d is not None)]
        decorators = [] if isinstance(scope, ast.Lambda) else scope.decorator_list
        return [*decorators, *defaults]
    if isinstance(scope, ast.ClassDef):
        return [*scope.decorator_list, *scope.bases, *scope.keywords]
    if isinstance(scope, _COMPREHENSION):
        return [scope.generators[0].iter]
    raise TypeError(f"not a nested scope: {type(scope).__name__}")


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Every node a scope evaluates itself, in source order; a nested scope is yielded but not entered."""
    stack = _inner_parts(scope)[::-1]
    while stack:
        node = stack.pop()
        yield node
        children = _outer_parts(node) if isinstance(node, _SCOPE) else list(ast.iter_child_nodes(node))
        stack.extend(reversed(children))


def _bound_by(node: ast.AST) -> Bindings:
    """Names one statement or expression binds in the scope that evaluates it."""
    if isinstance(node, ast.Import):
        return {
            alias.asname or alias.name.split(".")[0]: alias.name.split(".")
            if alias.asname
            else [alias.name.split(".")[0]]
            for alias in node.names
        }
    if isinstance(node, ast.ImportFrom):
        # A relative import binds a project-local module; it shadows like any local name.
        absolute = node.module is not None and node.level == 0
        return {
            alias.asname or alias.name: [*node.module.split("."), alias.name] if absolute and node.module else None
            for alias in node.names
        }
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return {node.id: None}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name: None}
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name: None}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest: None}
    return {}


def _names_stdlib(path: list[str]) -> bool:
    return any(segment in sys.stdlib_module_names for segment in path)


def _stronger(current: list[str] | None, new: list[str] | None) -> list[str] | None:
    """The binding to keep when one scope binds a name twice.

    Which one holds at the patch call depends on control flow the scan does not follow
    (`if`/`else`, `try`, loops), so it fails closed: an import beats any other binding and a
    standard-library import beats any other import, wherever each sits in the scope.
    """
    if new is None:
        return current
    if current is None or not _names_stdlib(current):
        return new
    return current


def _scope_bindings(scope: ast.AST, outer: Bindings, module: Bindings | None) -> Bindings:
    nodes = list(_own_nodes(scope))
    globals_ = {name for node in nodes if isinstance(node, ast.Global) for name in node.names}
    nonlocals = {name for node in nodes if isinstance(node, ast.Nonlocal) for name in node.names}
    # Any binding in a scope makes the name local for the whole scope, so the scope's own
    # bindings replace the outer ones; among themselves they merge through _stronger.
    local: Bindings = {}
    if isinstance(scope, _FUNCTION):
        args = scope.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None:
                local[arg.arg] = None
    for node in nodes:
        for name, binding in _bound_by(node).items():
            if name not in globals_ and name not in nonlocals:
                local[name] = _stronger(local[name], binding) if name in local else binding
    names = {**outer, **local}
    if module is not None:
        for name in globals_:
            names[name] = module.get(name)
    return names


def _chain(expr: ast.expr) -> list[str] | None:
    if isinstance(expr, ast.Name):
        return [expr.id]
    if isinstance(expr, ast.Attribute):
        head = _chain(expr.value)
        return None if head is None else [*head, expr.attr]
    return None


def _expand(chain: list[str], names: Bindings) -> list[str] | None:
    """Dotted import path of a name chain; None when its root is a local object."""
    root = names.get(chain[0])
    return None if root is None else [*root, *chain[1:]]


def _patcher(call: ast.Call, names: Bindings) -> _Patcher | None:
    chain = _chain(call.func)
    if chain is None:
        return None
    # `from unittest.mock import patch as mock_patch` makes `mock_patch(...)` a patch call;
    # a local root (`monkeypatch.setattr`) keeps its own name.
    func = _expand(chain, names) or chain
    if func[-2:] == ["patch", "object"]:
        return _Patcher.OBJECT
    if func[-2:] == ["patch", "multiple"]:
        return _Patcher.MULTIPLE
    if func[-1] == "patch":
        return _Patcher.PATCH
    if func[-1] in ("setattr", "delattr"):
        return _Patcher.ATTRIBUTE
    return None


def _patched_path(call: ast.Call, names: Bindings) -> list[str] | None:
    """Dotted path of the object a patch call mutates; None when it is no patch call or local."""
    patcher = _patcher(call, names)
    if patcher is None:
        return None
    # monkeypatch.setattr, mock.patch, patch.object and patch.multiple all name it `target`.
    target = call.args[0] if call.args else next((kw.value for kw in call.keywords if kw.arg == "target"), None)
    if target is None:
        return None
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        if patcher is _Patcher.OBJECT:
            return None
        segments = target.value.split(".")
        # "asyncio.sleep" names the attribute last, so the patched object is everything before
        # it; patch.multiple's string names the object itself.
        return segments if patcher is _Patcher.MULTIPLE else segments[:-1] or None
    if patcher is _Patcher.PATCH:
        return None
    chain = _chain(target)
    return None if chain is None else _expand(chain, names)


def _scan(scope: ast.AST, outer: Bindings, module: Bindings | None, path: Path) -> list[StdlibPatch]:
    names = _scope_bindings(scope, outer, module)
    module = names if module is None else module
    # A class body's names are invisible to the functions and comprehensions inside it.
    inner_outer = outer if isinstance(scope, ast.ClassDef) else names
    found: list[StdlibPatch] = []
    for node in _own_nodes(scope):
        if isinstance(node, ast.Call):
            dotted = _patched_path(node, names)
            if dotted and _names_stdlib(dotted):
                found.append(StdlibPatch(path=path, line=node.lineno, target=".".join(dotted)))
        if isinstance(node, _SCOPE):
            found.extend(_scan(node, inner_outer, module, path))
    return found


def find_stdlib_patches(path: Path) -> list[StdlibPatch]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return sorted(_scan(tree, {}, None, path), key=lambda patch: patch.line)


def test_no_test_patches_a_stdlib_module() -> None:
    found = [patch for path in sorted(TESTS.rglob("*.py")) for patch in find_stdlib_patches(path)]
    assert found == [], "patch the code under test's own binding instead (see module docstring): " + ", ".join(
        f"{patch.path.relative_to(TESTS)}:{patch.line} patches {patch.target}" for patch in found
    )


_HEADER = (
    "import asyncio\n"
    "import time\n"
    "from unittest import mock\n"
    "from unittest.mock import patch\n"
    "from unittest.mock import patch as mock_patch\n"
    "from hindsight_system_tests import waiting\n"
)
# The planted statement sits on the line after the header, two blank lines and the def.
_PLANTED_LINE = _HEADER.count("\n") + 4


def _plant(tmp_path: Path, line: str) -> Path:
    source = tmp_path / "test_planted.py"
    source.write_text(f"{_HEADER}\n\ndef test_x(monkeypatch, sleep, clock, client):\n    {line}\n")
    return source


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setattr(waiting.asyncio, "sleep", sleep)',  # the fixture form that broke PR 58's CI
        'monkeypatch.setattr(asyncio, "sleep", sleep)',
        'monkeypatch.setattr(asyncio.Event, "wait", sleep)',
        'monkeypatch.setattr("time.sleep", lambda _: None)',
        'monkeypatch.setattr("hindsight_system_tests.waiting.asyncio.sleep", sleep)',
        'monkeypatch.setattr(target=asyncio, name="sleep", value=sleep)',
        'monkeypatch.delattr(time, "monotonic")',
        'setattr(time, "monotonic", clock)',
        'patch("asyncio.sleep")',
        'patch(target="asyncio.sleep", new=sleep)',
        'mock_patch("asyncio.sleep", sleep)',
        'mock.patch.object(waiting.time, "monotonic")',
        "mock.patch.multiple(asyncio, sleep=sleep)",
        'patch.multiple("asyncio", sleep=sleep)',
        'import socket; monkeypatch.setattr(socket, "socket", sleep)',  # function-local import
        'global time; monkeypatch.setattr(time, "monotonic", clock)',
        # One scope binding a name twice: the import wins wherever it sits (fail closed).
        'asyncio = object(); import asyncio; monkeypatch.setattr(asyncio, "sleep", sleep)',
        'import asyncio; asyncio = object(); monkeypatch.setattr(asyncio, "sleep", sleep)',
        'import pytest as aio; import asyncio as aio; monkeypatch.setattr(aio, "sleep", sleep)',
        'import asyncio as aio; import pytest as aio; monkeypatch.setattr(aio, "sleep", sleep)',
    ],
)
def test_planted_stdlib_patch_is_found(tmp_path: Path, line: str) -> None:
    assert [patch.line for patch in find_stdlib_patches(_plant(tmp_path, line))] == [_PLANTED_LINE]


def test_planted_decorator_patch_is_found(tmp_path: Path) -> None:
    source = tmp_path / "test_planted.py"
    source.write_text(f'{_HEADER}\n\n@patch.object(asyncio, "sleep")\ndef test_x(asyncio):\n    pass\n')
    # The decorator runs in the module scope, where `asyncio` is still the import.
    assert [patch.line for patch in find_stdlib_patches(source)] == [_HEADER.count("\n") + 3]


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setattr(waiting, "asyncio", SimpleNamespace(sleep=sleep))',
        'monkeypatch.setattr(waiting, "time", SimpleNamespace(monotonic=clock))',
        'monkeypatch.setattr("hindsight_system_tests.waiting.asyncio", SimpleNamespace(sleep=sleep))',
        'monkeypatch.setattr(client.operations, "list_operations", sleep)',
        'patch.object(waiting, "wait_until_settled")',
        "patch.multiple(waiting, wait_until_settled=sleep)",
        # A test-local object named like a stdlib module is not that module.
        'time = SimpleNamespace(monotonic=clock); monkeypatch.setattr(time, "monotonic", clock)',
        'client.patch("/v1/default/banks/b", json={})',
    ],
)
def test_patching_the_code_under_test_binding_passes(tmp_path: Path, line: str) -> None:
    assert find_stdlib_patches(_plant(tmp_path, line)) == []
