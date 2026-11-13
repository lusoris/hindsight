"""Unit tests for hindsight_pydantic_ai configuration."""

import hindsight_pydantic_ai
from hindsight_pydantic_ai.config import (
    DEFAULT_HINDSIGHT_API_URL,
    HINDSIGHT_API_KEY_ENV,
)
from integration_testing.config_contract import ConfigApi, ConfigDefaultsContract, ConfigureContract

PYDANTIC_AI_API = ConfigApi(
    configure=hindsight_pydantic_ai.configure,
    get_config=hindsight_pydantic_ai.get_config,
    reset_config=hindsight_pydantic_ai.reset_config,
    default_api_url=DEFAULT_HINDSIGHT_API_URL,
    api_key_env=HINDSIGHT_API_KEY_ENV,
    extra_options={"verbose": True},
    extra_defaults={"verbose": False},
)


class TestDefaults(ConfigDefaultsContract):
    api = PYDANTIC_AI_API


class TestConfigure(ConfigureContract):
    api = PYDANTIC_AI_API
