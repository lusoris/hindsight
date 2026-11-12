"""Gated MCP-endpoint E2E (requires_real_llm)."""

from __future__ import annotations

from hindsight_copilot.mcp_config import mcp_endpoint_url
from integration_testing.mcp_e2e import HINDSIGHT_API_URL, assert_lists_memory_tools, mcp_e2e_marks

pytestmark = mcp_e2e_marks()


def test_mcp_endpoint_lists_memory_tools() -> None:
    """The configured Copilot MCP endpoint completes the handshake and lists recall/retain."""
    url = mcp_endpoint_url(HINDSIGHT_API_URL, "copilot-e2e")
    assert_lists_memory_tools(url, "copilot-e2e")
