"""The llama.cpp prompt cache must stay in RAM, never on disk.

`llama_cpp.server` offers two prompt-cache backends. The `disk` one is backed
by diskcache, which pickles its entries (CVE-2025-69872): anyone able to write
to the cache directory gets code execution in the server process when an entry
is read back. diskcache has had no release since 5.6.3 in 2023 and no fixed
version exists, so choosing that backend is a permanent exposure rather than a
version to bump.

`ram` happens to be llama_cpp.server's own default, which is why this was never
a live vulnerability. That is exactly the problem: the safety rested on a
default in someone else's package, invisible at our call site. These tests
assert we pass the flag ourselves, so switching to the disk backend has to be a
deliberate edit rather than an inherited default that quietly changes.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hindsight_api.engine.providers import llamacpp_llm
from hindsight_api.engine.providers.llamacpp_llm import LlamaCppServer


@dataclass
class _PopenCall:
    cmd: list[str]
    env: dict[str, str] | None


def _captured_popen(monkeypatch, tmp_path, **kwargs) -> _PopenCall:
    """Start a server with the subprocess stubbed, and return the argv and env.

    `start()` writes a log file under MODELS_DIR before spawning, so point that
    at tmp_path; otherwise it raises before Popen and captures nothing.
    """
    calls: list[_PopenCall] = []

    def _fake_popen(cmd, *args, **popen_kwargs):
        """Record the argv and env instead of spawning, and return a live-looking process."""
        calls.append(_PopenCall(cmd=cmd, env=popen_kwargs.get("env")))
        proc = MagicMock()
        proc.poll.return_value = None
        return proc

    monkeypatch.setattr(llamacpp_llm, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(llamacpp_llm.subprocess, "Popen", _fake_popen)
    # _wait_for_ready polls a real socket for up to 120s. The argv is captured
    # at Popen, well before that, so skip it.
    monkeypatch.setattr(LlamaCppServer, "_wait_for_ready", AsyncMock())

    server = LlamaCppServer(model_path=Path("/tmp/model.gguf"), port=18080, **kwargs)
    asyncio.run(server.start())

    assert calls, "subprocess.Popen was never reached"
    return calls[0]


def _captured_cmd(monkeypatch, tmp_path, **kwargs) -> list[str]:
    """Start a stubbed server and return only its argv."""
    return _captured_popen(monkeypatch, tmp_path, **kwargs).cmd


def _last_cache_type(cmd: list[str]) -> str:
    """The value llama_cpp.server ends up with: argparse keeps the last one given."""
    value = None
    for i, arg in enumerate(cmd):
        if arg.startswith("--cache_t") and "=" in arg:
            value = arg.split("=", 1)[1]
        elif arg.startswith("--cache_t"):
            value = cmd[i + 1]
    assert value is not None, "no --cache_type in argv"
    return value


def test_cache_type_is_passed_explicitly(monkeypatch, tmp_path):
    """Guards CVE-2025-69872: the flag must be present, not left to a default."""
    cmd = _captured_cmd(monkeypatch, tmp_path)
    assert "--cache_type" in cmd, (
        "llama.cpp server started without an explicit --cache_type. The prompt "
        "cache backend would then follow llama_cpp.server's default, which can "
        "change without notice; the `disk` backend pickles via diskcache "
        "(CVE-2025-69872, no fixed version)."
    )


def test_cache_type_is_ram_not_disk(monkeypatch, tmp_path):
    """Without extra args the backend is `ram` and `disk` appears nowhere in argv."""
    cmd = _captured_cmd(monkeypatch, tmp_path)
    value = cmd[cmd.index("--cache_type") + 1]
    assert value == "ram", f"prompt cache backend is {value!r}, must be 'ram'"
    assert "disk" not in cmd, "the diskcache-backed prompt cache must never be selected"


@pytest.mark.parametrize(
    "extra", ["--cache_type disk", "--cache_type=disk", "--cache_t disk", "--n_threads 8 --cache_type disk"]
)
def test_extra_args_cannot_select_disk_cache(monkeypatch, tmp_path, extra):
    """HINDSIGHT_API_LLAMACPP_EXTRA_ARGS is appended to argv; the RAM flag must still win."""
    cmd = _captured_cmd(monkeypatch, tmp_path, extra_args=extra)
    assert _last_cache_type(cmd) == "ram"


@pytest.mark.parametrize(
    "extra", ["--config_file /x.json", "--config=/x.json", "--co /x.json", "--n_threads 8 --config_file=/x"]
)
def test_extra_args_refuse_config_file(monkeypatch, tmp_path, extra):
    """A config file makes llama_cpp.server ignore argv, the RAM flag included."""
    with pytest.raises(ValueError, match="config file"):
        _captured_cmd(monkeypatch, tmp_path, extra_args=extra)


def test_inherited_config_file_env_is_dropped(monkeypatch, tmp_path):
    """CONFIG_FILE in the parent environment would replace argv; the child must not inherit it."""
    monkeypatch.setenv("CONFIG_FILE", "/x.json")
    env = _captured_popen(monkeypatch, tmp_path).env
    assert env is not None and "CONFIG_FILE" not in env


def test_other_extra_args_pass_through(monkeypatch, tmp_path):
    """Unrelated extra args still reach llama_cpp.server, in order, before the forced RAM flag."""
    cmd = _captured_cmd(monkeypatch, tmp_path, extra_args="--type_k 1 --n_threads 8")
    assert cmd[-6:] == ["--type_k", "1", "--n_threads", "8", "--cache_type", "ram"]
