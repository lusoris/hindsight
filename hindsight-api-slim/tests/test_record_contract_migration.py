"""Unit tests for the record contract Alembic migration (d8f3a1b2c4e5)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "hindsight_api"
    / "alembic"
    / "versions"
    / "d8f3a1b2c4e5_add_record_contract_fields.py"
)


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


def test_pg_schema_prefix_quoting(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()

    _set_target_schema(monkeypatch, migration, 'te"st')
    assert migration._pg_schema_prefix() == '"te""st".'

    _set_target_schema(monkeypatch, migration, None)
    assert migration._pg_schema_prefix() == ""


def test_oracle_upgrade_idempotency_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()

    recorded_sql: list[str] = []

    class FakeBind:
        def exec_driver_sql(self, sql: str) -> None:
            recorded_sql.append(sql)

    fake_bind = FakeBind()
    monkeypatch.setattr(migration.op, "get_bind", lambda: fake_bind)

    # Also spy on op.execute to verify no unguarded statement is issued there
    executed_sql: list[str] = []
    monkeypatch.setattr(migration.op, "execute", executed_sql.append)

    migration._oracle_upgrade()

    # Verify no unguarded statements were executed via op.execute
    for sql in executed_sql:
        normalized = sql.strip().upper()
        assert not normalized.startswith("ALTER TABLE"), f"Bare ALTER TABLE found in op.execute: {sql}"
        assert not normalized.startswith("CREATE INDEX"), f"Bare CREATE INDEX found in op.execute: {sql}"

    expected_tables = ["MEMORY_UNITS", "INVALIDATED_MEMORY_UNITS"]
    expected_columns = [
        "PROVENANCE_CLASS",
        "WRITE_AUTHORITY",
        "OBSERVED_AT",
        "FETCHED_AT",
        "TTL_SECONDS",
        "SCOPE",
        "RESET_CONDITION",
        "RECORD_STATUS",
    ]
    expected_indexes = [
        "IX_MEMORY_UNITS_RECORD_STATUS",
        "IX_MEMORY_UNITS_PROVENANCE_CLASS",
    ]

    # Verify column guards
    for table in expected_tables:
        for col in expected_columns:
            matching = [
                stmt
                for stmt in recorded_sql
                if "USER_TAB_COLUMNS" in stmt.upper() and f"'{table}'" in stmt.upper() and f"'{col}'" in stmt.upper()
            ]
            assert len(matching) == 1, (
                f"Expected exactly 1 guard for {table}.{col}, found {len(matching)} in recorded statements"
            )

    # Verify index guards
    for idx in expected_indexes:
        matching = [stmt for stmt in recorded_sql if "USER_INDEXES" in stmt.upper() and f"'{idx}'" in stmt.upper()]
        assert len(matching) == 1, f"Expected exactly 1 guard for index {idx}, found {len(matching)}"

    assert len(recorded_sql) == 16 + 2, f"Expected 18 recorded guarded statements, got {len(recorded_sql)}"

    # Ensure no recorded statement is a bare unguarded ALTER TABLE or CREATE INDEX
    for stmt in recorded_sql:
        normalized = stmt.strip().upper()
        assert not normalized.startswith("ALTER TABLE"), f"Bare ALTER TABLE in recorded_sql: {stmt}"
        assert not normalized.startswith("CREATE INDEX"), f"Bare CREATE INDEX in recorded_sql: {stmt}"
