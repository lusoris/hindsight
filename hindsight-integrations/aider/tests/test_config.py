"""Tests for config loading."""

import json

import pytest

from hindsight_aider.config import DEFAULT_HINDSIGHT_API_URL, load_config


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


def test_malformed_file_falls_back(tmp_path):
    p = tmp_path / "aider.json"
    p.write_text("{ broken")
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL


@pytest.mark.parametrize(
    "content",
    [
        "[1, 2]",
        # A string holding a key name passes the `key in data` test and then fails on data[key];
        # a list never does, so it alone would not notice a missing object check.
        '"bankId"',
    ],
)
def test_non_object_json_falls_back(tmp_path, content):
    p = tmp_path / "aider.json"
    p.write_text(content)
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id is None


def test_non_utf8_file_falls_back(tmp_path):
    """A settings file that is not UTF-8 falls back to the defaults instead of raising."""
    p = tmp_path / "aider.json"
    p.write_bytes(b'\xff\xfe{"bankId": "from-file"}')
    cfg = load_config(config_file=p, env={})
    assert cfg.hindsight_api_url == DEFAULT_HINDSIGHT_API_URL
    assert cfg.bank_id is None
