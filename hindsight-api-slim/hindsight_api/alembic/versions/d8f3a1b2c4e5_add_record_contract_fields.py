"""Add memory record contract fields and indexes to memory_units.

Adds provenance, authority, observation/fetch timestamps, TTL, scope, reset
condition, and record lifecycle status columns to ``memory_units`` (and to
``invalidated_memory_units`` so the curation archive stays in lockstep with
the live table), along with composite indexes on ``(bank_id, record_status)``
and ``(bank_id, provenance_class)``.

Revision ID: d8f3a1b2c4e5
Revises: c7e2f9a4d1b8
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from hindsight_api.alembic._dialect import run_for_dialect

revision: str = "d8f3a1b2c4e5"
down_revision: str | Sequence[str] | None = "c7e2f9a4d1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("memory_units", "invalidated_memory_units")

# Every statement below is a constant. The table name reaches the SQL through
# SQLAlchemy (``DDL.against`` fills ``%(fullname)s`` with the quoted,
# schema-qualified name; ``op.create_index`` / ``op.drop_index`` quote their own
# arguments) or through a bind variable on Oracle. An earlier version spliced a
# hand-quoted schema prefix into f-strings, which left the quoting to this file.
#
# All eight columns go into one ALTER TABLE per table: PG validates the two CHECK
# constraints with a scan of the table, and a single statement scans it once.
_PG_ADD_COLUMNS = """
    ALTER TABLE %(fullname)s
        ADD COLUMN IF NOT EXISTS provenance_class VARCHAR(32) NOT NULL DEFAULT 'inferred'
            CHECK (provenance_class IN ('tool-run', 'file-read', 'recalled', 'inferred')),
        ADD COLUMN IF NOT EXISTS write_authority TEXT,
        ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS ttl_seconds INTEGER,
        ADD COLUMN IF NOT EXISTS scope TEXT,
        ADD COLUMN IF NOT EXISTS reset_condition TEXT,
        ADD COLUMN IF NOT EXISTS record_status VARCHAR(16) NOT NULL DEFAULT 'active'
            CHECK (record_status IN ('active', 'stale', 'disputed', 'tombstoned'))
"""

_PG_DROP_COLUMNS = """
    ALTER TABLE %(fullname)s
        DROP COLUMN IF EXISTS record_status,
        DROP COLUMN IF EXISTS reset_condition,
        DROP COLUMN IF EXISTS scope,
        DROP COLUMN IF EXISTS ttl_seconds,
        DROP COLUMN IF EXISTS fetched_at,
        DROP COLUMN IF EXISTS observed_at,
        DROP COLUMN IF EXISTS write_authority,
        DROP COLUMN IF EXISTS provenance_class
"""

_INDEXES = (
    ("ix_memory_units_record_status", ("bank_id", "record_status")),
    ("ix_memory_units_provenance_class", ("bank_id", "provenance_class")),
)

# Oracle has no ADD COLUMN IF NOT EXISTS or CREATE INDEX IF NOT EXISTS, so each
# add runs behind a data dictionary check (user_tab_columns / user_indexes) and a
# re-run does not fail with ORA-01430 or ORA-00955. The guard blocks are constant;
# the object names and the DDL arrive as bind variables, so the DDL needs no
# quote escaping inside the PL/SQL literal.
_ORACLE_COLUMN_GUARD = """
    DECLARE
        n NUMBER;
    BEGIN
        SELECT COUNT(*) INTO n FROM user_tab_columns
        WHERE table_name = :table_name
          AND column_name = :column_name;
        IF n = 0 THEN
            EXECUTE IMMEDIATE :ddl;
        END IF;
    END;
"""

_ORACLE_INDEX_GUARD = """
    DECLARE
        n NUMBER;
    BEGIN
        SELECT COUNT(*) INTO n FROM user_indexes
        WHERE index_name = :index_name;
        IF n = 0 THEN
            EXECUTE IMMEDIATE :ddl;
        END IF;
    END;
"""

# Oracle column DDL per (table, constraint name prefix). Constraint names carry
# the prefix because Oracle constraint names are unique per schema, and both
# tables get the same two CHECK constraints.
_ORACLE_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "provenance_class",
        (
            "VARCHAR2(32) DEFAULT 'inferred' NOT NULL"
            " CONSTRAINT chk_{prefix}_provenance_class"
            " CHECK (provenance_class IN ('tool-run', 'file-read', 'recalled', 'inferred'))"
        ),
    ),
    ("write_authority", "CLOB"),
    ("observed_at", "TIMESTAMP WITH TIME ZONE"),
    ("fetched_at", "TIMESTAMP WITH TIME ZONE"),
    ("ttl_seconds", "NUMBER(10)"),
    ("scope", "CLOB"),
    ("reset_condition", "CLOB"),
    (
        "record_status",
        (
            "VARCHAR2(16) DEFAULT 'active' NOT NULL"
            " CONSTRAINT chk_{prefix}_record_status"
            " CHECK (record_status IN ('active', 'stale', 'disputed', 'tombstoned'))"
        ),
    ),
)

_ORACLE_TABLE_PREFIXES = (("memory_units", "mu"), ("invalidated_memory_units", "imu"))

_ORACLE_DROP_COLUMNS = """
    ALTER TABLE %(fullname)s DROP (
        record_status,
        reset_condition,
        scope,
        ttl_seconds,
        fetched_at,
        observed_at,
        write_authority,
        provenance_class
    )
"""


def _pg_target_schema() -> str | None:
    """Tenant schema this migration runs against on PG, or None for the search_path default."""
    return context.config.get_main_option("target_schema") or None


def _table(name: str, schema: str | None) -> sa.Table:
    """Bare table handle for ``DDL.against``; it only supplies the quoted name."""
    return sa.Table(name, sa.MetaData(), schema=schema)


def _pg_upgrade() -> None:
    schema = _pg_target_schema()
    for table in _TABLES:
        op.execute(sa.DDL(_PG_ADD_COLUMNS).against(_table(table, schema)))
    for index_name, columns in _INDEXES:
        op.create_index(index_name, "memory_units", list(columns), schema=schema, if_not_exists=True)


def _pg_downgrade() -> None:
    schema = _pg_target_schema()
    for index_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name="memory_units", schema=schema, if_exists=True)
    for table in _TABLES:
        op.execute(sa.DDL(_PG_DROP_COLUMNS).against(_table(table, schema)))


def _oracle_upgrade() -> None:
    bind = op.get_bind()
    for table, prefix in _ORACLE_TABLE_PREFIXES:
        for column_name, column_template in _ORACLE_COLUMNS:
            column_def = column_template.format(prefix=prefix)
            bind.exec_driver_sql(
                _ORACLE_COLUMN_GUARD,
                {
                    "table_name": table.upper(),
                    "column_name": column_name.upper(),
                    "ddl": f"ALTER TABLE {table} ADD ({column_name} {column_def})",
                },
            )
    for index_name, columns in _INDEXES:
        bind.exec_driver_sql(
            _ORACLE_INDEX_GUARD,
            {
                "index_name": index_name.upper(),
                "ddl": f"CREATE INDEX {index_name} ON memory_units ({', '.join(columns)})",
            },
        )


def _oracle_downgrade() -> None:
    for index_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name="memory_units")
    for table in _TABLES:
        op.execute(sa.DDL(_ORACLE_DROP_COLUMNS).against(_table(table, None)))


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade, oracle=_oracle_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade, oracle=_oracle_downgrade)
