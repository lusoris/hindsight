"""Scripted operations only; the suite's autouse stub guards start no server."""

from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from hindsight_system_tests import waiting


@dataclass
class Operation:
    task_type: str = "retain"
    status: str = "pending"
    error_message: str | None = None


@dataclass
class Page:
    operations: list[Operation] = field(default_factory=list)


@pytest.fixture
def scripted_client(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SimpleNamespace]:
    sleep = AsyncMock()
    monkeypatch.setattr(waiting.asyncio, "sleep", sleep)

    def make(*pages: Page) -> SimpleNamespace:
        operations = SimpleNamespace(list_operations=AsyncMock(side_effect=pages))
        return SimpleNamespace(operations=operations, sleep=sleep)

    return make


async def test_two_quiet_polls_and_custom_interval(scripted_client: Callable[..., SimpleNamespace]) -> None:
    client = scripted_client(*(Page() for _ in range(6)))
    await waiting.wait_until_settled(client, "bank", poll_interval=0.75)
    client.sleep.assert_awaited_once_with(0.75)
    assert client.operations.list_operations.await_args_list == [
        call("bank", status=status, limit=100) for _ in range(2) for status in ("failed", "pending", "processing")
    ]


async def test_busy_poll_resets_the_quiet_streak(scripted_client: Callable[..., SimpleNamespace]) -> None:
    pages = [Page() for _ in range(12)]
    pages[4] = Page([Operation()])
    client = scripted_client(*pages)
    await waiting.wait_until_settled(client, "bank")
    assert client.operations.list_operations.await_count == 12
    assert client.sleep.await_args_list == [call(0.2)] * 3


@pytest.mark.parametrize("message", ["provider failed", None])
async def test_failed_operation_raises(scripted_client: Callable[..., SimpleNamespace], message: str | None) -> None:
    client = scripted_client(Page([Operation(status="failed", error_message=message)]))
    with pytest.raises(AssertionError) as exc:
        await waiting.wait_until_settled(client, "bank")
    assert str(exc.value) == (f"bank bank has failed background operations — retain: {message or 'no error recorded'}")
    client.sleep.assert_not_awaited()


async def test_errored_pending_operation_raises(scripted_client: Callable[..., SimpleNamespace]) -> None:
    client = scripted_client(Page(), Page([Operation(error_message="retry queued")]), Page())
    with pytest.raises(AssertionError, match="background operation that failed — retain: retry queued"):
        await waiting.wait_until_settled(client, "bank")


async def test_allow_failed_waits_out_pending_retry(scripted_client: Callable[..., SimpleNamespace]) -> None:
    failed = Page([Operation(status="failed", error_message="exhausted")])
    retry = Page([Operation(error_message="retry queued")])
    client = scripted_client(failed, retry, Page(), failed, Page(), Page(), failed, Page(), Page())
    await waiting.wait_until_settled(client, "bank", allow_failed=True)
    assert client.operations.list_operations.await_count == 9
    assert client.sleep.await_count == 2


async def test_timeout_names_in_flight_operations(
    scripted_client: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(waiting, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    client = scripted_client(Page(), Page([Operation()]), Page([Operation("consolidation", "processing")]))
    with pytest.raises(AssertionError) as exc:
        await waiting.wait_until_settled(client, "bank", timeout=1)
    assert str(exc.value) == (
        "bank bank did not settle within 1s; still in flight: ['retain(pending)', 'consolidation(processing)']"
    )
