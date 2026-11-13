"""Unit tests for hindsight_crewai configuration."""

from integration_testing.config_contract import ConfigApi, ConfigDefaultsContract, ConfigureContract

import hindsight_crewai
from hindsight_crewai.config import (
    DEFAULT_HINDSIGHT_API_URL,
    HINDSIGHT_API_KEY_ENV,
)

CREWAI_API = ConfigApi(
    configure=hindsight_crewai.configure,
    get_config=hindsight_crewai.get_config,
    reset_config=hindsight_crewai.reset_config,
    default_api_url=DEFAULT_HINDSIGHT_API_URL,
    api_key_env=HINDSIGHT_API_KEY_ENV,
    extra_options={"verbose": True},
    extra_defaults={"verbose": False},
)


class TestDefaults(ConfigDefaultsContract):
    api = CREWAI_API


class TestConfigure(ConfigureContract):
    api = CREWAI_API
