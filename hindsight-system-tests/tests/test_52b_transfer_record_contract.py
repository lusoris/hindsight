"""A memory's record contract survives a move to another bank.

The record contract says how far a memory can be trusted: where it came from
(``provenance_class``, ``write_authority``), when it was true (``observed_at``,
``fetched_at``, ``ttl_seconds``), and whether it still holds (``record_status``).
Recall reads it to flag ``is_stale``. Export/import is the one path that writes
it into another bank, so it is where two capabilities meet: an import that drops
the contract still succeeds, the facts arrive and recall answers, and only the
trust is gone — a memory marked stale on the source comes back fresh, with
nothing anywhere saying it changed.

Retain cannot set the contract (nothing in the retain pipeline writes it yet),
so the story stamps it into an exported archive, the way a producer that does
set it would, and follows it through import, recall and a second export.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from hindsight_client_api.api.document_transfer_api import DocumentTransferApi

from hindsight_system_tests.payloads import consolidation, extracted, fact

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc).replace(microsecond=0)

# One fact per way recall can judge freshness, each one a different field that
# an import could drop. Dropping record_status turns the first fresh, dropping
# ttl_seconds turns the second fresh, and dropping fetched_at turns the third
# stale (a TTL with no fetch time has an unknown age).
STATUS_STALE = "Alice moved to Berlin"
TTL_ELAPSED = "Alice renewed her Berlin lease"
TTL_FRESH = "Alice works from home"

CONTRACTS: dict[str, dict[str, object]] = {
    STATUS_STALE: {
        "provenance_class": "recalled",
        "write_authority": "agent:planner",
        "scope": "household",
        "reset_condition": "on-move",
        "record_status": "stale",
    },
    TTL_ELAPSED: {
        "provenance_class": "tool-run",
        "observed_at": (NOW - timedelta(days=2)).isoformat(),
        "fetched_at": (NOW - timedelta(days=1)).isoformat(),
        "ttl_seconds": 60,
    },
    TTL_FRESH: {
        "provenance_class": "file-read",
        "fetched_at": NOW.isoformat(),
        "ttl_seconds": 365 * 24 * 3600,
    },
}

EXPECTED_STALE = {STATUS_STALE: True, TTL_ELAPSED: True, TTL_FRESH: False}


@pytest.fixture
async def source_bank(client, llm, bank_id, settled) -> str:
    llm.on_step("extract_facts").returns(
        extracted(
            fact(STATUS_STALE, who="Alice", entities=["Alice", "Berlin"]),
            fact(TTL_ELAPSED, who="Alice", entities=["Alice", "Berlin"]),
            fact(TTL_FRESH, who="Alice", entities=["Alice"]),
        )
    )
    llm.on_step("consolidate").returns(consolidation())
    await client.aretain(bank_id=bank_id, content=f"{STATUS_STALE}. {TTL_ELAPSED}. {TTL_FRESH}.", document_id="d1")
    await settled(bank_id)
    return bank_id


@pytest.fixture
async def destination(client) -> AsyncIterator[str]:
    bank = f"systest-{uuid.uuid4().hex[:12]}"
    yield bank
    await client.banks.delete_bank(bank)


def _contract_key(text: str) -> str:
    """The CONTRACTS key a stored fact text belongs to (the server appends ``| Involving: ...``)."""
    return next(key for key in CONTRACTS if text.startswith(key))


def _stamp_contracts(archive: bytes) -> bytes:
    """The archive with each fact's record contract set from CONTRACTS, every other entry untouched."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.startswith("documents/") and info.filename.endswith(".json"):
                document = json.loads(data)
                for archived_fact in document["facts"]:
                    archived_fact.update(CONTRACTS[_contract_key(archived_fact["text"])])
                data = json.dumps(document).encode()
            dst.writestr(info, data)
    return out.getvalue()


def _archived_facts(archive: bytes) -> dict[str, dict]:
    """Every fact in a documents archive, keyed by its CONTRACTS key."""
    facts: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for name in zf.namelist():
            if name.startswith("documents/") and name.endswith(".json"):
                for archived_fact in json.loads(zf.read(name))["facts"]:
                    facts[_contract_key(archived_fact["text"])] = archived_fact
    return facts


async def _import(client, llm, bank: str, archive: bytes) -> None:
    # Silence the destination's own consolidation, as test_52 does: the import
    # leaves new facts there and auto-consolidation would otherwise run over them.
    llm.reset()
    llm.on_step("consolidate").returns(consolidation())
    await DocumentTransferApi(client.documents.api_client).import_documents(bank, archive)


async def _stamped_import(client, llm, source_bank: str, destination: str, settled) -> None:
    archive = _stamp_contracts(await client.aexport_documents(bank_id=source_bank))
    await _import(client, llm, destination, archive)
    await settled(destination)


async def test_recall_flags_the_imported_facts_the_source_marked_stale(
    client, llm, source_bank, destination, settled
):
    await _stamped_import(client, llm, source_bank, destination, settled)

    response = await client.arecall(bank_id=destination, query="Alice")
    stale = {_contract_key(r.text): r.is_stale for r in response.results}
    assert stale == EXPECTED_STALE


async def test_an_unstamped_import_is_fresh(client, llm, source_bank, destination, settled):
    """The control. Without it the assertion above could pass on a bank that flags
    every imported fact stale, whatever its contract says."""
    await _import(client, llm, destination, await client.aexport_documents(bank_id=source_bank))
    await settled(destination)

    response = await client.arecall(bank_id=destination, query="Alice")
    assert {_contract_key(r.text): r.is_stale for r in response.results} == dict.fromkeys(CONTRACTS, False)


async def test_a_second_export_carries_the_contract_on(client, llm, source_bank, destination, settled):
    """A bank is moved more than once. The contract has to come back out of the
    destination exactly as it went in, not only be readable by recall there."""
    await _stamped_import(client, llm, source_bank, destination, settled)

    reexported = _archived_facts(await client.aexport_documents(bank_id=destination))
    assert reexported.keys() == CONTRACTS.keys()
    for key, contract in CONTRACTS.items():
        for field, value in contract.items():
            got = reexported[key][field]
            if field in ("observed_at", "fetched_at"):
                assert datetime.fromisoformat(got) == datetime.fromisoformat(str(value)), (key, field)
            else:
                assert got == value, (key, field)
