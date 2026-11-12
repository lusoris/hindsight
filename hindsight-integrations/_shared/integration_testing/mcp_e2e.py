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
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import IO
from urllib.parse import urlsplit

import pytest

HINDSIGHT_API_URL: str = os.getenv("HINDSIGHT_API_URL", "http://localhost:8888")
HINDSIGHT_API_TOKEN: str | None = os.getenv("HINDSIGHT_API_TOKEN")


@dataclass(frozen=True)
class _ParsedUrl:
    """Validated HTTP(S) origin split from a URL string."""

    scheme: str
    hostname: str
    port: int


@dataclass(frozen=True)
class JsonRpcRequest:
    """Outbound JSON-RPC 2.0 request or notification (``id=None``)."""

    method: str
    id: int | None = None
    params: Mapping[str, object] | None = None

    def encode(self) -> bytes:
        """Serialize to UTF-8 JSON-RPC 2.0 bytes, omitting unset ``id`` and ``params``."""
        wire: dict[str, object] = {"jsonrpc": "2.0", "method": self.method}
        if self.id is not None:
            wire["id"] = self.id
        if self.params is not None:
            wire["params"] = dict(self.params)
        return json.dumps(wire).encode("utf-8")


@dataclass(frozen=True)
class RpcResponse:
    """Buffered HTTP response from one Streamable-HTTP MCP JSON-RPC POST."""

    status: int
    headers: http.client.HTTPMessage
    body: bytes


@dataclass(frozen=True)
class JsonRpcReply:
    """Parsed JSON-RPC 2.0 reply fields from an MCP HTTP response."""

    result: object | None
    error: object | None


def _parse_http_url(url: str) -> _ParsedUrl:
    """Parse ``url`` and require an ``http`` or ``https`` scheme with a hostname."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Expected an http(s) URL with a host, got {url!r}")
    default_port = 443 if parts.scheme == "https" else 80
    return _ParsedUrl(
        scheme=parts.scheme,
        hostname=parts.hostname,
        port=parts.port if parts.port is not None else default_port,
    )


class _McpRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler keeping POST on 307/308 and dropping cross-origin Authorization."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            source = _parse_http_url(req.full_url)
            target = _parse_http_url(newurl)
        except ValueError as exc:
            raise urllib.error.HTTPError(req.full_url, code, str(exc), headers, fp) from exc
        cross_origin = source != target

        if code in (307, 308) and req.get_method() == "POST":
            newurl = newurl.replace(" ", "%20")
            forwarded_headers = {
                key: value
                for key, value in req.headers.items()
                if key.lower() != "content-length" and not (cross_origin and key.lower() == "authorization")
            }
            return urllib.request.Request(
                newurl,
                data=req.data,
                headers=forwarded_headers,
                origin_req_host=req.origin_req_host,
                unverifiable=True,
                method="POST",
            )

        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and cross_origin:
            redirected.remove_header("Authorization")
        return redirected


def _build_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        _McpRedirectHandler(),
    ):
        opener.add_handler(handler)
    return opener


# Hand-built opener rather than urllib.request.urlopen or build_opener: both
# unconditionally add FileHandler, FTPHandler and DataHandler, leaving non-HTTP
# schemes openable (flagged by Bandit B310 and dynamic-urllib Semgrep rules),
# whereas pairing _parse_http_url with HTTPHandler and HTTPSHandler limits
# opens to http and https. ProxyHandler and redirect handling match main's
# urlopen behaviour for HTTP(S)_PROXY/NO_PROXY and 3xx responses; the 307/308
# override in _McpRedirectHandler preserves POST method, body and headers
# (dropping Authorization when scheme, host or port changes) because stdlib
# HTTPRedirectHandler raises HTTPError on POST 307/308 instead of following
# Starlette's slash redirect as fetch and the MCP SDK clients do.
_OPENER: urllib.request.OpenerDirector = _build_opener()
_OPENER_PROXIES: Mapping[str, str] = MappingProxyType(urllib.request.getproxies())


def _open(req: urllib.request.Request, timeout: float) -> http.client.HTTPResponse:
    # ProxyHandler snapshots getproxies() in __init__; rebuild when proxy env
    # variables changed after import (e.g. monkeypatch.setenv in tests).
    opener = _OPENER if urllib.request.getproxies() == _OPENER_PROXIES else _build_opener()
    return opener.open(req, timeout=timeout)


def reachable() -> bool:
    """Return True when ``<HINDSIGHT_API_URL>/health`` responds with HTTP 200."""
    try:
        health_url = f"{HINDSIGHT_API_URL.rstrip('/')}/health"
        _parse_http_url(health_url)
        req = urllib.request.Request(health_url, method="GET")
        with _open(req, timeout=3) as resp:
            resp.read()
            return resp.status == 200
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return False


def mcp_e2e_marks() -> list[pytest.MarkDecorator]:
    """Return the module-level ``pytestmark`` list gating live MCP endpoint E2E tests."""
    return [
        pytest.mark.requires_real_llm,
        pytest.mark.skipif(not reachable(), reason=f"Hindsight not reachable at {HINDSIGHT_API_URL}"),
    ]


def rpc(url: str, message: JsonRpcRequest, session: str | None = None) -> RpcResponse:
    """POST one JSON-RPC message to the Streamable-HTTP MCP ``url`` and return the response."""
    _parse_http_url(url)
    req = urllib.request.Request(url, data=message.encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if session:
        req.add_header("Mcp-Session-Id", session)
    if HINDSIGHT_API_TOKEN:
        req.add_header("Authorization", f"Bearer {HINDSIGHT_API_TOKEN}")
    with _open(req, timeout=15) as resp:
        return RpcResponse(status=resp.status, headers=resp.headers, body=resp.read())


def _extract_last_sse_data(text: str) -> str | None:
    """Return the last SSE event's ``data:`` lines joined with ``\\n``, or None if empty."""
    events: list[str] = []
    data_lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line == "":
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
        elif line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
        elif line == "data":
            data_lines.append("")
    if data_lines:
        events.append("\n".join(data_lines))
    return events[-1] if events else None


def parse_reply(resp: RpcResponse) -> JsonRpcReply:
    """Decode an ``application/json`` or ``text/event-stream`` MCP JSON-RPC response."""
    text = resp.body.decode("utf-8", "replace")
    content_type = resp.headers.get_content_type()
    if content_type == "application/json":
        raw_json: str | None = text.strip() or None
    elif content_type == "text/event-stream":
        raw_json = _extract_last_sse_data(text)
    else:
        raw_json = None

    # Raise AssertionError explicitly because this helper module does not match
    # test_*.py (pytest skips assertion rewriting here and Bandit B101 flags assert).
    if raw_json is None:
        raise AssertionError(f"MCP response carried no JSON-RPC message ({content_type}): {text[:300]}")
    try:
        decoded = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Invalid JSON in MCP response: {raw_json[:300]}") from exc
    if not isinstance(decoded, Mapping) or ("result" not in decoded and "error" not in decoded):
        raise AssertionError(f"MCP response carried no JSON-RPC reply object: {raw_json[:300]}")
    return JsonRpcReply(
        result=decoded["result"] if "result" in decoded else None,
        error=decoded["error"] if "error" in decoded else None,
    )


def assert_lists_memory_tools(url: str, client_name: str) -> None:
    """Perform the three-step MCP handshake on ``url`` and verify memory tools are listed."""
    # 1) initialize -> capture the MCP session id
    init_resp = rpc(
        url,
        JsonRpcRequest(
            method="initialize",
            id=1,
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "0"},
            },
        ),
    )
    init_reply = parse_reply(init_resp)
    if init_reply.error is not None or init_reply.result is None:
        raise AssertionError(f"MCP initialize failed (error={init_reply.error!r}, result={init_reply.result!r})")
    session = init_resp.headers.get("Mcp-Session-Id")

    # 2) initialized notification (required before normal requests)
    rpc(url, JsonRpcRequest(method="notifications/initialized"), session=session)

    # 3) tools/list -> should surface the memory tools
    resp = rpc(url, JsonRpcRequest(method="tools/list", id=2), session=session)
    tools_reply = parse_reply(resp)
    if tools_reply.error is not None or tools_reply.result is None:
        raise AssertionError(f"MCP tools/list failed (error={tools_reply.error!r}, result={tools_reply.result!r})")
    text = resp.body.decode("utf-8", "replace")
    # Raise AssertionError explicitly because this helper module does not match
    # test_*.py (pytest skips assertion rewriting here and Bandit B101 flags assert).
    if not ("recall" in text and "retain" in text):
        raise AssertionError(f"tools/list did not surface memory tools: {text[:300]}")
