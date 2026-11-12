"""Tests pinning the transfer record contract schema and import behaviour.

Pins TransferFact / TransferObservation default, date-normalisation and validation
behaviour, synchronisation between schema Literal types and engine valid tuple
constants, and field propagation by _apply_record_contract and build_fact_records.
"""

import dataclasses
import typing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pydantic
import pytest

from hindsight_api.engine.memories.base import (
    VALID_PROVENANCE_CLASSES,
    VALID_RECORD_STATUSES,
    build_fact_records,
)
from hindsight_api.engine.memories.pg import transfer as pg_transfer
from hindsight_api.engine.retain.types import ProcessedFact, pack_embedding
from hindsight_api.engine.transfer.export import _record_contract, _UnitLocation
from hindsight_api.engine.transfer.importer import _apply_record_contract
from hindsight_api.engine.transfer.schema import (
    TTL_SECONDS_MAX,
    ProvenanceClass,
    RecordStatus,
    TransferFact,
    TransferObservation,
    TransferRecordContract,
)


def test_legacy_archive_defaults() -> None:
    """An archive written before record contract fields existed imports with default values."""
    fact = TransferFact.model_validate({"text": "t", "fact_type": "world"})
    assert fact.provenance_class == "inferred"
    assert fact.record_status == "active"
    assert fact.write_authority is None
    assert fact.observed_at is None
    assert fact.fetched_at is None
    assert fact.ttl_seconds is None
    assert fact.scope is None
    assert fact.reset_condition is None

    obs = TransferObservation.model_validate({"text": "t"})
    assert obs.provenance_class == "inferred"
    assert obs.record_status == "active"
    assert obs.write_authority is None
    assert obs.observed_at is None
    assert obs.fetched_at is None
    assert obs.ttl_seconds is None
    assert obs.scope is None
    assert obs.reset_condition is None


def test_record_contract_validation() -> None:
    """Invalid provenance_class or record_status values are rejected by Pydantic."""
    with pytest.raises(pydantic.ValidationError):
        TransferFact.model_validate({"text": "t", "fact_type": "world", "record_status": "expired"})

    with pytest.raises(pydantic.ValidationError):
        TransferFact.model_validate({"text": "t", "fact_type": "world", "provenance_class": "guessed"})


def test_record_contract_dates_normalized_on_fact_and_observation() -> None:
    """Offsetless ISO-8601 strings become aware UTC and year-9999 placeholders become None
    on both TransferFact and TransferObservation (not shadowed by _drop_placeholder_dates).
    """
    expected_observed = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    expected_fetched = datetime(2026, 3, 1, 10, 5, 0, tzinfo=timezone.utc)

    fact = TransferFact.model_validate(
        {
            "text": "t",
            "fact_type": "world",
            "observed_at": "2026-03-01T10:00:00",
            "fetched_at": "2026-03-01T10:05:00",
        }
    )
    assert fact.observed_at == expected_observed
    assert fact.observed_at.tzinfo == timezone.utc
    assert fact.fetched_at == expected_fetched
    assert fact.fetched_at.tzinfo == timezone.utc

    obs = TransferObservation.model_validate(
        {
            "text": "t",
            "observed_at": "2026-03-01T10:00:00",
            "fetched_at": "2026-03-01T10:05:00",
        }
    )
    assert obs.observed_at == expected_observed
    assert obs.observed_at.tzinfo == timezone.utc
    assert obs.fetched_at == expected_fetched
    assert obs.fetched_at.tzinfo == timezone.utc

    fact_placeholder = TransferFact.model_validate(
        {
            "text": "t",
            "fact_type": "world",
            "observed_at": "9999-12-31T23:59:59Z",
            "fetched_at": "9999-01-01T00:00:00",
        }
    )
    assert fact_placeholder.observed_at is None
    assert fact_placeholder.fetched_at is None

    obs_placeholder = TransferObservation.model_validate(
        {
            "text": "t",
            "observed_at": "9999-12-31T23:59:59Z",
            "fetched_at": "9999-01-01T00:00:00",
        }
    )
    assert obs_placeholder.observed_at is None
    assert obs_placeholder.fetched_at is None


@pytest.mark.parametrize("model", [TransferFact, TransferObservation])
def test_ttl_seconds_bounded_to_the_column_range(model: type[TransferRecordContract]) -> None:
    """An archive cannot carry a TTL that reads as stale forever or that the INTEGER column cannot hold."""
    base = {"text": "t", "fact_type": "world"}
    for ok in (0, 3600, TTL_SECONDS_MAX):
        assert model.model_validate({**base, "ttl_seconds": ok}).ttl_seconds == ok
    for bad in (-1, TTL_SECONDS_MAX + 1):
        with pytest.raises(pydantic.ValidationError, match="ttl_seconds"):
            model.model_validate({**base, "ttl_seconds": bad})


@dataclass
class _StoredMemory:
    """The attribute shape of a store's memory, plus a field the contract does not own."""

    text: str
    provenance_class: str
    write_authority: str | None
    observed_at: datetime | None
    fetched_at: datetime | None
    ttl_seconds: int | None
    scope: str | None
    reset_condition: str | None
    record_status: str


def test_export_record_contract_reads_attribute_and_mapping_sources() -> None:
    """The export helper takes all eight fields from a store memory and from a SQL row alike."""
    t_observed = datetime(2026, 3, 2, 11, 0, 0, tzinfo=timezone.utc)
    t_fetched = datetime(2026, 3, 2, 11, 15, 0, tzinfo=timezone.utc)
    stored = _StoredMemory(
        text="t",
        provenance_class="tool-run",
        write_authority="agent:v1",
        observed_at=t_observed,
        fetched_at=t_fetched,
        ttl_seconds=60,
        scope="repo",
        reset_condition="on-commit",
        record_status="tombstoned",
    )
    expected = {
        "provenance_class": "tool-run",
        "write_authority": "agent:v1",
        "observed_at": t_observed,
        "fetched_at": t_fetched,
        "ttl_seconds": 60,
        "scope": "repo",
        "reset_condition": "on-commit",
        "record_status": "tombstoned",
    }
    assert _record_contract(stored).model_dump() == expected
    assert _record_contract(dataclasses.asdict(stored)).model_dump() == expected


def test_export_record_contract_refuses_a_source_missing_a_column() -> None:
    """A loader that stops selecting a contract column fails loudly instead of exporting the default."""
    row = {
        "provenance_class": "tool-run",
        "write_authority": None,
        "observed_at": None,
        "fetched_at": None,
        "ttl_seconds": None,
        "scope": None,
        "reset_condition": None,
    }
    with pytest.raises(KeyError, match="record_status"):
        _record_contract(row)

    @dataclass
    class _NoStatus:
        provenance_class: str = "tool-run"

    with pytest.raises(AttributeError):
        _record_contract(_NoStatus())


def test_record_contract_literals_in_sync_with_base_tuples() -> None:
    """The schema Literal types stay in lockstep with the valid base tuples."""
    assert typing.get_args(ProvenanceClass) == VALID_PROVENANCE_CLASSES
    assert typing.get_args(RecordStatus) == VALID_RECORD_STATUSES


def test_apply_record_contract_copies_all_eight_fields() -> None:
    """_apply_record_contract copies all eight contract fields onto a ProcessedFact."""
    t_observed = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    t_fetched = datetime(2026, 3, 1, 10, 5, 0, tzinfo=timezone.utc)

    source = TransferRecordContract(
        provenance_class="tool-run",
        write_authority="agent:lane",
        observed_at=t_observed,
        fetched_at=t_fetched,
        ttl_seconds=60,
        scope="repo",
        reset_condition="on-commit",
        record_status="stale",
    )
    processed = ProcessedFact(
        fact_text="Alice works at Google.",
        fact_type="world",
        embedding=pack_embedding([0.1, 0.2]),
        occurred_start=None,
        occurred_end=None,
        mentioned_at=None,
        context="",
        metadata={},
    )

    _apply_record_contract(processed, source)

    assert processed.provenance_class == "tool-run"
    assert processed.write_authority == "agent:lane"
    assert processed.observed_at == t_observed
    assert processed.fetched_at == t_fetched
    assert processed.ttl_seconds == 60
    assert processed.scope == "repo"
    assert processed.reset_condition == "on-commit"
    assert processed.record_status == "stale"


def test_build_fact_records_copies_all_eight_fields() -> None:
    """build_fact_records copies all eight non-default contract fields onto FactRecord."""
    t_observed = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    t_fetched = datetime(2026, 3, 1, 10, 5, 0, tzinfo=timezone.utc)

    processed = ProcessedFact(
        fact_text="Alice works at Google.",
        fact_type="world",
        embedding=pack_embedding([0.1, 0.2]),
        occurred_start=None,
        occurred_end=None,
        mentioned_at=None,
        context="",
        metadata={},
        provenance_class="tool-run",
        write_authority="agent:lane",
        observed_at=t_observed,
        fetched_at=t_fetched,
        ttl_seconds=60,
        scope="repo",
        reset_condition="on-commit",
        record_status="stale",
    )

    [record] = build_fact_records(["unit-1"], [processed], document_id="doc-1")

    assert record.provenance_class == "tool-run"
    assert record.write_authority == "agent:lane"
    assert record.observed_at == t_observed
    assert record.fetched_at == t_fetched
    assert record.ttl_seconds == 60
    assert record.scope == "repo"
    assert record.reset_condition == "on-commit"
    assert record.record_status == "stale"


@dataclass
class _StubConn:
    rows: list[dict[str, Any]]

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return self.rows


@pytest.mark.asyncio
async def test_pg_load_observations_carries_all_eight_fields() -> None:
    """pg.transfer.load_observations carries all eight non-default contract columns."""
    t_observed = datetime(2026, 3, 2, 11, 0, 0, tzinfo=timezone.utc)
    t_fetched = datetime(2026, 3, 2, 11, 15, 0, tzinfo=timezone.utc)
    src_id = "00000000-0000-0000-0000-0000000000a1"
    conn = _StubConn(
        rows=[
            {
                "id": "00000000-0000-0000-0000-0000000000b1",
                "text": "Alice and Bob are colleagues.",
                "tags": ["team"],
                "created_at": t_observed,
                "event_date": t_observed,
                "occurred_start": None,
                "occurred_end": None,
                "mentioned_at": t_observed,
                "observation_scopes": None,
                "proof_count": 2,
                "source_memory_ids": [src_id],
                "provenance_class": "recalled",
                "write_authority": "consolidator:v1",
                "observed_at": t_observed,
                "fetched_at": t_fetched,
                "ttl_seconds": 3600,
                "scope": "team",
                "reset_condition": "on-reorg",
                "record_status": "disputed",
            }
        ]
    )

    [obs] = await pg_transfer.load_observations(
        conn=conn,
        fq_table=lambda name: name,
        bank_id="bank-1",
        unit_index={src_id: _UnitLocation(document_id="doc-1", ordinal=0)},
    )

    assert obs.provenance_class == "recalled"
    assert obs.write_authority == "consolidator:v1"
    assert obs.observed_at == t_observed
    assert obs.fetched_at == t_fetched
    assert obs.ttl_seconds == 3600
    assert obs.scope == "team"
    assert obs.reset_condition == "on-reorg"
    assert obs.record_status == "disputed"
