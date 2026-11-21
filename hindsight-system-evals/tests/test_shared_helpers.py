"""Server-free checks for shared waiting and page-wave orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from hindsight_system_evals import pages, waiting


@pytest.fixture
def drain_client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    sleep = AsyncMock()
    # Replace only the helper's clock, not the event loop's own time method.
    loop = SimpleNamespace(time=Mock(side_effect=[0.0, 0.0, 0.5, 2.0]))
    monkeypatch.setattr(waiting, "asyncio", SimpleNamespace(sleep=sleep, get_running_loop=lambda: loop))
    listing = AsyncMock()
    return SimpleNamespace(operations=SimpleNamespace(list_operations=listing), sleep=sleep)


def operation_page(*operations: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(operations=list(operations))


async def test_drain_stops_on_idle(drain_client: SimpleNamespace) -> None:
    listing = drain_client.operations.list_operations
    listing.return_value = operation_page()
    await waiting.drain_then_fail_on_failed(drain_client, "b", timeout=1)
    assert listing.await_args_list == [call("b", status=s, limit=100) for s in ("pending", "processing", "failed")]
    drain_client.sleep.assert_not_awaited()


async def test_drain_waits_for_retry_before_checking_failed(drain_client: SimpleNamespace) -> None:
    retry = SimpleNamespace(task_type="retain", error_message="transient")
    listing = drain_client.operations.list_operations
    listing.side_effect = [operation_page(retry), operation_page(), *[operation_page() for _ in range(3)]]
    await waiting.drain_then_fail_on_failed(drain_client, "b", timeout=1)
    drain_client.sleep.assert_awaited_once_with(5)
    assert listing.await_args_list[-1] == call("b", status="failed", limit=100)


async def test_drain_times_out_without_querying_failed(drain_client: SimpleNamespace) -> None:
    drain_client.operations.list_operations.return_value = operation_page(SimpleNamespace(task_type="retain"))
    with pytest.raises(TimeoutError, match="^bank b still busy after 1s$"):
        await waiting.drain_then_fail_on_failed(drain_client, "b", timeout=1)
    assert drain_client.sleep.await_args_list == [call(5), call(5)]
    assert all(c.kwargs["status"] != "failed" for c in drain_client.operations.list_operations.await_args_list)


async def test_drain_reports_all_failed_operations(drain_client: SimpleNamespace) -> None:
    failed = operation_page(
        SimpleNamespace(task_type="retain", error_message="bad extraction"),
        SimpleNamespace(task_type="consolidation", error_message="bad model"),
    )
    drain_client.operations.list_operations.side_effect = [operation_page(), operation_page(), failed]
    with pytest.raises(RuntimeError) as exc:
        await waiting.drain_then_fail_on_failed(drain_client, "b", timeout=1)
    assert str(exc.value) == "bank b: retain: bad extraction, consolidation: bad model"


@pytest.mark.parametrize("overrides", [{}, {"timeout": 12.0, "allow_failed": True}])
async def test_evals_wait_forwards_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, float | bool]
) -> None:
    shared = AsyncMock()
    monkeypatch.setattr(waiting, "_wait_until_settled", shared)
    client = object()
    await waiting.wait_until_settled(client, "eval-bank", **overrides)
    shared.assert_awaited_once_with(
        client,
        "eval-bank",
        timeout=overrides.get("timeout", waiting.SETTLE_TIMEOUT_SECONDS),
        allow_failed=overrides.get("allow_failed", False),
        poll_interval=1.0,
    )


async def test_page_waves_preserve_items_order_and_snapshots() -> None:
    events = Mock()
    events.attach_mock(AsyncMock(), "retain")
    events.attach_mock(AsyncMock(), "settle")
    events.attach_mock(AsyncMock(), "refresh")
    events.attach_mock(
        AsyncMock(side_effect=[SimpleNamespace(content="first"), SimpleNamespace(content="updated")]), "read"
    )
    client = SimpleNamespace(
        aretain_batch=events.retain, arefresh_mental_model=events.refresh, aget_mental_model=events.read
    )
    outcome = pages.PageOutcome(question_id="q", category="update", bank_id="b", mental_model_id="m")
    waves = [[{"content": "base", "timestamp": "2026-03-02T12:00:00Z"}], []]
    await pages.build_page_waves(client, outcome, waves, events.settle)
    assert events.mock_calls == [
        event
        for wave in waves
        for event in (
            call.retain(bank_id="b", items=wave),
            call.settle("b"),
            call.refresh(bank_id="b", mental_model_id="m"),
            call.settle("b"),
            call.read(bank_id="b", mental_model_id="m"),
        )
    ]
    assert outcome.waves == [pages.WaveSnapshot(1, 1, "first"), pages.WaveSnapshot(2, 0, "updated")]
