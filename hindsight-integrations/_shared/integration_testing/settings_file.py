"""Shared settings-file fallback assertions for aider, devin-desktop and github-copilot.

Test-only module. These integrations put ``hindsight-integrations/_shared`` on the
test path with ``pythonpath = ["../_shared"]`` under ``[tool.pytest.ini_options]``.
Their ``tests/`` directories stay in the monorepo and are not in published wheels,
so the tests import this one file by path instead of copying it into each package.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

NON_UTF8_SETTINGS: bytes = b'\xff\xfe{"bankId": "from-file"}'


def assert_ignored_settings_file(caplog: pytest.LogCaptureFixture, path: Path) -> str:
    """Assert one WARNING naming the ignored file and return it for extra checks."""
    # Explicit failures also work without pytest assertion rewriting in this helper.
    if len(caplog.records) != 1:
        raise AssertionError(f"Expected exactly one settings warning, got {len(caplog.records)} records")
    record = caplog.records[0]
    if record.levelno != logging.WARNING:
        raise AssertionError(f"Expected WARNING, got {record.levelname}")
    message = record.getMessage()
    if str(path) not in message:
        raise AssertionError(f"Settings warning does not name {path}: {message}")
    if "Ignoring settings file" not in message:
        raise AssertionError(f"Settings warning does not say 'Ignoring settings file': {message}")
    return message


def assert_missing_file_logs_nothing(
    caplog: pytest.LogCaptureFixture, load: Callable[[Path], object], tmp_path: Path
) -> None:
    """Load a missing settings file and assert that no warning was logged."""
    caplog.set_level(logging.WARNING)
    load(tmp_path / "missing.json")
    if caplog.records:
        raise AssertionError(f"Expected no log records for a missing settings file, got {caplog.records}")
