"""Tests for config loading."""

import json
import logging

import pytest

from hindsight_aider.config import DEFAULT_HINDSIGHT_API_URL, load_config
from integration_testing.settings_file import (
    NON_UTF8_SETTINGS,
    assert_ignored_settings_file,
    assert_missing_file_logs_nothing,
)


def test_defaults(tmp_path):
    cfg = load_config(config_file=tmp_path / "missing.json", env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id is None
    assert cfg.auto_recall is True and cfg.auto_retain is True


def test_file_values(tmp_path):
    p = tmp_path / "aider.json"
    p.write_text(json.dumps({"hindsightApiToken": "t", "bankId": "b", "autoRetain": False}))
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_token == "t"
    assert cfg.bank_id == "b"
    assert cfg.auto_retain is False


def test_env_overrides_file(tmp_path):
    p = tmp_path / "aider.json"
    p.write_text(json.dumps({"bankId": "from-file"}))
    cfg = load_config(
        config_file=p, env={"HINDSIGHT_AIDER_BANK_ID": "from-env", "HINDSIGHT_AIDER_AUTO_RECALL": "false"}
    )
    assert cfg.bank_id == "from-env"
    assert cfg.auto_recall is False


def test_recall_preamble_overridable(tmp_path):
    p = tmp_path / "aider.json"
    p.write_text(json.dumps({"recallPreamble": "From file:"}))
    assert load_config(config_file=p, env={}).recall_preamble == "From file:"
    cfg = load_config(config_file=p, env={"HINDSIGHT_AIDER_RECALL_PREAMBLE": "From env:"})
    assert cfg.recall_preamble == "From env:"


def test_malformed_file_falls_back(tmp_path, caplog):
    p = tmp_path / "aider.json"
    p.write_text("{ broken")
    caplog.set_level(logging.WARNING)
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert_ignored_settings_file(caplog, p)


@pytest.mark.parametrize(
    "content",
    [
        "[1, 2]",
        # A string holding a key name passes the `key in data` test and then fails on data[key];
        # a list never does, so it alone would not notice a missing object check.
        '"bankId"',
    ],
)
def test_non_object_json_falls_back(tmp_path, content, caplog):
    p = tmp_path / "aider.json"
    p.write_text(content)
    caplog.set_level(logging.WARNING)
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id is None
    msg = assert_ignored_settings_file(caplog, p)
    assert type(json.loads(content)).__name__ in msg


def test_non_utf8_file_falls_back(tmp_path, caplog):
    """A settings file that is not UTF-8 falls back to the defaults instead of raising."""
    p = tmp_path / "aider.json"
    p.write_bytes(NON_UTF8_SETTINGS)
    caplog.set_level(logging.WARNING)
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id is None
    assert_ignored_settings_file(caplog, p)


def test_missing_file_logs_nothing(tmp_path, caplog):
    """A missing settings file falls back to defaults without logging a warning."""
    assert_missing_file_logs_nothing(caplog, lambda p: load_config(config_file=p, env={}), tmp_path)
