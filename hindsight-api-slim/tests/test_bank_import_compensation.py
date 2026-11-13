"""Identity-safe import compensation and restore-side stripping of its ownership markers."""

from __future__ import annotations

import ast
import inspect
import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine import memory_engine as engine_module
from hindsight_api.engine import transfer
from hindsight_api.engine.retain import bank_utils
from hindsight_api.engine.transfer import importer
from hindsight_api.engine.transfer.schema import BankRowsJSONEncoding
from hindsight_api.extensions.operation_validator import OperationValidationError, ValidationResult
from tests.test_bank_import_atomic import _rewrite, _stub_engine, _synthetic_archive


@pytest.fixture
def unit_engine(monkeypatch: pytest.MonkeyPatch) -> MemoryEngine:
    return _stub_engine(monkeypatch)


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
    assert "DELETE_BANK" in notes


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
    # Every mandatory keyword of the real batch function is named, not only `backend`:
    # pylint (E1125) cannot see through a `**kwargs` pass-through to a required keyword.
    required = {
        name
        for name, parameter in inspect.signature(importer._import_document_batch).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is inspect.Parameter.empty
    }
    assert required >= {"backend", "entity_resolver", "bank_id"}
    assert required <= {arg.arg for arg in wrapper.args.kwonlyargs}
    call = next(
        node
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "original_batch"
    )
    assert required <= {keyword.arg for keyword in call.keywords}
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


def test_server_owned_operation_metadata_keys_match_retry_fields() -> None:
    """The importer must strip every ownership field the retry trusts."""
    assert importer._SERVER_OWNED_OPERATION_METADATA_KEYS == set(engine_module._BankRestoreMetadata.model_fields)


@pytest.mark.parametrize(
    "encoding,value,expected",
    [
        pytest.param(
            "decoded",
            {"restored_bank_id": "victim", "restored_internal_id": "forged", "error": "boom"},
            {"error": "boom"},
            id="decoded-object",
        ),
        pytest.param(
            "serialized",
            json.dumps({"restored_bank_id": "victim", "restored_internal_id": "forged", "error": "boom"}),
            {"error": "boom"},
            id="serialized-object",
        ),
        pytest.param("serialized", '"text"', {}, id="serialized-string-scalar"),
        pytest.param("serialized", "null", {}, id="serialized-null"),
        pytest.param("decoded", ["x"], {}, id="decoded-list"),
        pytest.param("decoded", "text", {}, id="decoded-string"),
        pytest.param("decoded", None, None, id="decoded-none"),
        pytest.param("serialized", "not json", "not json", id="invalid-json-unchanged"),
    ],
)
def test_operation_metadata_strips_server_owned_fields(
    encoding: BankRowsJSONEncoding, value: Any, expected: Any
) -> None:
    row = {"operation_type": "import_bank", "result_metadata": value}
    rows = list(importer._without_server_owned_metadata([row], bank_rows_json_encoding=encoding))
    assert rows == [{"operation_type": "import_bank", "result_metadata": expected}]


def test_operation_without_result_metadata_is_unchanged() -> None:
    row = {"operation_type": "import_bank"}
    rows = list(importer._without_server_owned_metadata([row], bank_rows_json_encoding="decoded"))
    assert rows == [{"operation_type": "import_bank"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["decoded", "serialized"])
async def test_restore_strips_forged_operation_ownership(
    monkeypatch: pytest.MonkeyPatch, encoding: BankRowsJSONEncoding
) -> None:
    operation_id = str(uuid.uuid4())
    metadata = {"restored_bank_id": "victim", "restored_internal_id": str(uuid.uuid4()), "error": "boom"}
    row = {
        "operation_id": operation_id,
        "bank_id": "foreign",
        "operation_type": "import_bank",
        "status": "failed",
        "result_metadata": json.dumps(metadata) if encoding == "serialized" else metadata,
    }
    archive = _rewrite(_synthetic_archive(), "data/async_operations.json", json.dumps([row]).encode())
    written: list[tuple[str, list[dict[str, Any]]]] = []

    async def restore_rows(conn: Any, table: str, rows: Iterable[dict], **kwargs: Any) -> int:
        restored = list(rows)
        written.append((table, restored))
        return len(restored)

    monkeypatch.setattr(importer, "_restore_rows", restore_rows)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        await importer._restore_operational_rows(
            MagicMock(),
            zf,
            "target-bank",
            drop_operation_ids=False,
            unit_id_map={},
            bank_rows_json_encoding=encoding,
        )
    operations = next(rows for table, rows in written if table == "async_operations")
    assert operations == [
        {
            "operation_id": operation_id,
            "bank_id": "target-bank",
            "operation_type": "import_bank",
            "status": "failed",
            "result_metadata": {"error": "boom"},
        }
    ]
