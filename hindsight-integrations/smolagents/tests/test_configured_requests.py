"""Verify configured defaults reach the API through the real tools and SDK."""

from dataclasses import dataclass

import pytest
from hindsight_client import Hindsight
from hindsight_client_api.models.recall_request import RecallRequest
from hindsight_client_api.models.reflect_request import ReflectRequest
from pytest_httpserver import HTTPServer

from hindsight_smolagents import configure, create_hindsight_tools, reset_config


@dataclass(frozen=True)
class CapturedRequest:
    path: str
    payload_json: str


@pytest.mark.parametrize("explicit", [False, True])
def test_factory_configuration_reaches_api(httpserver: HTTPServer, explicit: bool) -> None:
    """Global configure() values reach recall and reflect requests, and explicit factory arguments override them."""
    httpserver.expect_request(
        "/v1/default/banks/test/memories/recall",
        method="POST",
    ).respond_with_json({"results": []})
    httpserver.expect_request(
        "/v1/default/banks/test/reflect",
        method="POST",
    ).respond_with_json({"text": "reflection"})
    reset_config()
    try:
        configure(budget="high", max_tokens=1234, recall_tags=["team"], recall_tags_match="all")
        client = Hindsight(base_url=httpserver.url_for("/").rstrip("/"), timeout=2.0)
        if explicit:
            created_tools = create_hindsight_tools(
                bank_id="test", client=client, budget="mid", max_tokens=4096, recall_tags_match="any"
            )
        else:
            created_tools = create_hindsight_tools(bank_id="test", client=client)
        tools = {tool.name: tool for tool in created_tools}
        assert tools["hindsight_recall"](query="preferences") == "No relevant memories found."
        assert tools["hindsight_reflect"](query="preferences") == "reflection"
        requests = [
            CapturedRequest(path=request.path, payload_json=request.get_data(as_text=True))
            for request, _ in httpserver.log
        ]
        recall = RecallRequest.model_validate_json(requests[0].payload_json)
        reflect = ReflectRequest.model_validate_json(requests[1].payload_json)
        assert requests[0].path == "/v1/default/banks/test/memories/recall"
        assert requests[1].path == "/v1/default/banks/test/reflect"
        assert recall.query == reflect.query == "preferences"
        assert recall.budget == reflect.budget == ("mid" if explicit else "high")
        assert recall.max_tokens == (4096 if explicit else 1234)
        assert recall.tags == ["team"]
        assert recall.tags_match == ("any" if explicit else "all")
    finally:
        reset_config()
