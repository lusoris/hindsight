"""Whole-bank import left a half-restored bank on a bad section.

Preflight refuses the upload before queuing; rollback independently removes only
this restore's bank when a later batch fails. Real-pg0 cases exercise committed
batches, while orchestration cases keep both barriers testable without local ML.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tracemalloc
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from asyncpg import DataError

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine import memory_engine as engine_module
from hindsight_api.engine import transfer
from hindsight_api.engine.cross_encoder import RRFPassthroughCrossEncoder
from hindsight_api.engine.db_utils import acquire_with_retry
from hindsight_api.engine.embeddings import Embeddings
from hindsight_api.engine.retain import bank_utils
from hindsight_api.engine.schema import fq_table
from hindsight_api.engine.transfer import TransferScope, importer, stream_export_bank
from hindsight_api.engine.transfer.schema import (
    HISTORY_TABLES,
    OPERATIONAL_TABLES,
    TransferDocument,
    TransferFact,
    TransferManifest,
    TransferObservation,
)
from tests.test_document_transfer import _retain, _seed_observation, _unique_bank


class _FixtureEmbeddings(Embeddings):
    """Fixed vectors: these tests exercise archive handling, not model quality."""

    provider_name = "atomic-fixture"
    dimension = 384

    async def initialize(self) -> None:
        pass

    async def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]


@pytest.fixture(scope="session")
def embeddings() -> Embeddings:
    return _FixtureEmbeddings()


@pytest.fixture(scope="session")
def cross_encoder() -> RRFPassthroughCrossEncoder:
    return RRFPassthroughCrossEncoder()


@dataclass
class _BankArchive:
    source: str
    data: bytes


@dataclass
class _InvalidTimestampOperation:
    bank_id: str
    operation_type: str = "retain"
    status: str = "completed"
    # A JSON number passes structural preflight, but asyncpg requires a datetime.
    created_at: int = 7


def _invalid_streamed_timestamp(archive: bytes, source: str) -> bytes:
    # Live memory units are replayed from documents, not a data/memory_units.json
    # section. The operations log is a streamed section restored verbatim.
    row = _InvalidTimestampOperation(bank_id=source)
    return _rewrite(archive, "data/async_operations.json", json.dumps([asdict(row)]).encode())


def _synthetic_archive() -> bytes:
    """A minimal valid bank archive for parser/orchestration tests without a DB."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            TransferManifest(
                source_bank_id="atomic-source", archive_type="bank", includes_history=True
            ).model_dump_json(),
        )
        zf.writestr("banks.json", '[{"bank_id":"atomic-source"}]')
        for index in range(4):
            document = TransferDocument(
                id=f"doc-{index}",
                original_text=f"Alice owns bicycle {index}.",
                facts=[TransferFact(text=f"Alice owns bicycle {index}.", fact_type="world")],
            )
            zf.writestr(f"documents/{index:06d}.json", document.model_dump_json())
        zf.writestr("observations.json", "[" + TransferObservation(text="Alice cycles.").model_dump_json() + "]")
    return out.getvalue()


def _rewrite(archive: bytes, name: str, payload: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename != name:
                dst.writestr(info, src.read(info))
        dst.writestr(name, payload)
    return out.getvalue()


def _bad_section(archive: bytes, section: str) -> bytes:
    if section == "document":
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = sorted(n for n in zf.namelist() if n.startswith("documents/") and n.endswith(".json"))
        assert len(names) >= 4, "the planted bad document must follow three valid documents"
        return _rewrite(
            archive, names[3], b'{"id":"bad","facts":[{"text":"bad","fact_type":"world","ttl_seconds":-1}]}'
        )
    if section == "observation":
        return _rewrite(archive, "observations.json", b'[{"text":"bad","record_status":"expired"}]')
    if section == "knowledge_page":
        return _rewrite(archive, "knowledge_pages.json", b'[{"kind":"not-a-page"}]')
    raise AssertionError(section)


@pytest_asyncio.fixture
async def exported_bank(memory: MemoryEngine, request_context: RequestContext) -> AsyncIterator[_BankArchive]:
    source = _unique_bank("atomic_source")
    try:
        first_ids = []
        for index in range(4):
            unit_ids = await _retain(memory, source, f"Alice owns bicycle {index}.", request_context, f"doc-{index}")
            first_ids.extend(unit_ids)
        backend = await memory._get_backend()
        await _seed_observation(
            pool=backend,
            memory=memory,
            bank_id=source,
            source_memory_ids=[uuid.UUID(str(first_ids[0]))],
            observation_text="Alice cycles.",
        )
        archive = b"".join(
            [chunk async for chunk in stream_export_bank(backend, source, scope=TransferScope(history=True))]
        )
        yield _BankArchive(source=source, data=archive)
    finally:
        await memory.delete_bank(source, request_context=request_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["observation", "document", "knowledge_page"])
async def test_bad_section_submit_leaves_no_bank_or_operation(
    memory, request_context, exported_bank, monkeypatch, section
):
    target = _unique_bank("atomic_rejected")
    monkeypatch.setattr(memory, "_operation_validator", None)
    try:
        with pytest.raises(ValueError):
            await memory.submit_bank_import_async(
                exported_bank.source, _bad_section(exported_bank.data, section), request_context, target_bank_id=target
            )
        backend = await memory._get_backend()
        assert await bank_utils.get_bank_profile_if_exists(backend, target) is None
        queued = await memory.list_operations(
            exported_bank.source, task_type="import_bank", limit=0, request_context=request_context
        )
        assert queued["total"] == 0
    finally:
        await memory.delete_bank(target, request_context=request_context)


@pytest.mark.asyncio
async def test_rollback_alone_removes_committed_document_batches(memory, request_context, exported_bank, monkeypatch):
    target = _unique_bank("atomic_rollback")
    committed: list[int] = []
    original_batch = importer._import_document_batch
    monkeypatch.setattr(importer, "validate_bank_archive", lambda archive, scope=None: None)
    monkeypatch.setattr(importer, "_DOCUMENT_BATCH_SIZE", 1)

    async def record_committed_batch(*, backend: Any, **kwargs: Any) -> Any:
        result = await original_batch(backend=backend, **kwargs)
        # A public read uses its own connection, so this observes earlier committed
        # batches between the restore's transactions, before rollback removes them.
        documents = await memory.list_documents(target, limit=0, request_context=request_context)
        committed.append(documents["total"])
        return result

    monkeypatch.setattr(importer, "_import_document_batch", record_committed_batch)
    try:
        with pytest.raises(ValueError, match="ttl_seconds"):
            await memory.import_bank_async(
                _bad_section(exported_bank.data, "document"), request_context, target_bank_id=target
            )
        assert committed and max(committed) >= 1, "barrier 2 must run after earlier document transactions committed"
        assert await bank_utils.get_bank_profile_if_exists(await memory._get_backend(), target) is None
    finally:
        await memory.delete_bank(target, request_context=request_context)


@pytest.mark.asyncio
async def test_invalid_streamed_value_passes_preflight_and_rolls_back(
    memory: MemoryEngine, request_context: RequestContext, exported_bank: _BankArchive
) -> None:
    target = _unique_bank("atomic_streamed_rollback")
    archive = _invalid_streamed_timestamp(exported_bank.data, exported_bank.source)
    importer.validate_bank_archive(archive)
    try:
        with pytest.raises(DataError, match="datetime"):
            await memory.import_bank_async(archive, request_context, target_bank_id=target)
        assert await bank_utils.get_bank_profile_if_exists(await memory._get_backend(), target) is None
    finally:
        await memory.delete_bank(target, request_context=request_context)


@pytest.mark.asyncio
async def test_delete_body_checks_identity_before_removing_bank_and_memories(
    memory: MemoryEngine, request_context: RequestContext
) -> None:
    bank = _unique_bank("atomic_delete_identity")
    try:
        unit_ids = await _retain(memory, bank, "Alice owns a bicycle.", request_context, "doc-identity")
        assert unit_ids
        backend = await memory._get_backend()
        # The profile helpers omit internal_id. Read only the bank row for the
        # identity token; verify the deletion's effects through public reads.
        async with acquire_with_retry(backend) as conn:
            internal_id = uuid.UUID(
                str(await conn.fetchval(f"SELECT internal_id FROM {fq_table('banks')} WHERE bank_id = $1", bank))
            )
        before = await memory.list_memory_units(bank, limit=100, request_context=request_context)
        assert before["total"] > 0
        refused = await memory._delete_bank_body(
            bank, expected_internal_id=uuid.uuid4(), request_context=request_context
        )
        assert refused.applied is False
        assert await bank_utils.get_bank_profile_if_exists(backend, bank) is not None
        after = await memory.list_memory_units(bank, limit=100, request_context=request_context)
        assert after["total"] == before["total"]
        assert {unit["id"] for unit in after["items"]} == {unit["id"] for unit in before["items"]}
        applied = await memory._delete_bank_body(
            bank, expected_internal_id=internal_id, request_context=request_context
        )
        assert applied.applied is True
        assert await bank_utils.get_bank_profile_if_exists(backend, bank) is None
    finally:
        await memory.delete_bank(bank, request_context=request_context)


@pytest.mark.asyncio
async def test_rollback_keeps_a_preexisting_bank(memory, request_context, exported_bank, monkeypatch):
    monkeypatch.setattr(importer, "validate_bank_archive", lambda archive, scope=None: None)
    delete = AsyncMock(wraps=memory._delete_bank_body)
    monkeypatch.setattr(memory, "_delete_bank_body", delete)
    with pytest.raises(ValueError, match="already exists"):
        await memory.import_bank_async(exported_bank.data, request_context, target_bank_id=exported_bank.source)
    delete.assert_not_awaited()
    assert await bank_utils.get_bank_profile_if_exists(await memory._get_backend(), exported_bank.source) is not None


@pytest.fixture
def unit_engine(monkeypatch: pytest.MonkeyPatch) -> MemoryEngine:
    """Stub the DB boundary, keeping the real engine submission and rollback methods."""
    engine = MemoryEngine.__new__(MemoryEngine)
    engine._operation_validator = None
    engine.embeddings = _FixtureEmbeddings()
    engine.entity_resolver = MagicMock()
    engine._config_resolver = MagicMock(resolve_full_config=AsyncMock())
    engine._file_storage = MagicMock(delete=AsyncMock())
    for name in (
        "_authenticate_tenant",
        "_refuse_bank_wide_if_scoped",
        "_get_backend",
        "_stash_archive",
        "_submit_async_operation",
        "_report_imported_documents",
    ):
        monkeypatch.setattr(engine, name, AsyncMock())
    monkeypatch.setattr(bank_utils, "get_bank_profile_if_exists", AsyncMock(return_value=None))
    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["observation", "document", "knowledge_page"])
async def test_submit_rejects_bad_section_before_stash_or_queue(unit_engine, request_context, section):
    with pytest.raises(ValueError):
        await unit_engine.submit_bank_import_async(
            "atomic-source",
            _bad_section(_synthetic_archive(), section),
            request_context,
            target_bank_id="atomic-target",
        )
    unit_engine._stash_archive.assert_not_awaited()
    unit_engine._submit_async_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_preflight_refuses_before_the_restore(unit_engine, request_context, monkeypatch):
    restore = AsyncMock()
    monkeypatch.setattr(transfer, "import_bank", restore)
    with pytest.raises(ValueError, match="ttl_seconds"):
        await unit_engine.import_bank_async(
            _bad_section(_synthetic_archive(), "document"), request_context, target_bank_id="atomic-target"
        )
    restore.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_id", [None, "00000000-0000-0000-0000-000000000001"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_rollback_orchestration_preserves_the_original_error(
    unit_engine, request_context, monkeypatch, operation_id, cleanup_fails
):
    monkeypatch.setattr(importer, "validate_bank_archive", lambda archive, scope=None: None)
    internal_id = uuid.uuid4()
    conn = MagicMock()
    conn.parse_json.side_effect = lambda value: value
    conn.fetchval = AsyncMock(side_effect=lambda query, *args: None if "result_metadata" in query else internal_id)
    conn.fetchrow = AsyncMock(return_value={"internal_id": internal_id})
    conn.execute = AsyncMock()

    @asynccontextmanager
    async def acquire(backend: Any) -> AsyncIterator[Any]:
        yield conn

    monkeypatch.setattr(engine_module, "acquire_with_retry", acquire)
    created_documents: list[str] = []
    raised: list[ValueError] = []

    async def replay(**kwargs: Any) -> None:
        await kwargs["on_bank_created"](conn)
        try:
            with zipfile.ZipFile(io.BytesIO(kwargs["archive"])) as zf:
                for document in importer._iter_documents(zf):
                    created_documents.append(document.id)
        except ValueError as error:
            raised.append(error)
            raise

    monkeypatch.setattr(transfer, "import_bank", replay)
    delete = AsyncMock(side_effect=RuntimeError("cleanup failed") if cleanup_fails else None)
    monkeypatch.setattr(unit_engine, "_delete_bank_body", delete)
    with pytest.raises(ValueError, match="ttl_seconds") as error:
        await unit_engine.import_bank_async(
            _bad_section(_synthetic_archive(), "document"),
            request_context,
            target_bank_id="atomic-target",
            operation_id=operation_id,
        )
    assert created_documents == ["doc-0", "doc-1", "doc-2"]
    assert error.value is raised[0]
    delete.assert_awaited_once_with("atomic-target", expected_internal_id=internal_id, request_context=request_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("marked", [False, True])
async def test_only_marked_stashed_tasks_skip_preflight(unit_engine, monkeypatch, marked):
    archive = _synthetic_archive()
    check = MagicMock(wraps=importer.validate_bank_archive)
    monkeypatch.setattr(importer, "validate_bank_archive", check)

    @asynccontextmanager
    async def retrieve(key: str) -> AsyncIterator[io.BytesIO]:
        yield io.BytesIO(archive)

    monkeypatch.setattr(unit_engine, "_retrieve_archive", retrieve)
    monkeypatch.setattr(
        transfer, "import_bank", AsyncMock(return_value=importer.BankImportResult(bank_id="atomic-target"))
    )
    task = {"storage_key": "archive.zip", "target_bank_id": "atomic-target"}
    if marked:
        task["archive_validated"] = True
    await unit_engine._handle_import_bank(task)
    assert check.call_count == (0 if marked else 1)


@pytest.mark.parametrize(
    "name",
    [
        *(f"data/{table}.json" for table in (*OPERATIONAL_TABLES, "invalidated_memory_units")),
        *(f"history/{table}.json" for table in HISTORY_TABLES),
    ],
)
@pytest.mark.parametrize("payload", [b'[{"bank_id":"atomic-source"},', b"[17]"])
def test_preflight_validates_each_streamed_row_section(name: str, payload: bytes) -> None:
    with pytest.raises(ValueError):
        importer.validate_bank_archive(_rewrite(_synthetic_archive(), name, payload))


def test_preflight_rejects_documents_only_manifest() -> None:
    archive = _rewrite(
        _synthetic_archive(),
        "manifest.json",
        TransferManifest(source_bank_id="atomic-source").model_dump_json().encode(),
    )
    with pytest.raises(ValueError, match="Not a whole-bank archive"):
        importer.validate_bank_archive(archive)


def test_preflight_opens_once_and_accepts_a_seekable_file(monkeypatch) -> None:
    open_archive = MagicMock(wraps=importer._open_archive)
    monkeypatch.setattr(importer, "_open_archive", open_archive)
    importer.validate_bank_archive(io.BytesIO(_synthetic_archive()))
    open_archive.assert_called_once()


def test_preflight_large_bank_stays_below_the_5737_streaming_bound(tmp_path, record_property) -> None:
    """The issue 5737 shape: 2000 x 50 KB log rows plus documents, never a whole list."""
    path = tmp_path / "large-bank.zip"
    with (
        zipfile.ZipFile(io.BytesIO(_synthetic_archive())) as source,
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for entry in source.infolist():
            target.writestr(entry, source.read(entry))
        for index in range(4, 2000):
            target.writestr(
                f"documents/{index:06d}.json",
                TransferDocument(
                    id=f"doc-{index}", facts=[TransferFact(text="A fact.", fact_type="world")]
                ).model_dump_json(),
            )
        with target.open("data/async_operations.json", "w") as entry:
            entry.write(b"[")
            for index in range(2000):
                if index:
                    entry.write(b",")
                entry.write(
                    json.dumps(
                        {"bank_id": "atomic-source", "status": "completed", "task_payload": {"content": "x" * 50_000}}
                    ).encode()
                )
            entry.write(b"]")
    tracemalloc.start()
    try:
        with path.open("rb") as archive:
            importer.validate_bank_archive(archive)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    record_property("archive_bytes", path.stat().st_size)
    record_property("preflight_peak_bytes", peak)
    assert peak < 100_000_000 / 2, f"issue 5737 bound: peak {peak} must be < log_bytes / 2 = 50,000,000 bytes"


@pytest.mark.asyncio
async def test_failure_before_on_bank_created_does_not_delete_existing_bank(unit_engine, request_context, monkeypatch):
    monkeypatch.setattr(importer, "validate_bank_archive", lambda archive, scope=None: None)
    monkeypatch.setattr(bank_utils, "get_bank_profile_if_exists", AsyncMock(return_value=object()))
    monkeypatch.setattr(transfer, "import_bank", AsyncMock(side_effect=ValueError("target already exists")))
    delete = AsyncMock()
    monkeypatch.setattr(unit_engine, "_delete_bank_body", delete)
    with pytest.raises(ValueError, match="already exists"):
        await unit_engine.import_bank_async(_synthetic_archive(), request_context, target_bank_id="atomic-target")
    delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, {"internal_id": uuid.uuid4()}])
async def test_rollback_never_deletes_a_missing_or_replaced_bank(unit_engine, request_context, monkeypatch, row):
    conn = MagicMock(fetchrow=AsyncMock(return_value=row), execute=AsyncMock())

    @asynccontextmanager
    async def transaction() -> AsyncIterator[None]:
        yield

    conn.transaction = transaction

    @asynccontextmanager
    async def acquire(backend: Any) -> AsyncIterator[Any]:
        yield conn

    monkeypatch.setattr(engine_module, "acquire_with_retry", acquire)
    files = AsyncMock()
    monkeypatch.setattr(unit_engine, "_delete_files_quietly", files)
    await unit_engine._discard_half_restored_bank("atomic-target", uuid.uuid4(), request_context)
    files.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_restore_also_rolls_back_its_own_bank(unit_engine, request_context, monkeypatch):
    internal_id = uuid.uuid4()
    conn = MagicMock(fetchval=AsyncMock(return_value=internal_id))
    rollback = AsyncMock()
    monkeypatch.setattr(unit_engine, "_discard_half_restored_bank", rollback)
    cancelled = asyncio.CancelledError()

    async def cancel_after_creation(**kwargs: Any) -> None:
        await kwargs["on_bank_created"](conn)
        raise cancelled

    monkeypatch.setattr(transfer, "import_bank", cancel_after_creation)
    with pytest.raises(asyncio.CancelledError) as error:
        await unit_engine.import_bank_async(_synthetic_archive(), request_context, target_bank_id="atomic-target")
    assert error.value is cancelled
    rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_lets_a_cancellation_during_cleanup_propagate(unit_engine, request_context, monkeypatch):
    """Cleanup swallows its own errors, but never a cancellation: asyncio needs it re-raised."""
    monkeypatch.setattr(importer, "validate_bank_archive", lambda archive, scope=None: None)
    internal_id = uuid.uuid4()
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=internal_id)
    conn.fetchrow = AsyncMock(return_value={"internal_id": internal_id})

    @asynccontextmanager
    async def acquire(backend: Any) -> AsyncIterator[Any]:
        yield conn

    monkeypatch.setattr(engine_module, "acquire_with_retry", acquire)
    restore_error = ValueError("bad section")

    async def fail_after_creation(**kwargs: Any) -> None:
        await kwargs["on_bank_created"](conn)
        raise restore_error

    monkeypatch.setattr(transfer, "import_bank", fail_after_creation)
    delete = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(unit_engine, "_delete_bank_body", delete)
    with pytest.raises(asyncio.CancelledError) as error:
        await unit_engine.import_bank_async(_synthetic_archive(), request_context, target_bank_id="atomic-target")
    assert error.value.__context__ is restore_error
    delete.assert_awaited_once_with("atomic-target", expected_internal_id=internal_id, request_context=request_context)


@pytest.mark.asyncio
async def test_submit_marks_only_archives_that_passed_preflight(unit_engine, request_context):
    await unit_engine.submit_bank_import_async(
        "atomic-source", _synthetic_archive(), request_context, target_bank_id="atomic-target"
    )
    payload = unit_engine._submit_async_operation.call_args.kwargs["task_payload"]
    assert payload["archive_validated"] is True


@pytest.mark.asyncio
async def test_bank_row_conflicts_are_never_silently_adopted():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"column_name": "bank_id", "data_type": "text"}])
    conn.executemany = AsyncMock()
    await importer._restore_rows(conn, "banks", [{"bank_id": "atomic-target"}])
    sql = conn.executemany.call_args.args[0]
    assert "ON CONFLICT DO NOTHING" not in sql, "a bank-row collision must fail before the ownership callback"
    await importer._restore_rows(conn, "directives", [{"bank_id": "atomic-target"}])
    assert "ON CONFLICT DO NOTHING" in conn.executemany.call_args.args[0]


@pytest.mark.parametrize(
    "name", ["banks.json", "directives.json", "webhooks.json", "mental_models.json", "mental_model_history.json"]
)
@pytest.mark.parametrize("payload", [b"{}", b"[17]"])
def test_preflight_rejects_bad_bank_level_rows(name: str, payload: bytes) -> None:
    with pytest.raises(ValueError):
        importer.validate_bank_archive(_rewrite(_synthetic_archive(), name, payload))


def test_preflight_checks_the_mental_model_id_used_during_restore() -> None:
    with pytest.raises(ValueError, match="has no id"):
        importer.validate_bank_archive(_rewrite(_synthetic_archive(), "mental_models.json", b'[{"name":"missing-id"}]'))


def test_preflight_reads_attachment_bytes_and_reports_bad_crc_as_a_value_error() -> None:
    from hindsight_api.engine.transfer.schema import TransferAttachment

    content = b"unique-atomic-attachment-content"
    attachment = TransferAttachment(
        bank_id="atomic-source",
        document_id="doc-0",
        attachment_hash=hashlib.sha256(content).hexdigest(),
        short_id="short",
        media_type="text/plain",
        byte_size=len(content),
        entry="blobs/0000",
    )
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(_synthetic_archive())) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as target,
    ):
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
        target.writestr("attachments.json", "[" + attachment.model_dump_json() + "]")
        target.writestr(attachment.entry, content)
    good = out.getvalue()
    importer.validate_bank_archive(good)
    damaged = bytearray(good)
    damaged[damaged.index(content)] ^= 1
    with pytest.raises(ValueError, match="corrupt"):
        importer.validate_bank_archive(bytes(damaged))


@pytest.mark.asyncio
async def test_concurrent_default_bank_creation_fails_before_the_ownership_callback(monkeypatch):
    conn = MagicMock(fetchval=AsyncMock(return_value=None))

    @asynccontextmanager
    async def transaction() -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def acquire(backend: Any) -> AsyncIterator[Any]:
        yield conn

    conn.transaction = transaction
    monkeypatch.setattr(importer, "acquire_with_retry", acquire)
    monkeypatch.setattr(bank_utils, "create_bank_row_on_conn", AsyncMock(return_value=False))
    callback = AsyncMock()
    with pytest.raises(ValueError, match="concurrent call"):
        await importer.import_bank(
            backend=object(),
            embeddings_model=_FixtureEmbeddings(),
            entity_resolver=MagicMock(),
            resolve_config=AsyncMock(),
            format_date_fn=str,
            archive=_synthetic_archive(),
            target_bank_id="atomic-target",
            scope=TransferScope(bank_config=False),
            ops=MagicMock(),
            on_bank_created=callback,
        )
    callback.assert_not_awaited()
