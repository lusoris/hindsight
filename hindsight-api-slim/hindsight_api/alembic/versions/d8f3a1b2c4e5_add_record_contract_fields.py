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

from alembic import context, op

from hindsight_api.alembic._dialect import run_for_dialect

revision: str = "d8f3a1b2c4e5"
down_revision: str | Sequence[str] | None = "c7e2f9a4d1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("memory_units", "invalidated_memory_units")


def _pg_schema_prefix() -> str:
    """Schema-qualifier for raw SQL on PG (multi-tenant search_path)."""
    schema = context.config.get_main_option("target_schema")
    return f'"{schema}".' if schema else ""


def _pg_upgrade() -> None:
    schema = _pg_schema_prefix()
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {schema}{table}
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
        )

    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_memory_units_record_status ON {schema}memory_units (bank_id, record_status)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_memory_units_provenance_class "
        f"ON {schema}memory_units (bank_id, provenance_class)"
    )


def _pg_downgrade() -> None:
    schema = _pg_schema_prefix()
    op.execute(f"DROP INDEX IF EXISTS {schema}ix_memory_units_provenance_class")
    op.execute(f"DROP INDEX IF EXISTS {schema}ix_memory_units_record_status")
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {schema}{table}
                DROP COLUMN IF EXISTS record_status,
                DROP COLUMN IF EXISTS reset_condition,
                DROP COLUMN IF EXISTS scope,
                DROP COLUMN IF EXISTS ttl_seconds,
                DROP COLUMN IF EXISTS fetched_at,
                DROP COLUMN IF EXISTS observed_at,
                DROP COLUMN IF EXISTS write_authority,
                DROP COLUMN IF EXISTS provenance_class
            """
        )


def _oracle_upgrade() -> None:
    for table, prefix in (("memory_units", "mu"), ("invalidated_memory_units", "imu")):
        op.execute(
            f"""
            ALTER TABLE {table} ADD (
                provenance_class VARCHAR2(32) DEFAULT 'inferred' NOT NULL
                    CONSTRAINT chk_{prefix}_provenance_class
                    CHECK (provenance_class IN ('tool-run', 'file-read', 'recalled', 'inferred')),
                write_authority CLOB,
                observed_at TIMESTAMP WITH TIME ZONE,
                fetched_at TIMESTAMP WITH TIME ZONE,
                ttl_seconds NUMBER(10),
                scope CLOB,
                reset_condition CLOB,
                record_status VARCHAR2(16) DEFAULT 'active' NOT NULL
                    CONSTRAINT chk_{prefix}_record_status
                    CHECK (record_status IN ('active', 'stale', 'disputed', 'tombstoned'))
            )
            """
        )

    op.execute("CREATE INDEX ix_memory_units_record_status ON memory_units (bank_id, record_status)")
    op.execute("CREATE INDEX ix_memory_units_provenance_class ON memory_units (bank_id, provenance_class)")


def _oracle_downgrade() -> None:
    op.execute("DROP INDEX ix_memory_units_provenance_class")
    op.execute("DROP INDEX ix_memory_units_record_status")
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} DROP (
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
        )


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade, oracle=_oracle_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade, oracle=_oracle_downgrade)
