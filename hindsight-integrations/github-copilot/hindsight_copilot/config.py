"""Configuration for the Hindsight GitHub Copilot integration.

Settings layer (later wins): built-in defaults -> ``~/.hindsight/copilot.json``
-> environment variables. Resolved into a typed :class:`CopilotConfig`.

The integration is configuration-only: it wires the Hindsight MCP server into
VS Code's ``.vscode/mcp.json`` and writes a recall/retain rule into
``.github/copilot-instructions.md`` (which Copilot applies to every chat in the
workspace). Memory operations run through the MCP server at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._user_config import apply_string_settings, read_user_config

DEFAULT_HINDSIGHT_API_URL = "https://api.hindsight.vectorize.io"
DEFAULT_BANK_ID = "copilot"

USER_CONFIG_FILE = Path.home() / ".hindsight" / "copilot.json"


@dataclass
class CopilotConfig:
    """Resolved configuration for the Copilot MCP setup."""

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
    "HINDSIGHT_COPILOT_BANK_ID": "bank_id",
}


def load_config(config_file: Optional[Path] = None, env: Optional[dict] = None) -> CopilotConfig:
    """Load and resolve configuration from file then environment."""
    cfg = CopilotConfig()
    apply_string_settings(cfg, read_user_config(config_file, USER_CONFIG_FILE), _FILE_KEYS)
    apply_string_settings(cfg, os.environ if env is None else env, _ENV_KEYS)

    if not cfg.hindsight_api_url:
        cfg.hindsight_api_url = DEFAULT_HINDSIGHT_API_URL
    if not cfg.bank_id:
        cfg.bank_id = DEFAULT_BANK_ID

    return cfg
