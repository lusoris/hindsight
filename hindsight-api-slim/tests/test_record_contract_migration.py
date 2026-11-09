"""Tests for the record contract Alembic migration (d8f3a1b2c4e5).

The PG paths are checked as Alembic renders them in offline (``--sql``) mode, so the
assertions see the exact statements a tenant migration sends, schema quoting
included, and once for real: upgrade, downgrade and upgrade again in a tenant schema
of a database of the test's own. The Oracle upgrade goes through ``exec_driver_sql``,
which offline mode cannot render, so a fake bind records each call instead; the
statements themselves run against Oracle 23ai in the ``test-python-client-oracle``
CI job, which migrates a fresh database on every API change.
"""

from __future__ import annotations

import importlib.util
import io
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "hindsight_api" / "alembic"
_MIGRATION_PATH = _ALEMBIC_DIR / "versions" / "d8f3a1b2c4e5_add_record_contract_fields.py"
_REVISION = "d8f3a1b2c4e5"
_PRE_REVISION = "c7e2f9a4d1b8"

_TABLES = ("memory_units", "invalidated_memory_units")
_COLUMNS = (
    "provenance_class",
    "write_authority",
    "observed_at",
    "fetched_at",
    "ttl_seconds",
    "scope",
    "reset_condition",
    "record_status",
)
_INDEXES = (
    ("ix_memory_units_record_status", "(bank_id, record_status)"),
    ("ix_memory_units_provenance_class", "(bank_id, provenance_class)"),
)
_PG_CHECKS = (
    "CHECK (provenance_class IN ('tool-run', 'file-read', 'recalled', 'inferred'))",
    "CHECK (record_status IN ('active', 'stale', 'disputed', 'tombstoned'))",
)

# A schema name with a double quote in it proves the name is quoted and escaped,
# not pasted in; None is the default search_path (no qualifier).
_SCHEMAS = pytest.mark.parametrize(
    ("schema", "qualifier"),
    [('te"st', '"te""st".'), (None, "")],
    ids=["tenant-schema-with-quote", "default-search-path"],
)


@dataclass(frozen=True)
class _DriverCall:
    """One ``exec_driver_sql`` call: the statement and its bind parameters."""

    sql: str
    params: dict[str, str]


@dataclass(frozen=True)
class _ContractState:
    """What the migration owns in one PG schema: its columns, indexes and CHECK constraints."""

    columns: frozenset[tuple[str, str]]
    indexes: frozenset[str]
    checks: int


_FULL_STATE = _ContractState(
    columns=frozenset((table, column) for table in _TABLES for column in _COLUMNS),
    indexes=frozenset(name for name, _columns in _INDEXES),
    checks=2 * len(_TABLES),
)
_EMPTY_STATE = _ContractState(columns=frozenset(), indexes=frozenset(), checks=0)


def _load_migration() -> ModuleType:
    """Import the migration file by path; alembic version files are not a package."""
    spec = importlib.util.spec_from_file_location("migration_d8f3a1b2c4e5", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _set_target_schema(monkeypatch: pytest.MonkeyPatch, migration: ModuleType, schema: str | None) -> None:
    """Point the migration's alembic ``context.config`` at ``schema``; undone after the test."""
    # Outside a running migration alembic's ``context`` proxy has no ``config``, and a plain
    # assignment would leak into every later test in this worker, so go through monkeypatch.
    config = SimpleNamespace(get_main_option=lambda _option: schema)
    monkeypatch.setattr(migration.context, "config", config, raising=False)


def _render_pg(migration: ModuleType, direction: str) -> list[str]:
    """Statements ``upgrade``/``downgrade`` emits for PostgreSQL in offline mode, terminator stripped."""
    buffer = io.StringIO()
    migration_context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(migration_context):
        getattr(migration, direction)()
    return [statement.strip() for statement in buffer.getvalue().split(";") if statement.strip()]


@_SCHEMAS
def test_pg_upgrade_sql(monkeypatch: pytest.MonkeyPatch, schema: str | None, qualifier: str) -> None:
    """PG upgrade adds every contract column in one ALTER per table, each CHECK once, then both indexes."""
    migration = _load_migration()
    _set_target_schema(monkeypatch, migration, schema)

    statements = _render_pg(migration, "upgrade")

    assert [statement.splitlines()[0] for statement in statements] == [
        f"ALTER TABLE {qualifier}memory_units",
        f"ALTER TABLE {qualifier}invalidated_memory_units",
        *(f"CREATE INDEX IF NOT EXISTS {name} ON {qualifier}memory_units {columns}" for name, columns in _INDEXES),
    ]
    # All eight columns in one ALTER per table, so PG scans each table once for the CHECKs.
    for alter in statements[:2]:
        assert re.findall(r"ADD COLUMN IF NOT EXISTS (\w+) ", alter) == list(_COLUMNS)
        for check in _PG_CHECKS:
            assert alter.count(check) == 1


@_SCHEMAS
def test_pg_downgrade_sql(monkeypatch: pytest.MonkeyPatch, schema: str | None, qualifier: str) -> None:
    """PostgreSQL downgrade drops composite indexes in reverse order and drops all contract columns from both tables."""
    migration = _load_migration()
    _set_target_schema(monkeypatch, migration, schema)

    statements = _render_pg(migration, "downgrade")

    assert [statement.splitlines()[0] for statement in statements] == [
        *(f"DROP INDEX IF EXISTS {qualifier}{name}" for name, _columns in reversed(_INDEXES)),
        f"ALTER TABLE {qualifier}memory_units",
        f"ALTER TABLE {qualifier}invalidated_memory_units",
    ]
    for alter in statements[2:]:
        dropped = [line.strip().rstrip(",") for line in alter.splitlines()[1:]]
        assert sorted(dropped) == sorted(f"DROP COLUMN IF EXISTS {column}" for column in _COLUMNS)


def test_oracle_upgrade_guards_every_add(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oracle upgrade executes all column and index additions inside existence guards with distinct constraints."""
    migration = _load_migration()
    calls: list[_DriverCall] = []

    class FakeBind:
        def exec_driver_sql(self, sql: str, params: dict[str, str]) -> None:
            calls.append(_DriverCall(sql=sql, params=params))

    monkeypatch.setattr(migration.op, "get_bind", FakeBind)

    migration._oracle_upgrade()

    column_calls = [call for call in calls if call.sql is migration._ORACLE_COLUMN_GUARD]
    index_calls = [call for call in calls if call.sql is migration._ORACLE_INDEX_GUARD]
    # Every statement runs inside a guard; nothing is sent bare.
    assert len(calls) == len(column_calls) + len(index_calls) == len(_TABLES) * len(_COLUMNS) + len(_INDEXES)

    targets = [(call.params["table_name"], call.params["column_name"]) for call in column_calls]
    assert sorted(targets) == sorted((table.upper(), column.upper()) for table in _TABLES for column in _COLUMNS)
    for call in column_calls:
        table, column = call.params["table_name"].lower(), call.params["column_name"].lower()
        assert call.params["ddl"].startswith(f"ALTER TABLE {table} ADD ({column} ")
    # Oracle constraint names are unique per schema, so the two tables need distinct ones.
    constraint_names = re.findall(r"CONSTRAINT (\w+)", " ".join(call.params["ddl"] for call in column_calls))
    assert len(constraint_names) == len(set(constraint_names)) == 2 * len(_TABLES)

    assert [(call.params["index_name"], call.params["ddl"]) for call in index_calls] == [
        (name.upper(), f"CREATE INDEX {name} ON memory_units {columns}") for name, columns in _INDEXES
    ]


def test_oracle_guards_skip_existing_objects() -> None:
    """Both Oracle guards look the object up in the data dictionary and run the DDL only when it is absent."""
    migration = _load_migration()

    column_guard = " ".join(migration._ORACLE_COLUMN_GUARD.split())
    index_guard = " ".join(migration._ORACLE_INDEX_GUARD.split())

    assert "FROM user_tab_columns WHERE table_name = :table_name AND column_name = :column_name;" in column_guard
    assert "FROM user_indexes WHERE index_name = :index_name;" in index_guard
    for guard in (column_guard, index_guard):
        assert "IF n = 0 THEN EXECUTE IMMEDIATE :ddl; END IF;" in guard


def _alembic_cfg(db_url: str, schema: str) -> Config:
    """Alembic config that migrates ``schema`` of ``db_url``, as a tenant migration does."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("prepend_sys_path", ".")
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("target_schema", schema)
    return cfg


def _contract_state(db_url: str, schema: str) -> _ContractState:
    """Read the migration's columns, indexes and CHECK constraints in ``schema`` from the catalog."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            columns = conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = ANY(:tables) AND column_name = ANY(:columns)"
                ),
                {"schema": schema, "tables": list(_TABLES), "columns": list(_COLUMNS)},
            ).fetchall()
            indexes = conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND indexname = ANY(:names)"),
                {"schema": schema, "names": [name for name, _columns in _INDEXES]},
            ).scalars()
            checks = conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = :schema AND t.relname = ANY(:tables) AND c.contype = 'c' "
                    "AND pg_get_constraintdef(c.oid) ~ '(provenance_class|record_status)'"
                ),
                {"schema": schema, "tables": list(_TABLES)},
            ).scalar_one()
            return _ContractState(
                columns=frozenset((row.table_name, row.column_name) for row in columns),
                indexes=frozenset(indexes),
                checks=checks,
            )
    finally:
        engine.dispose()


def test_pg_upgrade_downgrade_roundtrip_in_tenant_schema(fresh_db_url: str) -> None:
    """On real PG, in a tenant schema: the upgrade adds everything, the downgrade removes it, and
    the upgrade runs again cleanly afterwards."""
    schema = "tenant_record_contract"
    cfg = _alembic_cfg(fresh_db_url, schema)

    command.upgrade(cfg, _PRE_REVISION)
    assert _contract_state(fresh_db_url, schema) == _EMPTY_STATE

    command.upgrade(cfg, _REVISION)
    assert _contract_state(fresh_db_url, schema) == _FULL_STATE

    command.downgrade(cfg, _PRE_REVISION)
    assert _contract_state(fresh_db_url, schema) == _EMPTY_STATE

    command.upgrade(cfg, _REVISION)
    assert _contract_state(fresh_db_url, schema) == _FULL_STATE
