"""The single-read env helpers in config.py: unset and empty both mean "not set"."""

import pytest

from hindsight_api.config import _env_float, _env_int, _env_int_or, _env_str_list

VAR = "HINDSIGHT_API_TEST_ENV_READER"


@pytest.mark.parametrize("raw", [None, ""])
def test_unset_or_empty_falls_back(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(VAR, raising=False)
    else:
        monkeypatch.setenv(VAR, raw)
    assert _env_int(VAR) is None
    assert _env_float(VAR) is None
    assert _env_str_list(VAR) is None
    assert _env_int_or(VAR, 7) == 7
    assert _env_int_or(VAR, None) is None


def test_set_values_are_converted(monkeypatch):
    monkeypatch.setenv(VAR, "3")
    assert _env_int(VAR) == 3
    assert _env_int_or(VAR, 7) == 3
    assert _env_float(VAR) == 3.0
    monkeypatch.setenv(VAR, "a, b")
    assert _env_str_list(VAR) == ["a", "b"]


def test_bad_value_raises_instead_of_falling_back(monkeypatch):
    monkeypatch.setenv(VAR, "not-a-number")
    with pytest.raises(ValueError):
        _env_int(VAR)


@pytest.mark.parametrize(
    "field,default",
    [
        ("transfer_import_max_entry_size_mb", 512),
        ("transfer_import_max_total_size_mb", 8192),
        ("transfer_import_max_entries", 1_000_000),
    ],
)
def test_transfer_import_limits_are_static_and_environment_backed(
    monkeypatch: pytest.MonkeyPatch, field: str, default: int
) -> None:
    from hindsight_api.config import HindsightConfig, StaticConfigProxy

    variable = "HINDSIGHT_API_" + field.upper()
    monkeypatch.delenv(variable, raising=False)
    assert getattr(HindsightConfig.from_env(), field) == default
    monkeypatch.setenv(variable, "3")
    config = HindsightConfig.from_env()
    assert getattr(StaticConfigProxy(config), field) == 3
    assert field not in HindsightConfig.get_configurable_fields()


@pytest.mark.parametrize(
    "field", ["transfer_import_max_entry_size_mb", "transfer_import_max_total_size_mb", "transfer_import_max_entries"]
)
@pytest.mark.parametrize("raw", ["0", "-1"])
def test_transfer_import_limit_below_one_stops_startup(monkeypatch: pytest.MonkeyPatch, field: str, raw: str) -> None:
    from hindsight_api.config import HindsightConfig

    # A ceiling of 0 would refuse every archive, manifest included, at import time;
    # the operator sees the variable name at startup instead.
    variable = "HINDSIGHT_API_" + field.upper()
    monkeypatch.setenv(variable, raw)
    with pytest.raises(ValueError, match=f"{variable} must be >= 1, got {raw}"):
        HindsightConfig.from_env()


def test_transfer_import_limits_default_when_the_dataclass_is_built_directly() -> None:
    import dataclasses

    from hindsight_api.config import (
        DEFAULT_TRANSFER_IMPORT_MAX_ENTRIES,
        DEFAULT_TRANSFER_IMPORT_MAX_ENTRY_SIZE_MB,
        DEFAULT_TRANSFER_IMPORT_MAX_TOTAL_SIZE_MB,
        HindsightConfig,
    )

    # Code that builds HindsightConfig itself (tests, embedders) predates these
    # fields and passes none of them; they must not become required arguments.
    limits = {"transfer_import_max_entry_size_mb", "transfer_import_max_total_size_mb", "transfer_import_max_entries"}
    source = HindsightConfig.from_env()
    kwargs = {
        f.name: getattr(source, f.name) for f in dataclasses.fields(HindsightConfig) if f.init and f.name not in limits
    }
    config = HindsightConfig(**kwargs)
    assert config.transfer_import_max_entry_size_mb == DEFAULT_TRANSFER_IMPORT_MAX_ENTRY_SIZE_MB == 512
    assert config.transfer_import_max_total_size_mb == DEFAULT_TRANSFER_IMPORT_MAX_TOTAL_SIZE_MB == 8192
    assert config.transfer_import_max_entries == DEFAULT_TRANSFER_IMPORT_MAX_ENTRIES == 1_000_000
