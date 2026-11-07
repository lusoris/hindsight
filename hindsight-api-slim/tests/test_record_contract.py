"""Unit tests for W15 slice 1: memory record contract fields and recall staleness."""

from datetime import datetime, timedelta, timezone

import pytest

from hindsight_api.api.http import RecallResult
from hindsight_api.engine.memories.base import FactRecord, StoredMemory
from hindsight_api.engine.response_models import MemoryFact


def _compute_is_stale(
    record_status: str,
    ttl_seconds: int | None,
    fetched_at: datetime | None,
    now: datetime,
) -> bool:
    return (record_status == "stale") or (
        ttl_seconds is not None and fetched_at is not None and (now - fetched_at).total_seconds() > ttl_seconds
    )


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
    is_stale = _compute_is_stale(rec.record_status, rec.ttl_seconds, rec.fetched_at, now)
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
    is_stale = _compute_is_stale(rec.record_status, rec.ttl_seconds, rec.fetched_at, now)
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
    is_stale = _compute_is_stale(rec.record_status, rec.ttl_seconds, rec.fetched_at, now)
    assert is_stale is True
    assert rec.is_stale(now=now) is True
    fact = MemoryFact(id=rec.unit_id, text=rec.text, fact_type=rec.fact_type, is_stale=is_stale)
    assert fact.is_stale is True
    result = RecallResult(id=fact.id, text=fact.text, type=fact.fact_type, is_stale=fact.is_stale)
    assert result.is_stale is True
