"""Configuration for the Hindsight OpenHands integration.

Settings layer (later wins): built-in defaults -> ``~/.hindsight/openhands.json``
-> environment variables. Resolved into a typed :class:`OpenHandsConfig`.

The integration is configuration-only: it wires the Hindsight MCP server into
OpenHands' ``config.toml`` (the ``[mcp]`` section) and writes a recall/retain
rule into the project's ``AGENTS.md``. Memory operations run through the MCP
server at runtime, so there is no daemon or direct API client here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._user_config import apply_string_settings, read_user_config

# Cross-integration cloud-default convention.
DEFAULT_HINDSIGHT_API_URL = "https://api.hindsight.vectorize.io"
DEFAULT_BANK_ID = "openhands"

USER_CONFIG_FILE = Path.home() / ".hindsight" / "openhands.json"


@dataclass
class OpenHandsConfig:
    """Resolved configuration for the OpenHands MCP setup."""

    hindsight_api_url: str = DEFAULT_HINDSIGHT_API_URL
    hindsight_api_token: Optional[str] = None
    # The memory bank the MCP server is scoped to (the last path segment of the
    # MCP endpoint URL).
    bank_id: str = DEFAULT_BANK_ID


_FILE_KEYS = {
    "hindsightApiUrl": "hindsight_api_url",
    "hindsightApiToken": "hindsight_api_token",
    "bankId": "bank_id",
}

_ENV_KEYS = {
    "HINDSIGHT_API_URL": "hindsight_api_url",
    "HINDSIGHT_API_TOKEN": "hindsight_api_token",
    "HINDSIGHT_OPENHANDS_BANK_ID": "bank_id",
}


def load_config(config_file: Optional[Path] = None, env: Optional[dict] = None) -> OpenHandsConfig:
    """Load and resolve configuration from file then environment."""
    cfg = OpenHandsConfig()
    apply_string_settings(cfg, read_user_config(config_file, USER_CONFIG_FILE), _FILE_KEYS)
    apply_string_settings(cfg, os.environ if env is None else env, _ENV_KEYS)

    if not cfg.hindsight_api_url:
        cfg.hindsight_api_url = DEFAULT_HINDSIGHT_API_URL
    if not cfg.bank_id:
        cfg.bank_id = DEFAULT_BANK_ID

    return cfg
