"""Shared Streamable-HTTP MCP endpoint E2E test helpers for coding-agent integrations.

Test-only module. Each integration that uses it puts ``hindsight-integrations/_shared``
on the test path with ``pythonpath = ["../_shared"]`` under ``[tool.pytest.ini_options]``.
Integration ``tests/`` directories stay in the monorepo and are not in the published
wheels, so the tests import this one file by path. Production code shared the same way
(``_shared/user_config.py``) has to be copied into each package instead, because the
wheels carry it.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest

HINDSIGHT_API_URL: str = os.getenv("HINDSIGHT_API_URL", "http://localhost:8888")
HINDSIGHT_API_TOKEN: str | None = os.getenv("HINDSIGHT_API_TOKEN")


@dataclass(frozen=True)
class _ParsedUrl:
    """Validated HTTP(S) connection target split from a URL string."""

    scheme: str
    hostname: str
    port: int | None
    request_target: str


@dataclass(frozen=True)
class RpcResponse:
    """Buffered HTTP response from one Streamable-HTTP MCP JSON-RPC POST."""

    status: int
    headers: http.client.HTTPMessage
    body: bytes


def _parse_http_url(url: str) -> _ParsedUrl:
    """Parse ``url`` and require an ``http`` or ``https`` scheme with a hostname."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Expected an http(s) URL with a host, got {url!r}")
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return _ParsedUrl(
        scheme=parts.scheme,
        hostname=parts.hostname,
        port=parts.port,
        request_target=target,
    )


def _connect(parsed: _ParsedUrl, timeout: float) -> http.client.HTTPConnection:
    """Open an HTTP or HTTPS connection for ``parsed`` with ``timeout`` seconds.

    Uses ``http.client`` rather than ``urllib.request.urlopen`` so only ``http`` and
    ``https`` schemes can be opened (avoiding ``file://`` exposure flagged by Bandit
    B310 and dynamic-urllib Semgrep rules) and sockets close deterministically.
    """
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout)
    return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)


def reachable() -> bool:
    """Return True when ``<HINDSIGHT_API_URL>/health`` responds with HTTP 200."""
    conn: http.client.HTTPConnection | None = None
    try:
        parsed = _parse_http_url(f"{HINDSIGHT_API_URL.rstrip('/')}/health")
        conn = _connect(parsed, timeout=3)
        conn.request("GET", parsed.request_target)
        resp = conn.getresponse()
        resp.read()
        return resp.status == 200
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        if conn is not None:
            conn.close()


def mcp_e2e_marks() -> list[pytest.MarkDecorator]:
    """Return the module-level ``pytestmark`` list gating live MCP endpoint E2E tests."""
    return [
        pytest.mark.requires_real_llm,
        pytest.mark.skipif(not reachable(), reason=f"Hindsight not reachable at {HINDSIGHT_API_URL}"),
    ]


def rpc(url: str, payload: Mapping[str, object], session: str | None = None) -> RpcResponse:
    """POST one JSON-RPC message to the Streamable-HTTP MCP ``url`` and return the response."""
    parsed = _parse_http_url(url)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    if HINDSIGHT_API_TOKEN:
        headers["Authorization"] = f"Bearer {HINDSIGHT_API_TOKEN}"

    data = json.dumps(dict(payload)).encode("utf-8")
    conn = _connect(parsed, timeout=15)
    try:
        conn.request("POST", parsed.request_target, body=data, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.headers, io.BytesIO(body))
        return RpcResponse(status=resp.status, headers=resp.headers, body=body)
    finally:
        conn.close()


def assert_lists_memory_tools(url: str, client_name: str) -> None:
    """Perform the three-step MCP handshake on ``url`` and verify memory tools are listed."""
    # 1) initialize -> capture the MCP session id
    init: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": "0"},
        },
    }
    session = rpc(url, init).headers.get("Mcp-Session-Id")

    # 2) initialized notification (required before normal requests)
    rpc(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session=session)

    # 3) tools/list -> should surface the memory tools
    resp = rpc(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session=session)
    text = resp.body.decode("utf-8", "replace")
    # Raise AssertionError explicitly because this helper module does not match
    # test_*.py (pytest skips assertion rewriting here and Bandit B101 flags assert).
    if not ("recall" in text and "retain" in text):
        raise AssertionError(f"tools/list did not surface memory tools: {text[:300]}")
