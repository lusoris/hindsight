"""Unit tests for the shared Streamable-HTTP MCP E2E helper (mcp_e2e)."""

from __future__ import annotations

import http.client
import json
import socket
import socketserver
import threading
import urllib.error
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import integration_testing.mcp_e2e as mcp_e2e
import pytest

_PROXY_ENV_VARS: tuple[str, ...] = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
)


@dataclass(frozen=True)
class RecordedRequest:
    """One HTTP request observed by the local loopback stub server."""

    method: str
    path: str
    requestline: str
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        target = name.lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return None


@dataclass(frozen=True)
class StubReply:
    """Scripted HTTP response returned by the loopback stub server."""

    status: int = 200
    content_type: str | None = "application/json"
    body: bytes = b""
    location: str | None = None
    session_id: str | None = None


def _encode_mcp_payload(payload: Mapping[str, object], mode: str) -> StubReply:
    encoded = json.dumps(dict(payload))
    if mode == "sse":
        body = f"event: message\r\ndata: {encoded}\r\n\r\n".encode("utf-8")
        return StubReply(status=200, content_type="text/event-stream; charset=utf-8", body=body)
    return StubReply(status=200, content_type="application/json; charset=utf-8", body=encoded.encode("utf-8"))


@dataclass
class StubServer:
    """Running loopback ThreadingHTTPServer plus recorded requests and route behaviour."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    base_url: str
    response_mode: str = "sse"
    session_id: str = "sess-stub-1"
    health_reply: StubReply = field(
        default_factory=lambda: StubReply(status=200, content_type="text/plain", body=b"ok")
    )
    init_payload: Mapping[str, object] = field(
        default_factory=lambda: {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub", "version": "0"},
            },
        }
    )
    tools_payload: Mapping[str, object] = field(
        default_factory=lambda: {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": "recall"}, {"name": "retain"}]},
        }
    )
    route_overrides: dict[str, StubReply] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    requests: list[RecordedRequest] = field(default_factory=list)


class _DaemonThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # Skip HTTPServer.server_bind's socket.getfqdn() reverse-DNS lookup on loopback.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


class _IPv6ThreadingHTTPServer(_DaemonThreadingHTTPServer):
    address_family = socket.AF_INET6


def _make_handler(state_box: list[StubServer]) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def _send(self, reply: StubReply) -> None:
            self.send_response(reply.status)
            if reply.content_type is not None:
                self.send_header("Content-Type", reply.content_type)
            if reply.location is not None:
                self.send_header("Location", reply.location)
            if reply.session_id is not None:
                self.send_header("Mcp-Session-Id", reply.session_id)
            self.send_header("Content-Length", str(len(reply.body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if reply.body:
                self.wfile.write(reply.body)

        def _dispatch(self) -> None:
            stub = state_box[0]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            recorded = RecordedRequest(
                method=self.command,
                path=self.path,
                requestline=self.requestline,
                headers=dict(self.headers.items()),
                body=body,
            )
            with stub.lock:
                stub.requests.append(recorded)

            route_path = urlsplit(self.path).path
            override = stub.route_overrides.get(route_path)
            if override is not None:
                self._send(override)
                return

            if self.command == "GET" and route_path == "/health":
                self._send(stub.health_reply)
                return

            if self.command == "POST" and route_path == "/mcp/":
                decoded = json.loads(body.decode("utf-8"))
                method = decoded.get("method")
                if method == "initialize":
                    base = _encode_mcp_payload(stub.init_payload, stub.response_mode)
                    self._send(
                        StubReply(
                            status=base.status,
                            content_type=base.content_type,
                            body=base.body,
                            session_id=stub.session_id,
                        )
                    )
                    return
                if method == "notifications/initialized":
                    self._send(StubReply(status=202, content_type="text/plain", body=b""))
                    return
                if method == "tools/list":
                    self._send(_encode_mcp_payload(stub.tools_payload, stub.response_mode))
                    return

            self._send(StubReply(status=404, content_type="text/plain", body=b"not found"))

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def log_message(self, format: str, *args: object) -> None:
            return

    return _Handler


@contextmanager
def _running_stub(ipv6: bool = False) -> Iterator[StubServer]:
    state_box: list[StubServer] = []
    handler_cls = _make_handler(state_box)
    if ipv6:
        server: ThreadingHTTPServer = _IPv6ThreadingHTTPServer(("::1", 0), handler_cls)
        port = server.server_address[1]
        base_url = f"http://[::1]:{port}"
    else:
        server = _DaemonThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        base_url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    stub = StubServer(server=server, thread=thread, base_url=base_url)
    state_box.append(stub)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _clean_proxy_and_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_TOKEN", None)


@pytest.fixture
def stub_server() -> Iterator[StubServer]:
    with _running_stub() as stub:
        yield stub


def test_proxy_routes_health_and_handshake(
    stub_server: StubServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a) Env HTTP proxy carries both /health and the MCP JSON-RPC handshake."""
    monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_URL", "http://hindsight.invalid")
    monkeypatch.setenv("http_proxy", stub_server.base_url)
    monkeypatch.setenv("HTTP_PROXY", stub_server.base_url)

    assert mcp_e2e.reachable() is True
    assert len(stub_server.requests) == 1
    health_req = stub_server.requests[0]
    assert health_req.path == "http://hindsight.invalid/health"
    assert health_req.requestline.startswith("GET http://hindsight.invalid/health ")

    mcp_e2e.assert_lists_memory_tools("http://hindsight.invalid/mcp/", "copilot-e2e")
    assert len(stub_server.requests) == 4
    for rpc_req in stub_server.requests[1:]:
        assert rpc_req.method == "POST"
        assert rpc_req.path == "http://hindsight.invalid/mcp/"


def test_redirects_followed_for_health_302_and_post_307(
    stub_server: StubServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) Health 302 -> 200 succeeds and POST 307/308 repeats POST with the same body."""
    stub_server.route_overrides["/health"] = StubReply(status=302, content_type=None, location="/health/ready")
    stub_server.route_overrides["/health/ready"] = StubReply(status=200, content_type="text/plain", body=b"ok")
    stub_server.route_overrides["/mcp"] = StubReply(status=307, content_type=None, location="/mcp/")
    stub_server.route_overrides["/mcp-perm"] = StubReply(status=308, content_type=None, location="/mcp/")
    monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_URL", stub_server.base_url)

    assert mcp_e2e.reachable() is True
    assert [r.path for r in stub_server.requests] == ["/health", "/health/ready"]

    stub_server.requests.clear()
    mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp", "copilot-e2e")
    assert len(stub_server.requests) == 6
    for idx in range(0, 6, 2):
        initial = stub_server.requests[idx]
        redirected = stub_server.requests[idx + 1]
        assert initial.method == "POST" and initial.path == "/mcp"
        assert redirected.method == "POST" and redirected.path == "/mcp/"
        assert redirected.body == initial.body and len(redirected.body) > 0
        assert redirected.header("Content-Type") == "application/json"

    stub_server.requests.clear()
    mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp-perm", "copilot-e2e")
    assert [r.path for r in stub_server.requests] == [
        "/mcp-perm",
        "/mcp/",
        "/mcp-perm",
        "/mcp/",
        "/mcp-perm",
        "/mcp/",
    ]


def test_redirect_307_drops_authorization_cross_origin_and_keeps_same_origin(
    stub_server: StubServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) A 307 to another port strips Authorization while a same-origin 307 keeps it."""
    monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_TOKEN", "secret-bearer-token")
    stub_server.route_overrides["/mcp"] = StubReply(status=307, content_type=None, location="/mcp/")

    mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp", "copilot-e2e")
    assert len(stub_server.requests) == 6
    for req in stub_server.requests:
        assert req.header("Authorization") == "Bearer secret-bearer-token"

    stub_server.requests.clear()
    with _running_stub() as other_stub:
        stub_server.route_overrides["/mcp-cross"] = StubReply(
            status=307,
            content_type=None,
            location=f"{other_stub.base_url}/mcp/",
        )
        mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp-cross", "copilot-e2e")
        assert len(stub_server.requests) == 3
        assert len(other_stub.requests) == 3
        for first_hop in stub_server.requests:
            assert first_hop.header("Authorization") == "Bearer secret-bearer-token"
        for redirected in other_stub.requests:
            assert redirected.header("Authorization") is None


def test_ipv6_loopback_reaches_server_with_bracketed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) Bracketed IPv6 URL connects on [::1] and sends Host: [::1]:<port>."""
    try:
        ctx = _running_stub(ipv6=True)
        stub_cm = ctx.__enter__()
    except OSError as exc:
        pytest.skip(f"IPv6 loopback binding unavailable: {exc}")

    try:
        port = stub_cm.server.server_address[1]
        monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_URL", f"http://[::1]:{port}")
        assert mcp_e2e.reachable() is True
        assert len(stub_cm.requests) == 1
        assert stub_cm.requests[0].header("Host") == f"[::1]:{port}"
    finally:
        ctx.__exit__(None, None, None)


def test_initialize_jsonrpc_error_with_http_200_raises(stub_server: StubServer) -> None:
    """(e) JSON-RPC error on initialize with HTTP 200 fails even when tools/list succeeds."""
    stub_server.init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32600, "message": "unsupported protocol"},
    }
    with pytest.raises(AssertionError, match="unsupported protocol"):
        mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp/", "copilot-e2e")


@pytest.mark.parametrize("response_mode", ["sse", "json"])
def test_happy_path_propagates_session_for_sse_and_json(
    stub_server: StubServer,
    response_mode: str,
) -> None:
    """(f) Both SSE and JSON MCP replies succeed and forward Mcp-Session-Id."""
    stub_server.response_mode = response_mode
    stub_server.session_id = f"session-{response_mode}"

    mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp/", "copilot-e2e")
    assert len(stub_server.requests) == 3

    init_req, initialized_req, list_req = stub_server.requests
    assert init_req.header("Mcp-Session-Id") is None
    assert initialized_req.header("Mcp-Session-Id") == f"session-{response_mode}"
    assert list_req.header("Mcp-Session-Id") == f"session-{response_mode}"

    initialized_body = json.loads(initialized_req.body.decode("utf-8"))
    assert initialized_body == {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_jsonrpc_request_encode_omits_id_for_notification() -> None:
    """(g) JsonRpcRequest.encode omits id and params when unset."""
    notif = mcp_e2e.JsonRpcRequest(method="notifications/initialized")
    assert json.loads(notif.encode().decode("utf-8")) == {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }

    call_no_params = mcp_e2e.JsonRpcRequest(method="tools/list", id=2)
    assert json.loads(call_no_params.encode().decode("utf-8")) == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    }

    call_with_params = mcp_e2e.JsonRpcRequest(method="initialize", id=1, params={"protocolVersion": "2025-03-26"})
    assert json.loads(call_with_params.encode().decode("utf-8")) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    }


def test_tools_list_jsonrpc_error_and_null_result_raise(stub_server: StubServer) -> None:
    """tools/list JSON-RPC error or null result raises even if error mentions recall/retain."""
    stub_server.tools_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32000, "message": "recall and retain backend down"},
    }
    with pytest.raises(AssertionError, match="recall and retain backend down"):
        mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp/", "copilot-e2e")

    stub_server.tools_payload = {"jsonrpc": "2.0", "id": 2, "result": None}
    with pytest.raises(AssertionError):
        mcp_e2e.assert_lists_memory_tools(f"{stub_server.base_url}/mcp/", "copilot-e2e")


def test_parse_reply_joins_last_sse_event_and_rejects_missing_message() -> None:
    """parse_reply joins multiline data: on the final SSE event and rejects empty/non-RPC bodies."""
    sse_headers = http.client.HTTPMessage()
    sse_headers["Content-Type"] = "text/event-stream; charset=utf-8"
    sse_body = (
        ": keepalive\n"
        'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        "event: message\n"
        "data: {\n"
        'data:   "jsonrpc": "2.0",\n'
        'data:   "id": 2,\n'
        'data:   "result": {"ok": true}\n'
        "data: }\n\n"
    ).encode("utf-8")
    parsed = mcp_e2e.parse_reply(mcp_e2e.RpcResponse(status=200, headers=sse_headers, body=sse_body))
    assert parsed == mcp_e2e.JsonRpcReply(result={"ok": True}, error=None)

    with pytest.raises(AssertionError):
        mcp_e2e.parse_reply(mcp_e2e.RpcResponse(status=200, headers=sse_headers, body=b": ping\n\n"))

    json_headers = http.client.HTTPMessage()
    json_headers["Content-Type"] = "application/json"
    with pytest.raises(AssertionError):
        mcp_e2e.parse_reply(mcp_e2e.RpcResponse(status=200, headers=json_headers, body=b""))
    with pytest.raises(AssertionError):
        mcp_e2e.parse_reply(mcp_e2e.RpcResponse(status=200, headers=json_headers, body=b"{}"))

    html_headers = http.client.HTTPMessage()
    html_headers["Content-Type"] = "text/html"
    with pytest.raises(AssertionError):
        mcp_e2e.parse_reply(mcp_e2e.RpcResponse(status=200, headers=html_headers, body=b"<html>ok</html>"))


def test_scheme_allowlist_and_non_200_status(
    stub_server: StubServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """file:// URLs are rejected, health requires final 200, and rpc raises HTTPError on >=400 or unfollowed 3xx."""
    monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_URL", "file:///etc/passwd")
    assert mcp_e2e.reachable() is False

    with pytest.raises(ValueError, match="http\\(s\\)"):
        mcp_e2e.rpc("file:///etc/passwd", mcp_e2e.JsonRpcRequest(method="tools/list", id=1))

    stub_server.health_reply = StubReply(status=204, content_type=None, body=b"")
    monkeypatch.setattr(mcp_e2e, "HINDSIGHT_API_URL", stub_server.base_url)
    assert mcp_e2e.reachable() is False

    stub_server.route_overrides["/mcp-err"] = StubReply(status=503, content_type="text/plain", body=b"busy")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        mcp_e2e.rpc(f"{stub_server.base_url}/mcp-err", mcp_e2e.JsonRpcRequest(method="tools/list", id=1))
    exc_info.value.close()
    assert exc_info.value.code == 503

    stub_server.route_overrides["/mcp-302-noloc"] = StubReply(status=302, content_type="text/plain", body=b"moved")
    with pytest.raises(urllib.error.HTTPError) as exc_info_302:
        mcp_e2e.rpc(f"{stub_server.base_url}/mcp-302-noloc", mcp_e2e.JsonRpcRequest(method="tools/list", id=1))
    exc_info_302.value.close()
    assert exc_info_302.value.code == 302

    stub_server.route_overrides["/mcp-file-redir"] = StubReply(
        status=307, content_type=None, location="file:///etc/passwd"
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info_file:
        mcp_e2e.rpc(f"{stub_server.base_url}/mcp-file-redir", mcp_e2e.JsonRpcRequest(method="tools/list", id=1))
    exc_info_file.value.close()
