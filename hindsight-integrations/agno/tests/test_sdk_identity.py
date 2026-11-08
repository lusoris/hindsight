"""The integration's SDK identity must survive an actual recall request."""

from agno.run.base import RunContext
from pytest_httpserver import HTTPServer

from hindsight_agno import HindsightTools, reset_config
from hindsight_agno.tools import _USER_AGENT


def test_integration_identity_reaches_sdk_request(httpserver: HTTPServer) -> None:
    """The recall tool sends the integration User-Agent on the request the SDK makes."""
    httpserver.expect_request(
        "/v1/default/banks/test/memories/recall",
        method="POST",
    ).respond_with_json({"results": []})
    reset_config()
    tools: HindsightTools | None = None
    try:
        tools = HindsightTools(bank_id="test", hindsight_api_url=httpserver.url_for("/").rstrip("/"))
        context = RunContext(run_id="run", session_id="session", user_id="user")
        assert tools.recall_memory(context, "preferences") == "No relevant memories found."
        user_agents = [request.headers.get("User-Agent") for request, _ in httpserver.log]
        paths = [request.path for request, _ in httpserver.log]
        assert user_agents == [_USER_AGENT]
        assert paths == ["/v1/default/banks/test/memories/recall"]
    finally:
        if tools is not None:
            tools._client.close()
        reset_config()
