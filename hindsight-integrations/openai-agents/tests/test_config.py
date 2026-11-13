"""Unit tests for hindsight_openai_agents configuration."""

import hindsight_openai_agents
from hindsight_openai_agents.config import (
    DEFAULT_BUDGET,
    DEFAULT_HINDSIGHT_API_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_RECALL_TAGS_MATCH,
    HINDSIGHT_API_KEY_ENV,
)
from integration_testing.config_contract import ConfigApi, ConfigDefaultsContract, ConfigureContract

OPENAI_AGENTS_API = ConfigApi(
    configure=hindsight_openai_agents.configure,
    get_config=hindsight_openai_agents.get_config,
    reset_config=hindsight_openai_agents.reset_config,
    default_api_url=DEFAULT_HINDSIGHT_API_URL,
    api_key_env=HINDSIGHT_API_KEY_ENV,
    extra_defaults={"api_key": None, "tags": None, "recall_tags": None, "recall_tags_match": "any"},
)


class TestDefaults(ConfigDefaultsContract):
    api = OPENAI_AGENTS_API

    def test_default_budget(self) -> None:
        assert DEFAULT_BUDGET == "mid"

    def test_default_max_tokens(self) -> None:
        assert DEFAULT_MAX_TOKENS == 4096

    def test_default_recall_tags_match(self) -> None:
        assert DEFAULT_RECALL_TAGS_MATCH == "any"


class TestConfigure(ConfigureContract):
    api = OPENAI_AGENTS_API

    def test_configure_returns_config_object(self) -> None:
        config = self.api.configure()
        assert config is self.api.get_config()
