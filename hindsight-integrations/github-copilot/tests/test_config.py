"""Tests for config loading."""

import json
import logging

from hindsight_copilot.config import DEFAULT_BANK_ID, DEFAULT_HINDSIGHT_API_URL, load_config
from integration_testing.settings_file import (
    NON_UTF8_SETTINGS,
    assert_ignored_settings_file,
    assert_missing_file_logs_nothing,
)


def test_defaults(tmp_path):
    cfg = load_config(config_file=tmp_path / "missing.json", env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.hindsight_api_token is None
    assert cfg.bank_id == DEFAULT_BANK_ID


def test_file_values(tmp_path):
    p = tmp_path / "copilot.json"
    p.write_text(json.dumps({"hindsightApiToken": "t", "bankId": "proj"}))
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_token == "t"
    assert cfg.bank_id == "proj"


def test_env_overrides_file(tmp_path):
    p = tmp_path / "copilot.json"
    p.write_text(json.dumps({"bankId": "from-file"}))
    cfg = load_config(config_file=p, env={"HINDSIGHT_COPILOT_BANK_ID": "from-env", "HINDSIGHT_API_TOKEN": "k"})
    assert cfg.bank_id == "from-env"
    assert cfg.hindsight_api_token == "k"


def test_malformed_file_falls_back(tmp_path, caplog):
    p = tmp_path / "copilot.json"
    p.write_text("{ broken")
    caplog.set_level(logging.WARNING)
    assert load_config(config_file=p, env={}).bank_id == DEFAULT_BANK_ID
    assert_ignored_settings_file(caplog, p)


def test_non_object_json_falls_back(tmp_path, caplog):
    p = tmp_path / "copilot.json"
    p.write_text("[1, 2]")
    caplog.set_level(logging.WARNING)
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id == DEFAULT_BANK_ID
    msg = assert_ignored_settings_file(caplog, p)
    assert "list" in msg


def test_non_utf8_file_falls_back(tmp_path, caplog):
    """A settings file that is not UTF-8 falls back to the defaults instead of raising."""
    p = tmp_path / "copilot.json"
    p.write_bytes(NON_UTF8_SETTINGS)
    caplog.set_level(logging.WARNING)
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id == DEFAULT_BANK_ID
    assert_ignored_settings_file(caplog, p)


def test_missing_file_logs_nothing(tmp_path, caplog):
    """A missing settings file falls back to defaults without logging a warning."""
    assert_missing_file_logs_nothing(caplog, lambda p: load_config(config_file=p, env={}), tmp_path)
