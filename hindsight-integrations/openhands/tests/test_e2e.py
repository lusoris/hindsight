"""End-to-end: the MCP endpoint our OpenHands config points at serves the tools.

Real-LLM bucket (``requires_real_llm``), skipped unless a Hindsight server is
reachable. Builds the same Streamable-HTTP MCP URL the integration writes into
config.toml, performs the MCP ``initialize`` handshake, and confirms
``tools/list`` returns the Hindsight memory tools — i.e. the generated config
points at a working server.

    HINDSIGHT_API_URL=http://localhost:8888 uv run pytest tests -v -m requires_real_llm
"""

from __future__ import annotations

from hindsight_openhands.openhands_config import mcp_endpoint_url
from integration_testing.mcp_e2e import HINDSIGHT_API_URL, assert_lists_memory_tools, mcp_e2e_marks

pytestmark = mcp_e2e_marks()


def test_mcp_endpoint_lists_memory_tools() -> None:
    """The configured OpenHands MCP endpoint completes the handshake and lists recall/retain."""
    url = mcp_endpoint_url(HINDSIGHT_API_URL, "openhands-e2e")
    assert_lists_memory_tools(url, "hindsight-openhands-e2e")
