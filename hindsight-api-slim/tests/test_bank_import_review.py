"""Independent preflight/restore barriers and identity-safe import compensation."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import struct
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine import memory_engine as engine_module
from hindsight_api.engine import transfer
from hindsight_api.engine.retain import bank_utils
from hindsight_api.engine.transfer import TransferScope, importer
from hindsight_api.engine.transfer.schema import TransferAttachment, TransferManifest, TransferScopeManifest
from hindsight_api.extensions.operation_validator import OperationValidationError, ValidationResult
from tests.test_bank_import_atomic import _invalid_streamed_timestamp, _rewrite, _synthetic_archive
from tests.test_bank_import_atomic import unit_engine as unit_engine


def _with_history(archive: bytes) -> bytes:
    return _rewrite(
        archive,
        "manifest.json",
        TransferManifest(source_bank_id="atomic-source", archive_type="bank", includes_history=True)
        .model_dump_json()
        .encode(),
    )


@pytest.mark.parametrize(
    "section,payload",
    [
        ("banks.json", b'[{"bank_id":"other"}]'),
        ("banks.json", b'[{"bank_id":"atomic-source"},{"bank_id":"other"}]'),
        ("banks.json", b"[]"),
        ("banks.json", b'[{"bank_id":"atomic-source"},{"bank_id":"atomic-source"}]'),
        ("directives.json", b'[{"bank_id":"other"}]'),
        ("webhooks.json", b'[{"bank_id":"other"}]'),
        ("mental_models.json", b'[{"bank_id":"other","id":"mm"}]'),
        ("mental_model_history.json", b'[{"bank_id":"other"}]'),
        ("knowledge_pages.json", b'[{"id":"folder","kind":"folder","name":"Folder","bank_id":"other"}]'),
        ("data/async_operations.json", b'[{"bank_id":"other","status":"completed"}]'),
        ("data/graph_maintenance_queue.json", b'[{"bank_id":"other"}]'),
        ("data/entity_maintenance_queue.json", b'[{"bank_id":"other"}]'),
        ("data/invalidated_memory_units.json", b'[{"bank_id":"other"}]'),
        ("history/audit_log.json", b'[{"bank_id":"other"}]'),
        ("history/llm_requests.json", b'[{"bank_id":"other"}]'),
        ("documents/000000.json", b'{"id":"doc","bank_id":"other","facts":[]}'),
        ("observations.json", b'[{"text":"An observation","bank_id":"other"}]'),
        (
            "attachments.json",
            b'[{"bank_id":"other","document_id":"doc","attachment_hash":"hash","short_id":"s","media_type":"text/plain","byte_size":0,"entry":"banks.json"}]',
        ),
    ],
)
def test_preflight_refuses_foreign_bank_rows(section: str, payload: bytes) -> None:
    with pytest.raises(ValueError, match=section.replace(".json", "")):
        importer.validate_bank_archive(_rewrite(_with_history(_synthetic_archive()), section, payload))


@pytest.mark.parametrize(
    "section", ["history/audit_log.json", "mental_models.json", "banks.json", "documents/000000.json"]
)
def test_preflight_only_reads_requested_sections(section: str) -> None:
    archive = _rewrite(_with_history(_synthetic_archive()), section, b"broken JSON")
    if section.startswith("history/"):
        excluded = TransferScope(history=False)
        included = TransferScope(history=True)
    elif section == "banks.json":
        excluded = TransferScope(data=True, bank_config=False)
        included = TransferScope()
    else:
        excluded = TransferScope(data=False, bank_config=True)
        included = TransferScope()
    importer.validate_bank_archive(archive, excluded)
    # History rows are streamed and fail on shape; the other sections are parsed whole.
    if section.startswith("history/"):
        refusal = pytest.raises(ValueError, match=f"{section} is not a JSON array")
    else:
        refusal = pytest.raises(json.JSONDecodeError, match="Expecting value")
    with refusal:
        importer.validate_bank_archive(archive, included)


def test_preflight_intersects_requested_and_manifest_scopes() -> None:
    manifest = TransferManifest(
        source_bank_id="atomic-source", archive_type="bank", scope=TransferScopeManifest(history=False)
    )
    archive = _rewrite(_synthetic_archive(), "manifest.json", manifest.model_dump_json().encode())
    archive = _rewrite(archive, "history/audit_log.json", b"corrupt")
    importer.validate_bank_archive(archive, TransferScope(history=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["atomic-source", "renamed-target"])
async def test_restore_rewrites_bank_ids_even_when_preflight_is_bypassed(
    unit_engine: MemoryEngine, request_context: RequestContext, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """Exercise the actual restore, including same-source imports, with a hostile bank row."""
    archive = _rewrite(_with_history(_synthetic_archive()), "banks.json", b'[{"bank_id":"foreign"}]')
    archive = _rewrite(archive, "directives.json", b'[{"bank_id":"foreign","id":"directive"}]')
    archive = _rewrite(archive, "webhooks.json", b'[{"bank_id":"foreign","id":"webhook"}]')
    archive = _rewrite(archive, "mental_models.json", b'[{"bank_id":"foreign","id":"model"}]')
    archive = _rewrite(archive, "mental_model_history.json", b'[{"bank_id":"foreign","mental_model_id":"model"}]')
    archive = _rewrite(archive, "history/audit_log.json", b'[{"bank_id":"foreign","id":7}]')
    archive = _rewrite(archive, "data/async_operations.json", b'[{"bank_id":"foreign","status":"completed"}]')
    internal_id = uuid.uuid4()
    conn = MagicMock(
        fetchval=AsyncMock(side_effect=lambda query, *args: internal_id if "SELECT internal_id" in query else None)
    )
    monkeypatch.setattr(bank_utils, "create_bank_vector_indexes", AsyncMock())

    @asynccontextmanager
    async def transaction() -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def acquire(backend: Any) -> AsyncIterator[Any]:
        yield conn

    conn.transaction = transaction
    monkeypatch.setattr(importer, "acquire_with_retry", acquire)
    monkeypatch.setattr(importer, "_replay_documents", AsyncMock(return_value=importer.ImportResult()))
    monkeypatch.setattr(importer, "_apply_mental_model_derived_state", AsyncMock())
    monkeypatch.setattr(importer, "_regenerate_mental_model_embeddings", AsyncMock(return_value={}))
    monkeypatch.setattr(importer, "_index_restored_pages", AsyncMock(return_value=0))
    written: list[str] = []
    audit_ids: list[int] = []

    async def restore_rows(conn: Any, table: str, rows: Any, **kwargs: Any) -> int:
        rows = list(rows)
        written.extend(row["bank_id"] for row in rows)
        if table == "audit_log":
            audit_ids.extend(row["id"] for row in rows if "id" in row)
        return len(rows)

    monkeypatch.setattr(importer, "_restore_rows", restore_rows)
    await unit_engine.import_bank_async(
        archive, request_context, target_bank_id=target, scope=TransferScope(history=True), _archive_validated=True
    )
    assert len(written) >= 4 and set(written) == {target}
    assert audit_ids == ([7] if target == "atomic-source" else [])


def _attachment_archive(
    *, content: bytes = b"actual", declared_content: bytes = b"actual", byte_size: int = 6
) -> bytes:
    attachment = TransferAttachment(
        bank_id="atomic-source",
        document_id="doc-0",
        attachment_hash=hashlib.sha256(declared_content).hexdigest(),
        short_id="short",
        media_type="text/plain",
        byte_size=byte_size,
        entry="blobs/0000",
    )
    archive = _rewrite(_synthetic_archive(), "attachments.json", ("[" + attachment.model_dump_json() + "]").encode())
    return _rewrite(archive, attachment.entry, content)


@pytest.mark.parametrize("defect", ["hash", "size"])
def test_preflight_checks_attachment_integrity_beyond_crc(defect: str) -> None:
    archive = _attachment_archive(
        declared_content=b"tamper" if defect == "hash" else b"actual", byte_size=7 if defect == "size" else 6
    )
    with pytest.raises(ValueError, match="attachment"):
        importer.validate_bank_archive(archive)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["hash", "size"])
async def test_restore_checks_attachment_before_storing(defect: str) -> None:
    archive = _attachment_archive(
        declared_content=b"tamper" if defect == "hash" else b"actual", byte_size=7 if defect == "size" else 6
    )
    store = MagicMock(store=AsyncMock())
    conn = MagicMock(execute=AsyncMock())
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        parsed = importer.parse_bank_archive(zf)
        with pytest.raises(ValueError, match="attachment"):
            await importer._restore_attachments(conn, "target", parsed.attachments, zf, store, {})
    store.store.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("[1 2]", "missing a comma"),
        ("[1,,2]", "unexpected separator"),
        ("[,1]", "unexpected separator"),
        ("[1,]", "unexpected separator"),
        ("[] false", "data after the JSON array"),
        ("[] ]", "data after the JSON array"),
        # Only the trailing-data check refuses this one: without it the row after
        # the first ']' is decoded and the second ']' closes the array again.
        ("[] {}]", "data after the JSON array"),
    ],
)
@pytest.mark.parametrize("read_chars", [1, 2, 1 << 20])
def test_streaming_json_rejects_invalid_separators_and_trailing_data(
    monkeypatch: pytest.MonkeyPatch, payload: str, error: str, read_chars: int
) -> None:
    monkeypatch.setattr(importer, "_JSON_READ_CHARS", read_chars)
    archive = _rewrite(_synthetic_archive(), "rows.json", payload.encode())
    with zipfile.ZipFile(io.BytesIO(archive)) as zf, pytest.raises(ValueError, match=error):
        list(importer._iter_json_array(zf, "rows.json"))


@pytest.mark.parametrize("payload", ["[]", "[ ]", '[{"a":[1,{"b":2}]},"comma, bracket]",1.5e2]'])
def test_streaming_json_accepts_valid_arrays(payload: str) -> None:
    archive = _rewrite(_synthetic_archive(), "rows.json", payload.encode())
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert list(importer._iter_json_array(zf, "rows.json")) == json.loads(payload)


def test_streaming_json_reads_through_the_closing_bracket_to_verify_crc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importer, "_JSON_READ_CHARS", 1)
    out = io.BytesIO()
    payload = b"[]" + b" " * 20_000
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("rows.json", payload)
    damaged = bytearray(out.getvalue())
    damaged[damaged.index(payload) + len(payload) - 1] ^= 1
    with zipfile.ZipFile(io.BytesIO(damaged)) as zf, pytest.raises(zipfile.BadZipFile):
        list(importer._iter_json_array(zf, "rows.json"))


def _unsupported_zip(archive: bytes, defect: str) -> bytes:
    damaged = bytearray(archive)
    # Set both local and central metadata; leave payload and CRC unchanged.
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        info = zf.getinfo("banks.json")
        local = info.header_offset
        central = zf.start_dir
        while damaged[central : central + 4] == b"PK\x01\x02":
            name_length, extra_length, comment_length = struct.unpack_from("<HHH", damaged, central + 28)
            name = damaged[central + 46 : central + 46 + name_length].decode()
            if name == "banks.json":
                break
            central += 46 + name_length + extra_length + comment_length
        else:
            raise AssertionError("banks.json central entry missing")
    for offset in (local + (6 if defect == "encrypted" else 8), central + (8 if defect == "encrypted" else 10)):
        struct.pack_into("<H", damaged, offset, 1 if defect == "encrypted" else 99)
    return bytes(damaged)


@pytest.mark.parametrize("defect", ["encrypted", "compression"])
def test_preflight_refuses_unsupported_entries_up_front(monkeypatch: pytest.MonkeyPatch, defect: str) -> None:
    archive = _unsupported_zip(_synthetic_archive(), defect)
    read = MagicMock(side_effect=AssertionError("must refuse before any entry is read"))
    monkeypatch.setattr(zipfile.ZipFile, "open", read)
    refusal = "encrypted entry 'banks.json'" if defect == "encrypted" else "unsupported compression for 'banks.json'"
    with pytest.raises(ValueError, match=refusal):
        importer.validate_bank_archive(archive)
    read.assert_not_called()


@pytest.mark.parametrize("defect", ["encrypted", "compression"])
@pytest.mark.asyncio
async def test_route_maps_unsupported_zip_to_400(
    unit_engine: MemoryEngine, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    # Use the real route and submission/preflight path, with only the DB boundary
    # stubbed. Unsupported ZIP metadata must be rejected before any persistence.
    import httpx

    from hindsight_api.api import create_app

    unit_engine._audit_logger = None
    monkeypatch.setattr(unit_engine, "resolve_bank_alias", AsyncMock(side_effect=lambda bank_id, **kwargs: bank_id))
    app = create_app(unit_engine, initialize_memory=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/default/banks/atomic-source/transfer/import",
            files={"file": ("bank.zip", _unsupported_zip(_synthetic_archive(), defect), "application/zip")},
            params={"target_bank_id": "atomic-target"},
        )
    assert response.status_code == 400, response.text
    unit_engine._stash_archive.assert_not_awaited()
    unit_engine._submit_async_operation.assert_not_awaited()


def test_preflight_maps_unimplemented_zip_reads_to_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zipfile.ZipFile, "read", MagicMock(side_effect=NotImplementedError("unsupported codec")))
    with pytest.raises(ValueError, match="corrupt"):
        importer.validate_bank_archive(_synthetic_archive())


@pytest.mark.parametrize(
    "limit", ["transfer_import_max_entry_size_mb", "transfer_import_max_total_size_mb", "transfer_import_max_entries"]
)
@pytest.mark.parametrize("path", ["preflight", "restore"])
@pytest.mark.asyncio
async def test_archive_limits_precede_all_entry_reads(monkeypatch: pytest.MonkeyPatch, limit: str, path: str) -> None:
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    monkeypatch.setattr(config, limit, 0)
    monkeypatch.setattr(importer, "get_config", lambda: config, raising=False)
    archive = _synthetic_archive()
    read = MagicMock(side_effect=AssertionError("limit must be checked before reads"))
    monkeypatch.setattr(zipfile.ZipFile, "open", read)
    with pytest.raises(ValueError, match="limit"):
        if path == "preflight":
            importer.validate_bank_archive(archive)
        else:
            await importer.import_bank(
                archive=archive,
                backend=MagicMock(),
                embeddings_model=MagicMock(),
                entity_resolver=MagicMock(),
                resolve_config=AsyncMock(),
                format_date_fn=str,
            )
    read.assert_not_called()


def test_preflight_accepts_invalid_streamed_timestamp() -> None:
    # This is the same invalid DB value as the real rollback test, verified here
    # independently of pg0 so a stricter preflight cannot make that test vacuous.
    importer.validate_bank_archive(_invalid_streamed_timestamp(_synthetic_archive(), "atomic-source"))


@dataclass
class _StreamedOperationRow:
    error_message: str
    bank_id: str = "atomic-source"
    operation_type: str = "retain"
    status: str = "failed"


@pytest.mark.parametrize("read_chars", [64 * 1024, 4 * 1024**2])
def test_streamed_item_size_is_bounded(monkeypatch: pytest.MonkeyPatch, read_chars: int) -> None:
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    config.transfer_import_max_entry_size_mb = 1
    monkeypatch.setattr(importer, "get_config", lambda: config)
    # Exercise configured read chunks both below and above the cap; the parser
    # previously allowed a large chunk to decode an oversized item in one read.
    monkeypatch.setattr(importer, "_JSON_READ_CHARS", read_chars)
    name = "data/async_operations.json"
    row = _StreamedOperationRow(error_message="x" * 1024**2)
    archive = _rewrite(_synthetic_archive(), name, json.dumps([asdict(row)]).encode())
    with pytest.raises(ValueError, match=f"{name} has an item larger than the entry size limit"):
        importer.validate_bank_archive(archive)


def test_streamed_items_below_cap_can_exceed_it_in_total(monkeypatch: pytest.MonkeyPatch) -> None:
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    config.transfer_import_max_entry_size_mb = 1
    monkeypatch.setattr(importer, "get_config", lambda: config)
    # Small rows must pass even when the combined section exceeds the whole-read
    # entry cap, because the section is decoded one row at a time.
    rows = [_StreamedOperationRow(error_message="x" * (512 * 1024)) for _ in range(3)]
    payload = json.dumps([asdict(row) for row in rows]).encode()
    assert len(payload) > 1024**2
    importer.validate_bank_archive(_rewrite(_synthetic_archive(), "data/async_operations.json", payload))


@pytest.mark.asyncio
async def test_document_import_entry_limit_precedes_all_entry_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    config.transfer_import_max_entry_size_mb = 1
    monkeypatch.setattr(importer, "get_config", lambda: config)
    archive = _rewrite(
        _synthetic_archive(),
        "manifest.json",
        TransferManifest(source_bank_id="atomic-source", archive_type="documents").model_dump_json().encode(),
    )
    archive = _rewrite(archive, "documents/000000.json", b" " * (1024**2 + 1))
    read = MagicMock(side_effect=AssertionError("limit must precede any entry read"))
    monkeypatch.setattr(zipfile.ZipFile, "open", read)
    with pytest.raises(ValueError, match="entry size limit"):
        await importer.import_documents(
            archive=archive,
            bank_id="document-target",
            backend=MagicMock(),
            embeddings_model=MagicMock(),
            entity_resolver=MagicMock(),
            config=config,
            format_date_fn=str,
        )
    read.assert_not_called()


@pytest.fixture
def deletion_connection(unit_engine: MemoryEngine, monkeypatch: pytest.MonkeyPatch) -> Any:
    conn = MagicMock(execute=AsyncMock(), fetchrow=AsyncMock(), fetchval=AsyncMock(return_value=None))
    conn.parse_json.side_effect = lambda value: value
    committed: list[bool] = []

    @asynccontextmanager
    async def transaction() -> AsyncIterator[None]:
        try:
            yield
        except BaseException:
            raise
        else:
            committed.append(True)

    @asynccontextmanager
    async def acquire(backend: Any) -> AsyncIterator[Any]:
        yield conn

    conn.transaction = transaction
    conn.committed = committed
    monkeypatch.setattr(engine_module, "acquire_with_retry", acquire)
    monkeypatch.setattr(unit_engine, "_delete_files_quietly", AsyncMock())
    from hindsight_api.engine import bank_info_cache, memories
    from hindsight_api.engine.memories.base import BankContentCounts

    store = MagicMock(
        store_owned_for=MagicMock(return_value=False),
        count_bank_contents=AsyncMock(return_value=BankContentCounts()),
        purge_bank_rows=AsyncMock(return_value=[]),
    )
    conn.store = store
    monkeypatch.setattr(memories, "get_memories", lambda: store)
    monkeypatch.setattr(bank_info_cache, "invalidate", AsyncMock())
    monkeypatch.setattr(bank_utils, "drop_bank_vector_indexes", AsyncMock())
    unit_engine._backend = MagicMock()
    unit_engine._tenant_extension = None
    unit_engine._bank_stats_cache = MagicMock(invalidate=AsyncMock())
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [False, True])
async def test_identity_mismatch_rolls_back_and_skips_all_sweeps(
    unit_engine: MemoryEngine, deletion_connection: Any, request_context: RequestContext, present: bool
) -> None:
    conn = deletion_connection
    conn.fetchrow.return_value = {"internal_id": uuid.uuid4(), "bank_id": "atomic-target"} if present else None
    outcome = await unit_engine._delete_bank_body(
        "atomic-target", expected_internal_id=uuid.uuid4(), request_context=request_context
    )
    assert outcome.applied is False
    assert conn.committed == []
    assert all("DELETE" not in call.args[0] for call in conn.execute.await_args_list)
    conn.store.count_bank_contents.assert_not_awaited()
    conn.store.purge_bank_rows.assert_not_awaited()
    conn.store.drop_bank_storage.assert_not_called()
    unit_engine._delete_files_quietly.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["replaced", "legacy"])
async def test_retry_preserves_recreated_bank_and_legacy_metadata(
    unit_engine: MemoryEngine,
    deletion_connection: Any,
    request_context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    original_id = uuid.uuid4()
    conn = deletion_connection
    metadata = {"restored_bank_id": "atomic-target"}
    if identity != "legacy":
        metadata["restored_internal_id"] = str(original_id)
    conn.fetchval = AsyncMock(return_value=metadata)
    conn.fetchrow.return_value = {"internal_id": uuid.uuid4(), "bank_id": "atomic-target"}
    monkeypatch.setattr(bank_utils, "get_bank_profile_if_exists", AsyncMock(return_value=object()))
    monkeypatch.setattr(transfer, "import_bank", AsyncMock(side_effect=ValueError("target already exists")))
    public_delete = AsyncMock()
    monkeypatch.setattr(unit_engine, "delete_bank", public_delete)
    with pytest.raises(ValueError, match="already exists"):
        await unit_engine.import_bank_async(
            _synthetic_archive(), request_context, target_bank_id="atomic-target", operation_id=str(uuid.uuid4())
        )
    public_delete.assert_not_awaited()
    assert conn.committed == []
    assert all("DELETE" not in call.args[0] for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_cleanup_failure_retains_original_error_and_recovery_note(
    unit_engine: MemoryEngine, request_context: RequestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ValueError("original failure")
    internal_id = uuid.uuid4()
    conn = MagicMock(fetchval=AsyncMock(return_value=internal_id))

    async def fail(**kwargs: Any) -> None:
        await kwargs["on_bank_created"](conn)
        raise original

    monkeypatch.setattr(transfer, "import_bank", fail)
    monkeypatch.setattr(
        unit_engine, "_discard_half_restored_bank", AsyncMock(side_effect=RuntimeError("cleanup failure"))
    )
    with pytest.raises(ValueError, match="original failure") as error:
        await unit_engine.import_bank_async(_synthetic_archive(), request_context, target_bank_id="atomic-target")
    assert error.value is original
    notes = " ".join(error.value.__notes__)
    assert "atomic-target" in notes
    assert "hindsight bank delete atomic-target" in notes
    assert "DELETE /v1/default/banks/atomic-target" in notes


@pytest.mark.asyncio
async def test_creation_metadata_records_identity_before_update(
    unit_engine: MemoryEngine,
    deletion_connection: Any,
    request_context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = deletion_connection
    internal_id = uuid.uuid4()
    conn.fetchval = AsyncMock(side_effect=[None, internal_id])

    async def replay(**kwargs: Any) -> importer.BankImportResult:
        await kwargs["on_bank_created"](conn)
        return importer.BankImportResult(bank_id="atomic-target")

    monkeypatch.setattr(transfer, "import_bank", replay)
    await unit_engine.import_bank_async(
        _synthetic_archive(), request_context, target_bank_id="atomic-target", operation_id=str(uuid.uuid4())
    )
    update = conn.execute.await_args
    assert json.loads(update.args[1]) == {"restored_bank_id": "atomic-target", "restored_internal_id": str(internal_id)}
    calls = [call[0] for call in conn.mock_calls]
    assert calls.index("fetchval", calls.index("fetchval") + 1) < calls.index("execute")


@pytest.mark.asyncio
async def test_compensation_bypasses_delete_validator_but_public_delete_does_not(
    unit_engine: MemoryEngine,
    deletion_connection: Any,
    request_context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = MagicMock(
        validate_create_bank=AsyncMock(return_value=ValidationResult.accept()),
        validate_bank_write=AsyncMock(return_value=ValidationResult.reject("delete denied")),
    )
    unit_engine._operation_validator = validator
    internal_id = uuid.uuid4()
    conn = deletion_connection
    conn.fetchval = AsyncMock(return_value=internal_id)
    conn.fetchrow.return_value = {"bank_id": "atomic-target", "internal_id": internal_id}

    async def replay(**kwargs: Any) -> None:
        await kwargs["on_bank_created"](conn)
        raise ValueError("late restore failure")

    monkeypatch.setattr(transfer, "import_bank", replay)
    with pytest.raises(ValueError, match="late restore failure"):
        await unit_engine.import_bank_async(_synthetic_archive(), request_context, target_bank_id="atomic-target")
    conn.store.purge_bank_rows.assert_awaited_once()
    assert conn.committed == [True]
    validator.validate_create_bank.assert_awaited_once()
    validator.validate_bank_write.assert_not_awaited()
    with pytest.raises(OperationValidationError, match="delete denied"):
        await unit_engine.delete_bank("atomic-target", request_context=request_context)
    conn.store.purge_bank_rows.assert_awaited_once()


def test_atomic_batch_wrapper_passes_backend_explicitly_and_uses_public_counts() -> None:
    """The wrapper must remain statically callable and counts must use engine read APIs."""
    module = ast.parse(Path(__file__).with_name("test_bank_import_atomic.py").read_text())
    wrapper = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "record_committed_batch"
    )
    assert "backend" in [arg.arg for arg in wrapper.args.kwonlyargs]
    call = next(
        node
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "original_batch"
    )
    assert "backend" in [keyword.arg for keyword in call.keywords]
    for name in ("test_bad_section_submit_leaves_no_bank_or_operation", "record_committed_batch"):
        function = next(
            node for node in ast.walk(module) if isinstance(node, ast.AsyncFunctionDef) and node.name == name
        )
        assert not any(isinstance(node, ast.Attribute) and node.attr == "fetchval" for node in ast.walk(function))


@pytest.mark.asyncio
async def test_replacement_between_restore_failure_and_delete_lock_is_left_intact(
    unit_engine: MemoryEngine,
    deletion_connection: Any,
    request_context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old unlocked pre-check saw the old row; deletion then saw its replacement."""
    original_id, replacement_id = uuid.uuid4(), uuid.uuid4()
    conn = deletion_connection
    conn.fetchval = AsyncMock(return_value=original_id)

    async def row_at_read(query: str, *args: Any) -> Any:
        # Simulate replacement after an unlocked ownership check, before deletion.
        return {"internal_id": replacement_id if "FOR NO KEY UPDATE" in query else original_id}

    conn.fetchrow.side_effect = row_at_read
    public_delete = AsyncMock()
    monkeypatch.setattr(unit_engine, "delete_bank", public_delete)

    async def fail_after_creation(**kwargs: Any) -> None:
        await kwargs["on_bank_created"](conn)
        raise ValueError("late failure after bank replacement")

    monkeypatch.setattr(transfer, "import_bank", fail_after_creation)
    with pytest.raises(ValueError, match="late failure"):
        await unit_engine.import_bank_async(_synthetic_archive(), request_context, target_bank_id="atomic-target")
    public_delete.assert_not_awaited()
    unit_engine._delete_files_quietly.assert_not_awaited()
    assert conn.committed == []
    locked_reads = [call.args[0] for call in conn.fetchrow.await_args_list]
    assert locked_reads and all("FOR NO KEY UPDATE" in query for query in locked_reads)


@pytest.mark.parametrize("fact_type,delete_profile", [(None, True), (None, False), ("world", False)])
@pytest.mark.asyncio
async def test_public_delete_preserves_counts_and_cleanup_modes(
    unit_engine: MemoryEngine,
    deletion_connection: Any,
    request_context: RequestContext,
    monkeypatch: pytest.MonkeyPatch,
    fact_type: str | None,
    delete_profile: bool,
) -> None:
    from hindsight_api.engine.memories.base import TypedMemoryScope

    conn = deletion_connection
    conn.fetchrow.return_value = {"bank_id": "atomic-target", "internal_id": uuid.uuid4()}
    conn.store.bank_memories_of_type = AsyncMock(return_value=TypedMemoryScope())
    conn.store.delete_bank_memories_of_type = AsyncMock()
    monkeypatch.setattr(unit_engine, "_submit_vector_index_maintenance_quietly", AsyncMock())
    result = await unit_engine.delete_bank(
        "atomic-target", fact_type=fact_type, delete_bank_profile=delete_profile, request_context=request_context
    )
    expected = {"memory_units_deleted": 0, "entities_deleted": 0}
    if fact_type is None:
        expected["documents_deleted"] = 0
        if delete_profile:
            expected["bank_deleted"] = True
    assert result == expected
    assert conn.committed == [True]
    assert unit_engine._delete_files_quietly.await_count == (0 if fact_type else 1)
    assert unit_engine._submit_vector_index_maintenance_quietly.await_count == (0 if delete_profile else 1)


@pytest.mark.asyncio
async def test_streamed_entry_used_as_attachment_still_has_a_whole_read_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from hindsight_api.config import HindsightConfig

    # Attachment entry names are opaque; a reference to a streamed array must not
    # bypass the whole-read cap when restore joins the blob for the storage API.
    content = b"[]" + b" " * (1024**2)
    attachment = TransferAttachment(
        bank_id="atomic-source",
        document_id="doc-0",
        attachment_hash=hashlib.sha256(content).hexdigest(),
        short_id="large",
        media_type="text/plain",
        byte_size=len(content),
        entry="data/async_operations.json",
    )
    archive = _rewrite(_synthetic_archive(), attachment.entry, content)
    config = HindsightConfig.from_env()
    config.transfer_import_max_entry_size_mb = 1
    monkeypatch.setattr(importer, "get_config", lambda: config)
    store = MagicMock(store=AsyncMock())
    with importer._open_archive(archive, produced_by="test export") as zf, pytest.raises(ValueError, match="limit"):
        await importer._restore_attachments(MagicMock(), "target", [attachment], zf, store, {})
    store.store.assert_not_awaited()
