"""Wait for the asynchronous half of the system to finish.

A retain returns as soon as the facts are stored, but the server is not done: the
worker still has observation extraction and possibly consolidation to run. Those
are not noise to be suppressed — they are where a large share of this project's
composition bugs live — so tests let them run and wait for them here.

The wait is expressed through the public operations API, like everything else in
this suite. It settles when the bank has nothing pending or processing, and it
fails loudly on a failed operation rather than letting a test assert against a
half-built bank and call it a pass.
"""

from __future__ import annotations

import asyncio
import os

from hindsight_client import Hindsight

from hindsight_system_tests.waiting import wait_until_settled as _wait_until_settled

# Far longer than the system tests use, and for a concrete reason: there a stub
# answers instantly, so 90s is generous. Here a settle waits on real fact
# extraction AND consolidation over the whole corpus, each a real model call. The
# first run of this suite failed at 90s with `consolidation(processing)` still in
# flight — the eval was fine, the inherited timeout was not.
SETTLE_TIMEOUT_SECONDS = float(os.getenv("HINDSIGHT_EVAL_SETTLE_TIMEOUT", "900"))
# Polling five times a second against a real server for fifteen minutes is a lot
# of pointless requests; a settle here takes minutes, not milliseconds.
POLL_INTERVAL_SECONDS = 1.0


async def wait_until_settled(
    client: Hindsight,
    bank_id: str,
    *,
    timeout: float = SETTLE_TIMEOUT_SECONDS,
    allow_failed: bool = False,
) -> None:
    """Wait using the shared system-tests helper with real-model eval defaults."""
    await _wait_until_settled(
        client, bank_id, timeout=timeout, allow_failed=allow_failed, poll_interval=POLL_INTERVAL_SECONDS
    )


async def drain_then_fail_on_failed(client: Hindsight, bank_id: str, *, timeout: float) -> None:
    """Wait until nothing is queued, then fail on anything that ended failed.

    Not ``wait_until_settled``: that gives up on the first attempt that errors,
    while a build runs ~a thousand model calls and a transient provider error is
    what the worker's retries are for. Only an operation that exhausted them is
    a broken build.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        busy = [
            op
            for status in ("pending", "processing")
            for op in (await client.operations.list_operations(bank_id, status=status, limit=100)).operations
        ]
        if not busy:
            break
        await asyncio.sleep(5)
    else:
        raise TimeoutError(f"bank {bank_id} still busy after {timeout}s")
    failed = (await client.operations.list_operations(bank_id, status="failed", limit=100)).operations
    if failed:
        raise RuntimeError(f"bank {bank_id}: {', '.join(f'{op.task_type}: {op.error_message}' for op in failed)}")
