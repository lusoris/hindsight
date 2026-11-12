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
# The tools a coding agent's MCP endpoint has to list (hindsight_api/mcp_tools.py names).
_MEMORY_TOOLS: frozenset[str] = frozenset({"recall", "retain"})


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


# Credentials that must never reach another origin on a redirect: the bearer token and
# the MCP session id. The session id is a credential too, since whoever holds it can
# act in that session, so it is dropped together with Authorization. The fetch spec's
# redirect step removes only Authorization, its one CORS non-wildcard request-header name.
# Compared lowercased: urllib stores header names capitalize()d ("Mcp-session-id").
_CROSS_ORIGIN_DROPPED_HEADERS: frozenset[str] = frozenset({"authorization", "mcp-session-id"})


class _McpRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler keeping POST on 307/308 and dropping credentials cross-origin."""

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
                if key.lower() != "content-length"
                and not (cross_origin and key.lower() in _CROSS_ORIGIN_DROPPED_HEADERS)
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
            # remove_header matches the stored key exactly, so look the keys up
            # case-insensitively instead of guessing urllib's capitalisation.
            # header_items() returns a fresh list, so removing while looping is safe.
            for name, _value in redirected.header_items():
                if name.lower() in _CROSS_ORIGIN_DROPPED_HEADERS:
                    redirected.remove_header(name)
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
# (dropping Authorization and Mcp-Session-Id when scheme, host or port changes,
# on every redirect it follows) because stdlib
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


def _message_json(resp: RpcResponse) -> str | None:
    """The JSON-RPC message text of an ``application/json`` or ``text/event-stream`` response, or None."""
    text = resp.body.decode("utf-8", "replace")
    content_type = resp.headers.get_content_type()
    if content_type == "application/json":
        return text.strip() or None
    if content_type == "text/event-stream":
        return _extract_last_sse_data(text)
    return None


def _decode_json(raw_json: str) -> object:
    # Raise AssertionError explicitly because this helper module does not match
    # test_*.py (pytest skips assertion rewriting here and Bandit B101 flags assert).
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Invalid JSON in MCP response: {raw_json[:300]}") from exc


def parse_reply(resp: RpcResponse, request_id: int) -> JsonRpcReply:
    """Decode the MCP JSON-RPC 2.0 reply to the request ``request_id`` (JSON or SSE body).

    Accepts only a reply object that says ``"jsonrpc": "2.0"`` and carries the request's
    id, so a stray message or another request's reply on the stream cannot pass.
    """
    raw_json = _message_json(resp)
    if raw_json is None:
        raise AssertionError(
            f"MCP response carried no JSON-RPC message ({resp.headers.get_content_type()}): "
            f"{resp.body.decode('utf-8', 'replace')[:300]}"
        )
    decoded = _decode_json(raw_json)
    if not isinstance(decoded, Mapping) or ("result" not in decoded and "error" not in decoded):
        raise AssertionError(f"MCP response carried no JSON-RPC reply object: {raw_json[:300]}")
    if decoded.get("jsonrpc") != "2.0":
        raise AssertionError(f"MCP reply is not JSON-RPC 2.0: {raw_json[:300]}")
    reply_id = decoded.get("id")
    # bool is an int subclass and True == 1, so a reply id of true would otherwise
    # match request 1.
    if isinstance(reply_id, bool) or reply_id != request_id:
        raise AssertionError(f"MCP reply id {reply_id!r} does not match request id {request_id}: {raw_json[:300]}")
    return JsonRpcReply(
        result=decoded["result"] if "result" in decoded else None,
        error=decoded["error"] if "error" in decoded else None,
    )


def _assert_notification_accepted(resp: RpcResponse) -> None:
    """Fail when the response to a JSON-RPC notification carries a JSON-RPC error.

    A notification gets no reply, so Streamable HTTP answers 202 with an empty body and
    that passes. A server that rejects the notification can still answer HTTP 200 with
    an error object, which has to fail the handshake instead of being ignored.
    """
    raw_json = _message_json(resp)
    if raw_json is None:
        return
    decoded = _decode_json(raw_json)
    if isinstance(decoded, Mapping) and "error" in decoded:
        raise AssertionError(f"MCP notification rejected (error={decoded['error']!r}): {raw_json[:300]}")


def _tool_names(result: object) -> frozenset[str]:
    """The ``name`` of every tool in a ``tools/list`` result, ignoring malformed entries."""
    tools = result.get("tools") if isinstance(result, Mapping) else None
    if not isinstance(tools, list):
        return frozenset()
    return frozenset(tool["name"] for tool in tools if isinstance(tool, Mapping) and isinstance(tool.get("name"), str))


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
    init_reply = parse_reply(init_resp, request_id=1)
    if init_reply.error is not None or init_reply.result is None:
        raise AssertionError(f"MCP initialize failed (error={init_reply.error!r}, result={init_reply.result!r})")
    session = init_resp.headers.get("Mcp-Session-Id")

    # 2) initialized notification (required before normal requests)
    _assert_notification_accepted(rpc(url, JsonRpcRequest(method="notifications/initialized"), session=session))

    # 3) tools/list -> should surface the memory tools
    resp = rpc(url, JsonRpcRequest(method="tools/list", id=2), session=session)
    tools_reply = parse_reply(resp, request_id=2)
    if tools_reply.error is not None or tools_reply.result is None:
        raise AssertionError(f"MCP tools/list failed (error={tools_reply.error!r}, result={tools_reply.result!r})")
    # Exact tool names rather than a substring search of the body, so a description
    # or error text mentioning "recall" cannot stand in for the tool itself.
    names = _tool_names(tools_reply.result)
    missing = sorted(_MEMORY_TOOLS - names)
    # Raise AssertionError explicitly because this helper module does not match
    # test_*.py (pytest skips assertion rewriting here and Bandit B101 flags assert).
    if missing:
        raise AssertionError(f"tools/list did not surface memory tools {missing}; listed: {sorted(names)}")
