"""A store-answered recall fails closed when the store leaves is_stale unset.

The store owns freshness for a recall it answers (see Memories.full_recall), but MemoryFact
defaults is_stale to False, so a store that never set it would report every record fresh.
The engine fails closed at the store boundary: any fact whose is_stale was not explicitly
set by the store (not in model_fields_set) is reported stale (is_stale=True), while an explicit
value set by the store stands.
"""

import uuid
from collections.abc import Iterator

import pytest

from hindsight_api.engine.memories import set_memories
from hindsight_api.engine.memories.base import FullRecallRequest
from hindsight_api.engine.memory_engine import MemoryEngine
from hindsight_api.engine.response_models import MemoryFact, RecallResult
from hindsight_api.models import RequestContext
from tests.test_memories_extension import InMemoryMemories


class _RecordingStore(InMemoryMemories):
    """Claims the recall and returns memory facts with unset vs explicit is_stale."""

    def __init__(self, bank_id: str) -> None:
        super().__init__({})
        self.bank_id = bank_id

    async def full_recall(self, request: FullRecallRequest) -> RecallResult:
        return RecallResult(
            results=[
                MemoryFact(
                    id="00000000-0000-0000-0000-000000000001",
                    text="fact without is_stale",
                    fact_type="world",
                ),
                MemoryFact(
                    id="00000000-0000-0000-0000-000000000002",
                    text="fact with explicit is_stale=False",
                    fact_type="world",
                    is_stale=False,
                ),
            ]
        )


@pytest.fixture
def restore_default_store() -> Iterator[None]:
    yield
    set_memories(None)


async def _claiming_store(memory: MemoryEngine, request_context: RequestContext) -> _RecordingStore:
    bank_id = f"so-stale-{uuid.uuid4().hex[:8]}"
    store = _RecordingStore(bank_id)
    set_memories(store)
    # The store owns the facts, never the bank row, so create it the way a retain would.
    await memory.ensure_bank_profile(bank_id, request_context=request_context)
    return store


@pytest.mark.asyncio
async def test_store_answered_recall_fails_closed_when_is_stale_unset(
    api_client, memory, request_context, restore_default_store
) -> None:
    store = await _claiming_store(memory, request_context)

    response = await api_client.post(
        f"/v1/default/banks/{store.bank_id}/memories/recall",
        json={"query": "test freshness", "types": ["world"], "limit": 10},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    results = data["results"]
    assert len(results) == 2

    # Fact without is_stale was unset by the store: fails closed to is_stale=True
    assert results[0]["id"] == "00000000-0000-0000-0000-000000000001"
    assert results[0]["is_stale"] is True

    # Fact with explicit is_stale=False set by the store: preserved as is_stale=False
    assert results[1]["id"] == "00000000-0000-0000-0000-000000000002"
    assert results[1]["is_stale"] is False
