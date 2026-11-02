"""A recall's embedding and rerank calls show up in the bank's request history.

Hosted embedding and rerank providers bill per token or per document, and recall
makes both calls on every request. Retain then recall, and the bank's history must
show each kind of call, filterable by kind, with the provider's own token count for
the embeddings (the stub reports one token per word) — and each call inside the run of
the operation that made it: the recall's query embedding and rerank share one recall
trace, and the retained text is embedded under the retain.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from hindsight_client_api.api.llm_traces_api import LLMTracesApi

from hindsight_system_tests.payloads import consolidation, extracted, fact

pytestmark = pytest.mark.asyncio


async def test_embedding_and_rerank_calls_are_in_request_history(client, llm, bank_id, settled):
    llm.on_step("extract_facts").returns(extracted(fact("Alice lives in Lisbon")))
    llm.on_step("consolidate").returns(consolidation())

    await client.aretain(bank_id=bank_id, content="Alice lives in Lisbon.")
    await settled(bank_id)
    await client.arecall(bank_id=bank_id, query="Where does Alice live?")

    traces = LLMTracesApi(client._api_client)

    async def fetch() -> _TraceRows:
        return _TraceRows(
            embedding=(await traces.list_llm_requests(bank_id, call_type="embedding", limit=500)).items,
            rerank=(await traces.list_llm_requests(bank_id, call_type="rerank", limit=500)).items,
            llm=(await traces.list_llm_requests(bank_id, call_type="llm", limit=500)).items,
        )

    rows = await _poll_trace_rows(fetch)
    embedding, rerank, llm_rows = rows.embedding, rows.rerank, rows.llm

    query = next(r for r in embedding if r.scope == "embedding_query" and r.operation == "recall")
    assert query.provider == "openai" and query.status == "success"
    assert query.input == {"texts": ["Where does Alice live?"]}
    assert query.input_tokens == len("Where does Alice live?".split())
    assert query.llm_info["input_count"] == 1
    assert all(r.scope == "rerank" and r.llm_info["input_count"] >= 1 for r in rerank)

    # One recall, one run: its rerank carries the same trace as its query embedding.
    [recall_rerank] = [r for r in rerank if r.trace_id == query.trace_id]
    assert query.trace_id and recall_rerank.operation == "recall"
    assert recall_rerank.input["queries"] == ["Where does Alice live?"]
    assert len(recall_rerank.output["scores"]) == len(recall_rerank.input["documents"])

    retained = next(r for r in embedding if r.scope == "embedding_document" and r.operation == "retain")
    assert retained.trace_id and retained.trace_id != query.trace_id
    assert all(r.operation for r in embedding + rerank)  # no call outside a run

    assert llm_rows and not {r.scope for r in llm_rows} & {"embedding_document", "embedding_query", "rerank"}

    stats = await traces.llm_request_stats(bank_id, call_type="embedding", period="1d")
    assert sum(bucket.total for bucket in stats.buckets) == len(embedding)


@dataclass
class _TraceRows:
    embedding: list[Any]
    rerank: list[Any]
    llm: list[Any]


def _recall_traces_ready(rows: _TraceRows) -> bool:
    """Whether every row the trace assertions pick has been written: the recall's query
    embedding, a rerank in the same trace, the retain's document embedding, and the
    retain's LLM calls."""
    queries = [r for r in rows.embedding if r.scope == "embedding_query" and r.operation == "recall"]
    return (
        any(r.trace_id == q.trace_id for q in queries for r in rows.rerank)
        and any(r.scope == "embedding_document" and r.operation == "retain" for r in rows.embedding)
        and bool(rows.llm)
    )


async def _poll_trace_rows(
    fetch: Callable[[], Awaitable[_TraceRows]], timeout: float = 10.0, interval: float = 0.1
) -> _TraceRows:
    """Poll the request history until _recall_traces_ready holds.

    Rows are buffered and written in batches, so they appear over time. Waiting only
    for the scopes was a race: an embedding_query row from another operation, written
    before the recall's own, ended the poll and the next() that picks the recall row
    raised StopIteration. asyncio.timeout bounds the fetches too: each one otherwise
    inherits the client's 300 s request timeout, and a stalled fetch would replace the
    diagnostic below with a client error.
    """
    rows = _TraceRows(embedding=[], rerank=[], llm=[])
    try:
        async with asyncio.timeout(timeout):
            while True:
                rows = await fetch()
                if _recall_traces_ready(rows):
                    return rows
                await asyncio.sleep(interval)
    except TimeoutError:
        raise AssertionError(
            f"missing traces: embedding={[(r.scope, r.operation) for r in rows.embedding]} "
            f"rerank={[(r.operation, r.trace_id) for r in rows.rerank]} llm={len(rows.llm)}"
        ) from None


def _row(**fields: str) -> SimpleNamespace:
    return SimpleNamespace(**fields)


_EARLY = _TraceRows(
    embedding=[
        _row(scope="embedding_document", operation="retain", trace_id="t1"),
        _row(scope="embedding_query", operation="consolidation", trace_id="t2"),
    ],
    rerank=[_row(operation="recall", trace_id="t3")],
    llm=[_row(scope="extract_facts")],
)
_COMPLETE = _TraceRows(
    embedding=[*_EARLY.embedding, _row(scope="embedding_query", operation="recall", trace_id="t3")],
    rerank=_EARLY.rerank,
    llm=_EARLY.llm,
)


async def test_trace_poll_waits_for_the_recall_rows():
    """Planted ordering from the flaky run: an embedding_query row from another
    operation and a rerank arrive before the recall's own query embedding. The poll
    must keep fetching until the recall's row is there, not stop at the first page.

    Pure: it drives the suite's poll with scripted pages and needs no server, like
    the client-contract checks in test_51."""
    pages = [_EARLY, _COMPLETE]
    calls = 0

    async def fetch() -> _TraceRows:
        nonlocal calls
        calls += 1
        return pages[min(calls, len(pages)) - 1]

    assert await _poll_trace_rows(fetch, interval=0) is _COMPLETE
    assert calls == 2


async def test_trace_poll_reports_what_it_saw_on_timeout():
    """A poll that never sees the recall's rows fails with the rows it did see."""

    async def fetch() -> _TraceRows:
        return _EARLY

    with pytest.raises(AssertionError, match=r"missing traces: .*'consolidation'"):
        await _poll_trace_rows(fetch, timeout=0.05, interval=0.01)


async def test_deleting_a_bank_deletes_its_request_history(client, llm, bank_id, settled):
    # The history holds the bank's prompts and memory text: it must not outlive the bank,
    # nor reappear when a bank with the same id is created again (#5839).
    llm.on_step("extract_facts").returns(extracted(fact("Alice lives in Lisbon")))
    llm.on_step("consolidate").returns(consolidation())

    await client.aretain(bank_id=bank_id, content="Alice lives in Lisbon.")
    await settled(bank_id)

    traces = LLMTracesApi(client._api_client)
    for _ in range(50):
        if (await traces.list_llm_requests(bank_id, limit=1)).total:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("retain left no request history")

    await client.adelete_bank(bank_id)
    await client.acreate_bank(bank_id=bank_id, name="Alice")

    history = await traces.list_llm_requests(bank_id, limit=500)
    assert history.total == 0 and history.items == []
