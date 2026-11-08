"""Memory record contract: the provenance/TTL/status columns and the stale flag on recall.

The record-level tests pin validation and the staleness rule; the database test drives
the columns through the store's insert and the engine's recall, which is the path a
caller actually sees.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hindsight_api import RequestContext
from hindsight_api.api.http import RecallResult
from hindsight_api.engine.memories import get_memories
from hindsight_api.engine.memories.base import FactRecord, StoredMemory, compute_is_stale
from hindsight_api.engine.memory_engine import MemoryEngine
from hindsight_api.engine.response_models import Budget, MemoryFact
from hindsight_api.engine.schema import fq_table
from tests.fact_fakes import RECORD_CONTRACT_DEFAULTS


def test_fact_record_accepts_provenance_and_write_authority() -> None:
    now = datetime.now(timezone.utc)
    rec = FactRecord(
        unit_id="00000000-0000-0000-0000-000000000001",
        text="verified via tool",
        embedding=[0.1, 0.2],
        fact_type="world",
        provenance_class="tool-run",
        write_authority="pytest-runner",
        observed_at=now,
        fetched_at=now,
        ttl_seconds=3600,
        scope="repo:hindsight",
        reset_condition="on schema migration",
        record_status="active",
    )
    assert rec.provenance_class == "tool-run"
    assert rec.write_authority == "pytest-runner"
    assert rec.observed_at == now
    assert rec.fetched_at == now
    assert rec.ttl_seconds == 3600
    assert rec.scope == "repo:hindsight"
    assert rec.reset_condition == "on schema migration"
    assert rec.record_status == "active"


def test_fact_record_defaults() -> None:
    rec = FactRecord(
        unit_id="00000000-0000-0000-0000-000000000002",
        text="default record",
        embedding=[0.1, 0.2],
        fact_type="world",
    )
    assert rec.provenance_class == "inferred"
    assert rec.record_status == "active"
    assert rec.write_authority is None
    assert rec.observed_at is None
    assert rec.fetched_at is None
    assert rec.ttl_seconds is None
    assert rec.scope is None
    assert rec.reset_condition is None

    stored = StoredMemory(
        unit_id="00000000-0000-0000-0000-000000000002",
        text="default stored",
        fact_type="world",
    )
    assert stored.provenance_class == "inferred"
    assert stored.record_status == "active"
    assert stored.write_authority is None
    assert stored.observed_at is None
    assert stored.fetched_at is None
    assert stored.ttl_seconds is None
    assert stored.scope is None
    assert stored.reset_condition is None


def test_invalid_provenance_class_raises() -> None:
    with pytest.raises(ValueError, match="provenance_class"):
        FactRecord(
            unit_id="00000000-0000-0000-0000-000000000003",
            text="bad provenance",
            embedding=[0.1],
            fact_type="world",
            provenance_class="bogus",
        )


def test_invalid_record_status_raises() -> None:
    with pytest.raises(ValueError, match="record_status"):
        FactRecord(
            unit_id="00000000-0000-0000-0000-000000000004",
            text="bad status",
            embedding=[0.1],
            fact_type="world",
            record_status="deleted",
        )


def test_processed_fact_record_contract_validation() -> None:
    from array import array

    from hindsight_api.engine.retain.types import ProcessedFact

    # Defaults construct fine
    fact = ProcessedFact(
        fact_text="Ada shipped the parser",
        fact_type="world",
        embedding=array("f", [0.01, 0.02]),
        occurred_start=None,
        occurred_end=None,
        mentioned_at=None,
        context="standup notes",
        metadata={},
    )
    assert fact.provenance_class == "inferred"
    assert fact.record_status == "active"

    # provenance_class="bogus" raises ValueError
    with pytest.raises(ValueError, match="provenance_class"):
        ProcessedFact(
            fact_text="Ada shipped the parser",
            fact_type="world",
            embedding=array("f", [0.01, 0.02]),
            occurred_start=None,
            occurred_end=None,
            mentioned_at=None,
            context="standup notes",
            metadata={},
            provenance_class="bogus",
        )

    # record_status="bogus" raises ValueError
    with pytest.raises(ValueError, match="record_status"):
        ProcessedFact(
            fact_text="Ada shipped the parser",
            fact_type="world",
            embedding=array("f", [0.01, 0.02]),
            occurred_start=None,
            occurred_end=None,
            mentioned_at=None,
            context="standup notes",
            metadata={},
            record_status="bogus",
        )


def test_ttl_expired_is_stale() -> None:
    now = datetime.now(timezone.utc)
    fetched_at = now - timedelta(hours=2)
    rec = StoredMemory(
        unit_id="00000000-0000-0000-0000-000000000005",
        text="expired fact",
        fact_type="world",
        ttl_seconds=3600,
        fetched_at=fetched_at,
        record_status="active",
    )
    is_stale = compute_is_stale(rec.record_status, rec.ttl_seconds, rec.fetched_at, now=now)
    assert is_stale is True
    assert rec.is_stale(now=now) is True
    fact = MemoryFact(id=rec.unit_id, text=rec.text, fact_type=rec.fact_type, is_stale=is_stale)
    assert fact.is_stale is True
    result = RecallResult(id=fact.id, text=fact.text, type=fact.fact_type, is_stale=fact.is_stale)
    assert result.is_stale is True


def test_ttl_not_expired_is_not_stale() -> None:
    now = datetime.now(timezone.utc)
    fetched_at = now - timedelta(minutes=5)
    rec = StoredMemory(
        unit_id="00000000-0000-0000-0000-000000000006",
        text="fresh fact",
        fact_type="world",
        ttl_seconds=3600,
        fetched_at=fetched_at,
        record_status="active",
    )
    is_stale = compute_is_stale(rec.record_status, rec.ttl_seconds, rec.fetched_at, now=now)
    assert is_stale is False
    assert rec.is_stale(now=now) is False
    fact = MemoryFact(id=rec.unit_id, text=rec.text, fact_type=rec.fact_type)
    assert fact.is_stale is False
    result = RecallResult(id=fact.id, text=fact.text, type=fact.fact_type)
    assert result.is_stale is False


def test_record_status_stale_ignores_ttl() -> None:
    now = datetime.now(timezone.utc)
    fetched_at = now - timedelta(minutes=5)
    rec = StoredMemory(
        unit_id="00000000-0000-0000-0000-000000000007",
        text="explicitly stale fact",
        fact_type="world",
        ttl_seconds=3600,
        fetched_at=fetched_at,
        record_status="stale",
    )
    is_stale = compute_is_stale(rec.record_status, rec.ttl_seconds, rec.fetched_at, now=now)
    assert is_stale is True
    assert rec.is_stale(now=now) is True
    fact = MemoryFact(id=rec.unit_id, text=rec.text, fact_type=rec.fact_type, is_stale=is_stale)
    assert fact.is_stale is True
    result = RecallResult(id=fact.id, text=fact.text, type=fact.fact_type, is_stale=fact.is_stale)
    assert result.is_stale is True


def test_compute_is_stale_normalizes_naive_fetched_at() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Naive values as a timezone-unaware driver returns them: UTC wall time, no tzinfo.
    fetched_fresh = (now - timedelta(seconds=30)).replace(tzinfo=None)
    fetched_stale = (now - timedelta(seconds=90)).replace(tzinfo=None)
    assert compute_is_stale("active", ttl_seconds=60, fetched_at=fetched_fresh, now=now) is False
    assert compute_is_stale("active", ttl_seconds=60, fetched_at=fetched_stale, now=now) is True


def test_compute_is_stale_normalizes_aware_offset_fetched_at() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    tz_plus_5 = timezone(timedelta(hours=5))
    fetched_aware = (now - timedelta(seconds=30)).astimezone(tz_plus_5)
    assert compute_is_stale("active", ttl_seconds=60, fetched_at=fetched_aware, now=now) is False
    assert compute_is_stale("active", ttl_seconds=10, fetched_at=fetched_aware, now=now) is True


async def _seed(memory: MemoryEngine, conn, bank_id: str, text: str, **contract: object) -> str:
    """Insert one world fact through the store with the given record-contract columns."""
    fact = SimpleNamespace(
        fact_text=text,
        embedding=(await memory.embeddings.encode([text]))[0],
        fact_type="world",
        tags=[],
        context=None,
        document_id=None,
        chunk_id=None,
        metadata=None,
        observation_scopes=None,
        entities=[],
        causal_relations=[],
        occurred_start=None,
        occurred_end=None,
        mentioned_at=None,
        attachment_ids=[],
        **{**RECORD_CONTRACT_DEFAULTS, **contract},
    )
    unit_ids = await get_memories().insert_facts(conn=conn, ops=memory._backend.ops, bank_id=bank_id, facts=[fact])
    return unit_ids[0]


@pytest.mark.asyncio
async def test_recall_flags_expired_and_stale_records(memory: MemoryEngine, request_context: RequestContext) -> None:
    bank_id = f"test-record-contract-{uuid.uuid4().hex[:8]}"
    await memory.ensure_bank_profile(bank_id=bank_id, request_context=request_context)
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    try:
        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            expired = await _seed(
                memory,
                conn,
                bank_id,
                "The staging cluster runs Kubernetes 1.31.",
                provenance_class="tool-run",
                write_authority="kubectl version",
                fetched_at=two_hours_ago,
                ttl_seconds=3600,
            )
            marked = await _seed(
                memory, conn, bank_id, "The staging cluster runs on three nodes.", record_status="stale"
            )
            fresh = await _seed(
                memory,
                conn,
                bank_id,
                "The staging cluster stores data on Ceph.",
                fetched_at=datetime.now(timezone.utc),
                ttl_seconds=3600,
            )

            stored = await get_memories().get_memories(
                conn=conn, fq_table=fq_table, bank_id=bank_id, unit_ids=[expired]
            )
            [row] = stored
            assert row.provenance_class == "tool-run"
            assert row.write_authority == "kubectl version"
            assert row.ttl_seconds == 3600

        result = await memory.recall_async(
            bank_id=bank_id,
            query="staging cluster",
            fact_type=["world"],
            budget=Budget.MID,
            max_tokens=2048,
            request_context=request_context,
        )
        stale_by_id = {fact.id: fact.is_stale for fact in result.results}
        assert {expired, marked, fresh} <= set(stale_by_id), stale_by_id
        assert stale_by_id[expired] is True
        assert stale_by_id[marked] is True
        assert stale_by_id[fresh] is False
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


@pytest.mark.asyncio
async def test_invalidated_memory_keeps_its_record_contract(
    memory: MemoryEngine, request_context: RequestContext
) -> None:
    """The archive move copies the contract columns, and the archived read must return them."""
    bank_id = f"test-record-contract-{uuid.uuid4().hex[:8]}"
    await memory.ensure_bank_profile(bank_id=bank_id, request_context=request_context)
    fetched = datetime.now(timezone.utc) - timedelta(minutes=10)
    try:
        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            unit_id = await _seed(
                memory,
                conn,
                bank_id,
                "The build host runs Python 3.11.",
                provenance_class="tool-run",
                write_authority="python --version",
                fetched_at=fetched,
                ttl_seconds=900,
                scope="host:build",
                reset_condition="on toolchain upgrade",
                record_status="disputed",
            )
            store = get_memories()
            assert await store.invalidate_memory(
                conn=conn, fq_table=fq_table, bank_id=bank_id, unit_id=unit_id, reason="test"
            )
            archived = await store.get_archived_memory(conn=conn, fq_table=fq_table, bank_id=bank_id, unit_id=unit_id)
        assert archived is not None
        assert archived.provenance_class == "tool-run"
        assert archived.write_authority == "python --version"
        assert archived.fetched_at == fetched
        assert archived.ttl_seconds == 900
        assert archived.scope == "host:build"
        assert archived.reset_condition == "on toolchain upgrade"
        assert archived.record_status == "disputed"
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)


@pytest.mark.asyncio
async def test_recall_source_facts_carries_record_contract_fields(
    memory: MemoryEngine, request_context: RequestContext
) -> None:
    """The store's recall_source_facts implementation must return record_status, ttl_seconds, and fetched_at."""
    bank_id = f"test-recall-source-facts-{uuid.uuid4().hex[:8]}"
    await memory.ensure_bank_profile(bank_id=bank_id, request_context=request_context)
    now = datetime.now(timezone.utc)
    try:
        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            unit_id = await _seed(
                memory,
                conn,
                bank_id,
                "The primary database runs Postgres 16.",
                provenance_class="tool-run",
                write_authority="psql --version",
                fetched_at=now,
                ttl_seconds=1800,
                record_status="active",
            )
            store = get_memories()
            sources = await store.recall_source_facts(conn=conn, fq_table=fq_table, bank_id=bank_id, unit_ids=[unit_id])
            assert unit_id in sources
            fact = sources[unit_id]
            assert fact.record_status == "active"
            assert fact.ttl_seconds == 1800
            assert fact.fetched_at is not None
            assert abs((fact.fetched_at - now).total_seconds()) < 1.0
    finally:
        await memory.delete_bank(bank_id, request_context=request_context)
