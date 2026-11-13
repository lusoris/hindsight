"""Tests for the admin CLI whole-bank transfer boundary."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hindsight_api.admin import cli


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_run_export_bank_declares_decoded_json_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The codec-enabled admin producer must identify its rows as decoded."""
    connection = _FakeConnection()
    call_record: dict = {}

    async def fake_stream_export_bank(conn, bank_id, **kwargs):
        call_record["args"] = (conn, bank_id)
        call_record["kwargs"] = kwargs
        yield b"arch"
        yield b"ive"

    async def fake_admin_connect(db_url: str) -> _FakeConnection:
        assert db_url == "postgresql://example"
        return connection

    monkeypatch.setattr(cli, "_admin_connect", fake_admin_connect)
    monkeypatch.setattr(cli, "stream_export_bank", fake_stream_export_bank)
    # The CLI resolves the configured memories store and hands it to the export: a bank whose
    # memories live outside SQL cannot be read from this connection, and without the store the
    # archive would come out well-formed and empty.
    store = object()
    monkeypatch.setattr(cli, "get_memories", lambda: store)

    output = tmp_path / "bank.zip"
    size = await cli._run_export_bank(
        "postgresql://example",
        "source-bank",
        output,
        "tenant_schema",
        include_history=True,
    )

    from hindsight_api.engine.transfer import TransferScope

    assert call_record["args"] == (connection, "source-bank")
    assert call_record["kwargs"]["bank_rows_json_encoding"] == "decoded"
    assert call_record["kwargs"]["memories"] is store
    # --include-history maps onto the scope's third flag; the other two are what a
    # migration always carries.
    assert call_record["kwargs"]["scope"] == TransferScope(data=True, bank_config=True, history=True)
    # Attachment bytes live in file storage rather than in a column, so the CLI has
    # to hand the export a storage client or a bank with attachments exports rows
    # pointing at blobs the archive does not carry.
    assert call_record["kwargs"]["file_storage"] is not None
    assert output.read_bytes() == b"archive"
    assert size == len(b"archive")
    assert connection.closed is True


def test_import_bank_command_prints_cleanup_recovery_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    import typer

    failure = ValueError("original restore error")
    failure.add_note("Recover half-restored bank: hindsight bank delete damaged-bank")
    monkeypatch.setattr(
        cli.HindsightConfig,
        "from_env",
        lambda: SimpleNamespace(database_url="postgresql://example", database_schema="public"),
    )
    monkeypatch.setattr(cli, "_run_import_bank", AsyncMock(side_effect=failure))
    with pytest.raises(typer.Exit) as error:
        cli.import_bank_command(archive=tmp_path / "archive.zip", schema=None, target_bank=None, include_history=False)
    assert error.value.exit_code == 1
    output = capsys.readouterr().err
    assert "original restore error" in output
    assert "hindsight bank delete damaged-bank" in output
