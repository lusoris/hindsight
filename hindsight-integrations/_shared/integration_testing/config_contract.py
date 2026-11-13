"""Shared configuration API contracts for CrewAI, Pydantic AI and OpenAI Agents.

Test-only module. Each integration puts ``hindsight-integrations/_shared`` on the
test path with ``pythonpath = ["../_shared"]`` under ``[tool.pytest.ini_options]``.
Integration ``tests/`` directories stay in the monorepo and are not in published
wheels, so the tests import this one file by path instead of copying it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch


@dataclass(frozen=True)
class ConfigApi:
    """Package configuration functions, constants and package-specific options."""

    configure: Callable[..., Any]
    get_config: Callable[[], Any]
    reset_config: Callable[[], None]
    default_api_url: str
    api_key_env: str
    # Options only some packages accept (crewai/pydantic-ai: verbose=True), passed to configure()
    # in the all-options test and asserted back on the returned config.
    extra_options: Mapping[str, Any] = field(default_factory=dict)
    # Defaults beyond url/budget/max_tokens that a bare configure() must return
    # (crewai/pydantic-ai: verbose=False; openai-agents: api_key/tags/recall_tags None, recall_tags_match "any").
    extra_defaults: Mapping[str, Any] = field(default_factory=dict)


# Explicit failures instead of `assert`: pytest rewrites asserts only in test modules, so in
# this by-path helper a bare assert would fail without the compared values, and `python -O`
# strips it (Bandit B101, which Codacy applies here because the file is outside `tests/`).
def _expect(name: str, actual: Any, expected: Any) -> None:
    # None and bools compare by identity so a truthy stand-in (1, "x") cannot pass.
    same = actual is expected if expected is None or isinstance(expected, bool) else actual == expected
    if not same:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")


def _expect_attributes(config: Any, expected: Mapping[str, Any]) -> None:
    for name, value in expected.items():
        _expect(name, getattr(config, name), value)


class ConfigDefaultsContract:
    """Constants shared by all configuration APIs."""

    api: ConfigApi

    def test_default_api_url(self) -> None:
        _expect("default_api_url", self.api.default_api_url, "https://api.hindsight.vectorize.io")

    def test_env_var_name(self) -> None:
        _expect("api_key_env", self.api.api_key_env, "HINDSIGHT_API_KEY")


class ConfigureContract:
    """Configuration defaults, overrides and global state lifecycle."""

    api: ConfigApi

    def setup_method(self) -> None:
        self.api.reset_config()

    def teardown_method(self) -> None:
        self.api.reset_config()

    def test_configure_with_no_arguments(self) -> None:
        config = self.api.configure()
        _expect_attributes(
            config,
            {"hindsight_api_url": self.api.default_api_url, "budget": "mid", "max_tokens": 4096},
        )
        _expect_attributes(config, self.api.extra_defaults)

    def test_configure_reads_api_key_from_env(self) -> None:
        with patch.dict(os.environ, {self.api.api_key_env: "test-key"}):
            config = self.api.configure()
        _expect("api_key", config.api_key, "test-key")

    def test_configure_explicit_overrides_env(self) -> None:
        with patch.dict(os.environ, {self.api.api_key_env: "env-key"}):
            config = self.api.configure(api_key="explicit-key")
        _expect("api_key", config.api_key, "explicit-key")

    def test_configure_all_options(self) -> None:
        options: dict[str, Any] = {
            "hindsight_api_url": "http://custom:8888",
            "api_key": "my-key",
            "budget": "high",
            "max_tokens": 2048,
            "tags": ["env:test"],
            "recall_tags": ["scope:global"],
            "recall_tags_match": "all",
            **self.api.extra_options,
        }
        config = self.api.configure(**options)
        _expect_attributes(config, options)

    def test_get_config_returns_none_without_configure(self) -> None:
        _expect("get_config()", self.api.get_config(), None)

    def test_get_config_returns_config_after_configure(self) -> None:
        self.api.configure()
        _expect("get_config() is set", self.api.get_config() is not None, True)

    def test_reset_config(self) -> None:
        self.api.configure()
        _expect("get_config() is set", self.api.get_config() is not None, True)
        self.api.reset_config()
        _expect("get_config() after reset", self.api.get_config(), None)
