"""Reflection mode must consume the SDK response field, including in HTTP calls."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from hindsight_client_api.models.reflect_request import ReflectRequest
from hindsight_client_api.models.reflect_response import ReflectResponse
from pytest_httpserver import HTTPServer

from hindsight_agentcore import HindsightRuntimeAdapter, RecallPolicy, TurnContext, reset_config


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Synthesized context", "Prefers café ☕", ""])
async def test_before_turn_accepts_sdk_reflect_response(text: str) -> None:
    reset_config()
    try:
        client = MagicMock()
        client.areflect = AsyncMock(return_value=ReflectResponse(text=text))
        client.arecall = AsyncMock()
        adapter = HindsightRuntimeAdapter(recall_policy=RecallPolicy(mode="reflect", budget="high", max_tokens=1234))
        adapter._client = client
        context = TurnContext(runtime_session_id="session", user_id="user", agent_name="agent")
        assert await adapter.before_turn(context, query="preferences") == text
        client.areflect.assert_awaited_once_with(
            bank_id="user:user:agent:agent", query="preferences", budget="high", max_tokens=1234
        )
        client.arecall.assert_not_called()
    finally:
        reset_config()


@pytest.mark.asyncio
async def test_before_turn_reflects_through_actual_sdk(httpserver: HTTPServer) -> None:
    httpserver.expect_request(
        "/v1/default/banks/user:user:agent:agent/reflect",
        method="POST",
    ).respond_with_json({"text": "Synthesized project context"})
    reset_config()
    # The adapter builds its own SDK client from the URL, so the real client path is exercised.
    adapter = HindsightRuntimeAdapter(
        hindsight_api_url=httpserver.url_for("/").rstrip("/"),
        recall_policy=RecallPolicy(mode="reflect", budget="high", max_tokens=1234),
    )
    try:
        context = TurnContext(runtime_session_id="session", user_id="user", agent_name="agent")
        assert await adapter.before_turn(context, query="preferences") == "Synthesized project context"
        paths = [request.path for request, _ in httpserver.log]
        requests = [ReflectRequest.model_validate_json(request.get_data(as_text=True)) for request, _ in httpserver.log]
        assert paths == ["/v1/default/banks/user:user:agent:agent/reflect"]
        assert len(requests) == 1
        assert requests[0].query == "preferences"
        assert requests[0].budget == "high"
        assert requests[0].max_tokens == 1234
    finally:
        if adapter._client is not None:
            await adapter._client.aclose()
        reset_config()
