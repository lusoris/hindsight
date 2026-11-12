"""Tests pinning the transfer record contract schema and import behaviour.

Pins TransferFact / TransferObservation default and validation behaviour,
synchronisation between schema Literal types and engine valid tuple constants,
and field propagation by _apply_record_contract.
"""

import typing
from datetime import datetime, timezone

import pydantic
import pytest

from hindsight_api.engine.memories.base import VALID_PROVENANCE_CLASSES, VALID_RECORD_STATUSES
from hindsight_api.engine.retain.types import ProcessedFact, pack_embedding
from hindsight_api.engine.transfer.importer import _apply_record_contract
from hindsight_api.engine.transfer.schema import (
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
