"""Layered user settings shared by the coding-agent integrations.

One source, copied into each package that reads ``~/.hindsight/<agent>.json``
(aider, github-copilot, openhands, devin-desktop) by
``scripts/sync-integration-config.py``. Each package is released to PyPI on its
own, so it carries a copy instead of depending on a common package; the script's
``--check`` mode, run by ``scripts/hooks/lint.sh``, fails when a copy drifts.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

_logger = logging.getLogger(__name__)


def read_user_config(config_file: Path | None, default_file: Path) -> dict[str, object]:
    """Return the settings object in ``config_file``, or in ``default_file`` when it is ``None``.

    A missing file gives ``{}`` silently; an unreadable file, invalid JSON or
    non-object JSON gives ``{}`` and logs a warning naming the file and the reason,
    so a broken settings file falls back to the defaults and the environment
    instead of stopping the integration.
    """
    path = config_file if config_file is not None else default_file
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # A broken file fell back silently before, so a typo in the file left every
        # setting at its default with no sign of why.
        _logger.warning("Ignoring settings file %s (%s); using defaults and environment", path, exc)
        return {}
    # Before this module each package indexed the parsed value directly, so a
    # top-level JSON list or string raised (AttributeError/TypeError) in load_config.
    if not isinstance(data, dict):
        _logger.warning(
            "Ignoring settings file %s: top-level JSON is %s, not an object; using defaults and environment",
            path,
            type(data).__name__,
        )
        return {}
    return data


def apply_string_settings(cfg: object, source: Mapping[str, object], keys: Mapping[str, str]) -> None:
    """Set ``cfg.<attr>`` to ``str(source[key])`` for each non-empty value, in ``keys`` order (a later key wins)."""
    for key, attr in keys.items():
        value = source.get(key)
        if value:
            setattr(cfg, attr, str(value))
