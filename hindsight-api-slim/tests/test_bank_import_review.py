"""Independent preflight/restore barriers and identity-safe import compensation."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import uuid
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api import MemoryEngine, RequestContext
from hindsight_api.engine.retain import bank_utils
from hindsight_api.engine.transfer import TransferScope, importer
from hindsight_api.engine.transfer.schema import TransferAttachment, TransferManifest, TransferScopeManifest
from tests.test_bank_import_atomic import (
    _invalid_streamed_timestamp,
    _rewrite,
    _stub_connection,
    _stub_engine,
    _synthetic_archive,
)

if TYPE_CHECKING:
    from hindsight_api.config import HindsightConfig


@pytest.fixture
def unit_engine(monkeypatch: pytest.MonkeyPatch) -> MemoryEngine:
    return _stub_engine(monkeypatch)


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
    _stub_connection(monkeypatch, importer, conn)
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
    # Build first because writestr calls ZipFile.open, which this test patches.
    archive = _synthetic_archive()
    monkeypatch.setattr(zipfile.ZipFile, "open", MagicMock(side_effect=NotImplementedError("unsupported codec")))
    with pytest.raises(ValueError, match="corrupt"):
        importer.validate_bank_archive(archive)


async def _run_barrier(barrier: str, archive: bytes) -> None:
    """Pass ``archive`` through preflight, or through the import for any other ``barrier``."""
    if barrier == "preflight":
        importer.validate_bank_archive(archive)
        return
    await importer.import_bank(
        archive=archive,
        backend=MagicMock(),
        embeddings_model=MagicMock(),
        entity_resolver=MagicMock(),
        resolve_config=AsyncMock(),
        format_date_fn=str,
    )


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
        await _run_barrier(path, archive)
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


def _multibyte_operations(monkeypatch: pytest.MonkeyPatch, read_chars: int, chars: int) -> str:
    """Set a 1 MiB item limit and return one async_operations row of ``chars`` euro signs as JSON."""
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    config.transfer_import_max_entry_size_mb = 1
    monkeypatch.setattr(importer, "get_config", lambda: config)
    monkeypatch.setattr(importer, "_JSON_READ_CHARS", read_chars)
    # "€" is one character and three UTF-8 bytes; ensure_ascii=False keeps it that way
    # in the entry instead of escaping it to six ASCII bytes.
    return json.dumps([asdict(_StreamedOperationRow(error_message="€" * chars))], ensure_ascii=False)


@pytest.mark.parametrize("read_chars", [64 * 1024, 4 * 1024**2])
def test_streamed_item_size_counts_utf8_bytes(monkeypatch: pytest.MonkeyPatch, read_chars: int) -> None:
    text = _multibyte_operations(monkeypatch, read_chars, 1000 * 1024)
    # Under the limit in characters, about three times over it in bytes: the
    # character count used to stand in for the byte count and let this through.
    assert len(text) < 1024**2 < len(text.encode())
    name = "data/async_operations.json"
    archive = _rewrite(_synthetic_archive(), name, text.encode())
    # 64 KiB reads leave the item undecoded over several passes, so the check on the
    # pending buffer refuses it; one 4 MiB read decodes it whole, so the check on the
    # decoded item does.
    with pytest.raises(ValueError, match=f"{name} has an item larger than the entry size limit"):
        importer.validate_bank_archive(archive)


@pytest.mark.parametrize("read_chars", [64 * 1024, 4 * 1024**2])
def test_unfinished_streamed_item_is_refused_by_its_utf8_size(monkeypatch: pytest.MonkeyPatch, read_chars: int) -> None:
    # Cut before its last closing quote, the item never decodes, so only the check on
    # the pending buffer can refuse it by size. Counting characters there let the
    # buffer fill to the limit in characters (up to four times it in bytes) and the
    # read end at "not a complete JSON array" instead.
    text = _multibyte_operations(monkeypatch, read_chars, 1000 * 1024)[: -len('"}]')]
    name = "data/async_operations.json"
    archive = _rewrite(_synthetic_archive(), name, text.encode())
    with pytest.raises(ValueError, match=f"{name} has an item larger than the entry size limit"):
        importer.validate_bank_archive(archive)


@pytest.mark.parametrize("read_chars", [64 * 1024, 4 * 1024**2])
def test_streamed_multibyte_item_within_the_byte_limit_passes(monkeypatch: pytest.MonkeyPatch, read_chars: int) -> None:
    text = _multibyte_operations(monkeypatch, read_chars, 300 * 1024)
    # Over a quarter of the limit in characters, so the item is encoded and measured.
    assert 1024**2 < 4 * len(text) and len(text.encode()) < 1024**2
    importer.validate_bank_archive(_rewrite(_synthetic_archive(), "data/async_operations.json", text.encode()))


@pytest.mark.parametrize("read_chars", [64 * 1024, 4 * 1024**2])
def test_streamed_item_size_counts_crlf_bytes(monkeypatch: pytest.MonkeyPatch, read_chars: int) -> None:
    _multibyte_operations(monkeypatch, read_chars, 0)
    row = json.dumps(asdict(_StreamedOperationRow(error_message="x" * (600 * 1024))))
    # Put CRLF inside the item so newline translation would hide its stored size.
    text = "[{" + "\r\n" * (300 * 1024) + row[1:] + "]"
    payload = text.encode("utf-8")
    assert len(text.replace("\r\n", "\n").encode("utf-8")) < 1024**2 < len(payload)
    name = "data/async_operations.json"
    archive = _rewrite(_synthetic_archive(), name, payload)
    with pytest.raises(ValueError, match=f"{name} has an item larger than the entry size limit") as exc_info:
        importer.validate_bank_archive(archive)
    assert str(exc_info.value) == f"Invalid transfer archive: {name} has an item larger than the entry size limit"


def test_streamed_crlf_whitespace_parses() -> None:
    rows = [asdict(_StreamedOperationRow(error_message=message)) for message in ("first", "second")]
    text = json.dumps(rows, indent=1).replace("\n", "\r\n")
    archive = _rewrite(_synthetic_archive(), "data/async_operations.json", text.encode("utf-8"))
    importer.validate_bank_archive(archive)


_SHARED_BLOB = "blobs/000000.bin"


def _shared_blob_archive(monkeypatch: pytest.MonkeyPatch, copies: int) -> bytes:
    """A 600 KiB blob named by ``copies`` attachment rows, under a 1 MiB total-size limit."""
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    config.transfer_import_max_total_size_mb = 1
    monkeypatch.setattr(importer, "get_config", lambda: config)
    content = b"a" * (600 * 1024)
    rows = [
        TransferAttachment(
            bank_id="atomic-source",
            document_id="doc-0",
            attachment_hash=hashlib.sha256(content).hexdigest(),
            short_id=f"short-{copy}",
            media_type="text/plain",
            byte_size=len(content),
            entry=_SHARED_BLOB,
        )
        for copy in range(copies)
    ]
    archive = _rewrite(
        _synthetic_archive(), "attachments.json", ("[" + ",".join(row.model_dump_json() for row in rows) + "]").encode()
    )
    return _rewrite(archive, _SHARED_BLOB, content)


def test_attachment_blob_named_once_passes_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    importer.validate_bank_archive(_shared_blob_archive(monkeypatch, copies=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["preflight", "import"])
async def test_attachment_rows_sharing_a_blob_are_bounded_by_the_total_size(
    monkeypatch: pytest.MonkeyPatch, barrier: str
) -> None:
    # The archive holds 600 KiB, inside the 1 MiB total, but two rows name the same
    # entry and each barrier reads and hashes it once per row: 1200 KiB of work.
    archive = _shared_blob_archive(monkeypatch, copies=2)
    read = MagicMock(side_effect=AssertionError("the sum must be checked before any attachment is read"))
    monkeypatch.setattr(importer, "_validated_attachment_chunks", read)
    with pytest.raises(ValueError, match="attachments reference more bytes than the total size limit"):
        await _run_barrier(barrier, archive)
    read.assert_not_called()


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


def _patch_config(monkeypatch: pytest.MonkeyPatch, **overrides: int) -> HindsightConfig:
    """Patch the importer's environment config with the requested limits."""
    from hindsight_api.config import HindsightConfig

    config = HindsightConfig.from_env()
    for name, value in overrides.items():
        setattr(config, name, value)
    monkeypatch.setattr(importer, "get_config", lambda: config)
    return config


@dataclass
class _PaddedEntryArchive:
    archive: bytes
    declared_size: int


def _with_undeclared_padding(name: str) -> _PaddedEntryArchive:
    """Forge a small valid prefix's CRC and size over a much larger DEFLATE stream."""
    archive = _synthetic_archive()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        prefix = zf.read(name)
    archive = _rewrite(archive, name, prefix + b" " * (4 * 1024**2))
    damaged = bytearray(archive)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        local = zf.getinfo(name).header_offset
        central = zf.start_dir
        while damaged[central : central + 4] == b"PK\x01\x02":
            name_length, extra_length, comment_length = struct.unpack_from("<HHH", damaged, central + 28)
            entry_name = damaged[central + 46 : central + 46 + name_length].decode()
            if entry_name == name:
                break
            central += 46 + name_length + extra_length + comment_length
        else:
            raise AssertionError(f"{name} central entry missing")
    # Keep the compressed size intact so ZipExtFile sees all padding, while its
    # CRC and returned-length checks see only the valid prefix. Whole reads used
    # to inflate the padding before truncating it, making a size-only test pass.
    for offset in (local + 14, central + 16):
        struct.pack_into("<I", damaged, offset, zlib.crc32(prefix))
    for offset in (local + 22, central + 24):
        struct.pack_into("<I", damaged, offset, len(prefix))
    return _PaddedEntryArchive(archive=bytes(damaged), declared_size=len(prefix))


@dataclass
class _DecompressionCall:
    max_length: int
    inflated_bytes: int


class _RecordingDecompressor:
    """Record output before ZipExtFile truncates it to the declared size."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.calls: list[_DecompressionCall] = []

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        output = self.raw.decompress(data, max_length)
        self.calls.append(_DecompressionCall(max_length=max_length, inflated_bytes=len(output)))
        return output

    @property
    def eof(self) -> bool:
        return self.raw.eof

    @property
    def unconsumed_tail(self) -> bytes:
        return self.raw.unconsumed_tail

    def flush(self) -> bytes:
        return self.raw.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "barrier,name",
    [
        ("preflight", "manifest.json"),
        ("preflight", "banks.json"),
        ("preflight", "documents/000000.json"),
        ("restore", "manifest.json"),
        ("restore", "banks.json"),
        ("restore", "documents/000000.json"),
        ("documents", "documents/000000.json"),
    ],
)
async def test_entry_reads_bound_decompression_before_size_truncation(
    monkeypatch: pytest.MonkeyPatch, barrier: str, name: str
) -> None:
    planted = _with_undeclared_padding(name)
    _patch_config(monkeypatch, transfer_import_max_entry_size_mb=1)
    get_decompressor = zipfile._get_decompressor
    open_entry = zipfile.ZipFile.open
    target_decompressors: list[_RecordingDecompressor] = []

    def record_decompressor(compression: int) -> _RecordingDecompressor:
        return _RecordingDecompressor(get_decompressor(compression))

    def record_open(zf: zipfile.ZipFile, entry: Any, *args: Any, **kwargs: Any) -> Any:
        blob = open_entry(zf, entry, *args, **kwargs)
        if (entry.filename if isinstance(entry, zipfile.ZipInfo) else entry) == name:
            target_decompressors.append(blob._decompressor)
        return blob

    monkeypatch.setattr(zipfile, "_get_decompressor", record_decompressor)
    monkeypatch.setattr(zipfile.ZipFile, "open", record_open)
    if barrier == "restore":
        from hindsight_api.engine import memories

        class DocumentsRead(Exception):
            """Stop restore after real document reads, before embedding or later writes."""

        async def read_documents(zf: zipfile.ZipFile, **kwargs: Any) -> None:
            list(importer._iter_documents(zf))
            raise DocumentsRead

        # Keep restore's actual entry readers, stubbing only persistence and the
        # remainder of replay. The former tests stopped at directory metadata and
        # never observed the bytes inflated before ZipExtFile's size truncation.
        monkeypatch.setattr(memories, "get_memories", lambda: MagicMock(ensure_bank_storage=AsyncMock()))
        _stub_connection(monkeypatch, importer, MagicMock(fetchval=AsyncMock(return_value=None)))
        monkeypatch.setattr(importer, "_restore_rows", AsyncMock(return_value=1))
        monkeypatch.setattr(importer, "_replay_documents", read_documents)
        with pytest.raises(DocumentsRead):
            await _run_barrier(barrier, planted.archive)
    elif barrier == "preflight":
        await _run_barrier(barrier, planted.archive)
    else:
        assert len(list(importer.iter_imported_documents(planted.archive, {"doc-0": ["unit"]}, {}))) == 1

    # Other entries include intentionally streamed readers with a different chunk
    # size; measure every decompressor call for the forged whole-entry reader.
    assert target_decompressors
    chunk_limit = max(importer._ENTRY_READ_BYTES, zipfile.ZipExtFile.MIN_READ_SIZE)
    for decompressor in target_decompressors:
        assert decompressor.calls
        assert all(0 < call.max_length <= chunk_limit for call in decompressor.calls)
        assert sum(call.inflated_bytes for call in decompressor.calls) < planted.declared_size + chunk_limit


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_entry_reader_checks_actual_returned_bytes(monkeypatch: pytest.MonkeyPatch, extra_bytes: int) -> None:
    """Enforce the byte limit independently of the central-directory precheck."""
    _patch_config(monkeypatch, transfer_import_max_entry_size_mb=1)
    payload = b" " * (1024**2 + extra_bytes)
    archive = _rewrite(_synthetic_archive(), "rows.json", payload)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        if extra_bytes:
            with pytest.raises(ValueError, match="'rows.json' exceeds the entry size limit"):
                importer._read_entry(zf, "rows.json")
        else:
            assert importer._read_entry(zf, "rows.json") == payload


@contextmanager
def _refused_before_zipfile(patcher: pytest.MonkeyPatch, message: str) -> Iterator[None]:
    """The body must raise exactly ``message`` without constructing ZipFile."""
    parse = MagicMock(side_effect=AssertionError("the refusal must come before ZipFile is constructed"))
    patcher.setattr(importer.zipfile, "ZipFile", parse)
    with pytest.raises(ValueError) as error:
        yield
    assert str(error.value) == message
    parse.assert_not_called()


def _assert_archive_accepted(archive: bytes, as_file: bool) -> None:
    """Accept archive bytes or a file whose caller has moved the cursor."""
    if as_file:
        upload = io.BytesIO(archive)
        upload.seek(5)  # Precheck must reset files rather than inherit the caller's cursor.
        assert importer.validate_bank_archive(upload) is None
    else:
        assert importer.validate_bank_archive(archive) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["preflight", "restore"])
async def test_end_record_entry_limit_precedes_zipfile(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    archive = _synthetic_archive()
    _patch_config(monkeypatch, transfer_import_max_entries=6)  # The fixture declares seven entries.
    with _refused_before_zipfile(
        monkeypatch, "Invalid transfer archive: entry count exceeds the transfer import limit"
    ):
        await _run_barrier(path, archive)


def test_zip64_end_record_limits_and_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force CPython's writer to emit real ZIP64 end records for this small fixture.
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    archive_bytes = bytearray(_synthetic_archive())
    # The writer emits ZIP64 at the lowered threshold but still fits the classic
    # count. Plant its sentinel too, proving the precheck uses the ZIP64 count.
    struct.pack_into("<H", archive_bytes, len(archive_bytes) - 22 + 10, 0xFFFF)
    archive = bytes(archive_bytes)
    assert b"PK\x06\x06" in archive
    config = _patch_config(monkeypatch, transfer_import_max_entries=6)
    with monkeypatch.context() as before_parse:
        with _refused_before_zipfile(
            before_parse, "Invalid transfer archive: entry count exceeds the transfer import limit"
        ):
            importer.validate_bank_archive(archive)
    config.transfer_import_max_entries = 7
    assert importer.validate_bank_archive(archive) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["preflight", "restore"])
async def test_end_record_directory_size_precedes_zipfile(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    archive = bytearray(_synthetic_archive())
    # Claim a large directory without allocating it; the end-record barrier must
    # refuse before ZipFile attempts its whole-directory read.
    struct.pack_into("<L", archive, len(archive) - 22 + 12, 1024**2 + 1)
    _patch_config(monkeypatch, transfer_import_max_entry_size_mb=1)
    with _refused_before_zipfile(
        monkeypatch, "Invalid transfer archive: central directory exceeds the entry size limit"
    ):
        await _run_barrier(path, bytes(archive))


@pytest.mark.parametrize("field,fmt,sentinel", [(10, "<H", 0xFFFF), (12, "<L", 0xFFFFFFFF), (16, "<L", 0xFFFFFFFF)])
def test_end_record_zip64_sentinels_require_records(
    monkeypatch: pytest.MonkeyPatch, field: int, fmt: str, sentinel: int
) -> None:
    archive = bytearray(_synthetic_archive())
    struct.pack_into(fmt, archive, len(archive) - 22 + field, sentinel)
    _patch_config(monkeypatch)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed end-of-central-directory record"):
        importer.validate_bank_archive(bytes(archive))


def test_end_record_count_must_fit_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = bytearray(_synthetic_archive())
    struct.pack_into("<L", archive, len(archive) - 22 + 12, 7 * 46 - 1)
    _patch_config(monkeypatch)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed end-of-central-directory record"):
        importer.validate_bank_archive(bytes(archive))


@pytest.mark.parametrize("as_file", [False, True])
@pytest.mark.parametrize("zip64", [False, True])
@pytest.mark.parametrize("comment_size", [300, 65535])
def test_end_record_with_comment_accepts_bytes_and_file(
    monkeypatch: pytest.MonkeyPatch, as_file: bool, zip64: bool, comment_size: int
) -> None:
    archive = _synthetic_archive()
    if zip64:
        monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    out = io.BytesIO(archive)
    with zipfile.ZipFile(out, "a") as zf:
        zf.comment = b"x" * comment_size
    data = out.getvalue()
    _assert_archive_accepted(data, as_file)


def _archive_with_directory_fields() -> bytes:
    """Retain the seven-member fixture while exercising all variable-length fields."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_synthetic_archive())) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            info.comment = b"c" * 9
            # A valid unknown field must be skipped by CPython's _decodeExtra.
            info.extra = b"\xfe\xca\x04\x00abcd"
            target.writestr(info, data)
    return out.getvalue()


def test_end_record_with_65536_trailing_bytes_precedes_zipfile(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _synthetic_archive() + b"\x00" * 65536
    _patch_config(monkeypatch, transfer_import_max_entries=6)
    with _refused_before_zipfile(
        monkeypatch, "Invalid transfer archive: entry count exceeds the transfer import limit"
    ):
        importer.validate_bank_archive(archive)


def test_end_record_with_65537_trailing_bytes_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, transfer_import_max_entries=6)
    with pytest.raises(ValueError) as error:
        importer.validate_bank_archive(_synthetic_archive() + b"\x00" * 65537)
    assert str(error.value).startswith("Invalid transfer archive: the uploaded file is not a readable .zip")


def test_zip64_extensible_data_precedes_zipfile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    archive = bytearray(_synthetic_archive())
    end = len(archive) - 22
    record = end - 76
    struct.pack_into("<Q", archive, record + 4, 52)
    archive[record + 56 : record + 56] = b"x" * 8
    # Reproduce G2: understated classic values cannot stand in for ZIP64.
    struct.pack_into("<2H", archive, end + 8 + 8, 1, 1)
    struct.pack_into("<L", archive, end + 8 + 12, 46)
    _patch_config(monkeypatch, transfer_import_max_entries=6)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed end-of-central-directory record"):
        importer.validate_bank_archive(bytes(archive))


@pytest.mark.parametrize(
    "field,offset,fmt,change",
    [
        ("record", 4, "<Q", 1),  # sz = 45
        ("locator", 8, "<Q", -1),
        ("record", 48, "<Q", 1),
        ("locator", 16, "<L", 1),  # total disks = 2
        ("locator", 4, "<L", 1),  # the ZIP64 record is on another disk
        ("record", 0, "<L", None),  # keep the locator, erase the record signature
    ],
    ids=["size", "locator-offset", "directory-offset", "total-disks", "disk", "signature"],
)
def test_zip64_incompatible_shapes_precede_zipfile(
    monkeypatch: pytest.MonkeyPatch, field: str, offset: int, fmt: str, change: int | None
) -> None:
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    archive = bytearray(_synthetic_archive())
    end = len(archive) - 22
    position = end - (76 if field == "record" else 20) + offset
    value = struct.unpack_from(fmt, archive, position)[0]
    struct.pack_into(fmt, archive, position, 0 if change is None else value + change)
    _patch_config(monkeypatch)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed end-of-central-directory record"):
        importer.validate_bank_archive(bytes(archive))


def test_zip64_locator_cannot_select_an_earlier_valid_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    archive = bytearray(_synthetic_archive())
    record = len(archive) - 22 - 76
    # Two valid-looking records separate the generations: patched CPython reads
    # the earlier locator target; unpatched CPython reads the adjacent duplicate.
    archive[record + 56 : record + 56] = archive[record : record + 56]
    _patch_config(monkeypatch)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed end-of-central-directory record"):
        importer.validate_bank_archive(bytes(archive))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["preflight", "restore"])
async def test_directory_walk_refuses_understated_classic_counts(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    archive = bytearray(_archive_with_directory_fields())
    struct.pack_into("<2H", archive, len(archive) - 22 + 8, 1, 1)
    _patch_config(monkeypatch, transfer_import_max_entries=6)
    with _refused_before_zipfile(
        monkeypatch, "Invalid transfer archive: entry count exceeds the transfer import limit"
    ):
        await _run_barrier(path, bytes(archive))


def test_directory_walk_refuses_understated_zip64_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    archive = bytearray(_archive_with_directory_fields())
    end = len(archive) - 22
    struct.pack_into("<2H", archive, end + 8, 1, 1)
    struct.pack_into("<2Q", archive, end - 76 + 24, 1, 1)
    _patch_config(monkeypatch, transfer_import_max_entries=6)
    with _refused_before_zipfile(
        monkeypatch, "Invalid transfer archive: entry count exceeds the transfer import limit"
    ):
        importer.validate_bank_archive(bytes(archive))


@pytest.mark.parametrize("as_file", [False, True])
@pytest.mark.parametrize("zip64", [False, True])
@pytest.mark.parametrize("understated", [False, True])
def test_directory_walk_accepts_the_entry_limit(
    monkeypatch: pytest.MonkeyPatch, as_file: bool, zip64: bool, understated: bool
) -> None:
    if zip64:
        monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    archive = _archive_with_directory_fields()
    if understated:
        data = bytearray(archive)
        end = len(data) - 22
        struct.pack_into("<2H", data, end + 8, 1, 1)
        if zip64:
            struct.pack_into("<2Q", data, end - 76 + 24, 1, 1)
        archive = bytes(data)
    _patch_config(monkeypatch, transfer_import_max_entries=7)
    _assert_archive_accepted(archive, as_file)


def test_directory_walk_bad_record_precedes_zipfile(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = bytearray(_archive_with_directory_fields())
    end = len(archive) - 22
    start = struct.unpack_from("<L", archive, end + 16)[0]
    name_size, extra_size, comment_size = struct.unpack_from("<3H", archive, start + 28)
    second = start + 46 + name_size + extra_size + comment_size
    archive[second : second + 4] = b"\x00" * 4
    _patch_config(monkeypatch)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed central directory"):
        importer.validate_bank_archive(bytes(archive))


def test_directory_negative_start_precedes_zipfile(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = bytearray(_synthetic_archive())
    end = len(archive) - 22
    struct.pack_into("<L", archive, end + 12, end + 46)
    _patch_config(monkeypatch)
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed end-of-central-directory record"):
        importer.validate_bank_archive(bytes(archive))


@pytest.mark.parametrize("as_file", [False, True])
def test_directory_walk_crosses_chunk_boundaries(monkeypatch: pytest.MonkeyPatch, as_file: bool) -> None:
    monkeypatch.setattr(importer, "_DIRECTORY_CHUNK", 50)
    archive = _archive_with_directory_fields()
    config = _patch_config(monkeypatch, transfer_import_max_entries=7)
    _assert_archive_accepted(archive, as_file)
    understated = bytearray(archive)
    struct.pack_into("<2H", understated, len(understated) - 22 + 8, 1, 1)
    config.transfer_import_max_entries = 6
    with _refused_before_zipfile(
        monkeypatch, "Invalid transfer archive: entry count exceeds the transfer import limit"
    ):
        importer.validate_bank_archive(io.BytesIO(understated) if as_file else bytes(understated))


@pytest.mark.parametrize("size", [45, 46])
def test_directory_walk_requires_a_complete_header(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    from hindsight_api.config import HindsightConfig

    # Distinguish a header outside the declared directory from a declared header
    # whose bytes are absent from the upload. Neither may reach ZipFile.
    archive = b"PK\x01\x02" + b"\x00" * 41
    directory = importer._ArchiveDirectory(entries=0, size=size, start=0)
    config = HindsightConfig.from_env()
    with _refused_before_zipfile(monkeypatch, "Invalid transfer archive: malformed central directory"):
        importer._walk_archive_directory(archive, directory, config)


def test_directory_walk_reads_only_bounded_directory_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    from hindsight_api.config import HindsightConfig

    archive = _archive_with_directory_fields()
    end = len(archive) - 22
    size, start = struct.unpack_from("<2L", archive, end + 12)
    directory = importer._ArchiveDirectory(entries=7, size=size, start=start)
    config = HindsightConfig.from_env()
    config.transfer_import_max_entries = 7
    read = MagicMock(wraps=importer._read_archive_range)
    monkeypatch.setattr(importer, "_read_archive_range", read)
    # Every chunk size from one header to the whole directory: at some sizes a
    # header starts in a final partial chunk, whose read must stop at the end.
    clipped = False
    for chunk in range(46, size + 1):
        monkeypatch.setattr(importer, "_DIRECTORY_CHUNK", chunk)
        read.reset_mock()
        importer._walk_archive_directory(io.BytesIO(archive), directory, config)
        for call in read.call_args_list:
            offset, length = call.args[1:]
            assert start <= offset < start + size
            assert 0 < length <= chunk
            assert offset + length <= start + size
            clipped |= length < chunk
    assert clipped
