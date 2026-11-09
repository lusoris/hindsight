"""Tests for Codacy configuration validation.

Codacy exclusions must carry an explicit category, valid entry and expiry dates within
allowed category intervals, match the active engine, and point to existing files.
Without this verification, exceptions silently drift, expire unnoticed, or refer to
stale paths, violating the repository's duplication standards.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-codacy-config.py"


def _load_check_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_codacy_config", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves the module through sys.modules, so register it before running it.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


check_mod = _load_check_module()

TODAY = datetime.date(2026, 11, 8)


def test_real_repo_codacy_config_passes():
    """The real repo .codacy.yml passes with today 2026-11-08."""
    assert check_mod.main(["--root", str(REPO_ROOT)], today=TODAY) == 0


def _setup_tmp_codacy(tmp_path: Path, codacy_text: str) -> Path:
    """Helper to write a temporary .codacy.yml and dummy target file."""
    (tmp_path / "foo.py").touch()
    codacy_file = tmp_path / ".codacy.yml"
    codacy_file.write_text(codacy_text, encoding="utf-8")
    return codacy_file


def test_missing_comment_fails(tmp_path: Path, capsys):
    """An exclusion item without a preceding comment fails."""
    text = '---\nengines:\n  duplication:\n    exclude_paths:\n      - "foo.py"\n'
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert ".codacy.yml:5:" in captured.err
    assert "missing" in captured.err.lower()


def test_malformed_comment_fails(tmp_path: Path, capsys):
    """An exclusion item with a malformed comment fails."""
    text = (
        '---\nengines:\n  duplication:\n    exclude_paths:\n      # not a valid exception comment\n      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert ".codacy.yml:6:" in captured.err
    assert "malformed" in captured.err.lower()


def test_expired_fails(tmp_path: Path, capsys):
    """An exclusion whose expiry date is in the past fails."""
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-01, expiry 2026-11-07, rule duplication: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "expired" in captured.err.lower()


def test_d_window_120_days_passes_and_121_fails(tmp_path: Path):
    """Category D allows up to 120 days from entry, but fails at 121 days."""
    text_120 = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text_120)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 0

    text_121 = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-09, rule duplication: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text_121)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1


def test_entry_in_future_fails(tmp_path: Path, capsys):
    """An entry date in the future fails."""
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-09, expiry 2027-03-08, rule duplication: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "future" in captured.err.lower()


def test_rule_not_matching_engine_fails(tmp_path: Path, capsys):
    """A rule in the comment that does not match the enclosing engine fails."""
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-08, rule eslint: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "does not match" in captured.err.lower()


def test_pattern_matching_nothing_fails(tmp_path: Path, capsys):
    """An exclusion whose pattern matches no files under root fails."""
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
        '      - "nonexistent_file.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "matches no files" in captured.err.lower()


def test_unrecognised_line_fails(tmp_path: Path, capsys):
    """Any unrecognised line fails with an instruction to extend the script."""
    text = "---\nfoo: bar\n"
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "unrecognised line; extend scripts/check-codacy-config.py" in captured.err


def test_invalid_date_fails(tmp_path: Path, capsys):
    """An entry or expiry that is not an ISO date fails."""
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-02-30, rule duplication: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    assert "invalid date" in capsys.readouterr().err


def test_file_that_is_not_utf8_fails(tmp_path: Path, capsys):
    """A .codacy.yml that is not valid UTF-8 is reported, not a traceback."""
    (tmp_path / ".codacy.yml").write_bytes("---\n# caf\u00e9\n".encode("latin-1"))
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    assert "not valid UTF-8" in capsys.readouterr().err


def test_unsupported_top_level_key_fails(tmp_path: Path, capsys):
    """An unsupported top-level key fails naming the key."""
    text = (
        "---\n"
        "exclude_paths:\n"
        "  # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
        '  - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "unsupported key 'exclude_paths'" in captured.err


def test_unsupported_engine_level_key_fails(tmp_path: Path, capsys):
    """An unsupported engine-level key under an engine fails naming the key."""
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    include_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
        '      - "foo.py"\n'
    )
    _setup_tmp_codacy(tmp_path, text)
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "unsupported key 'include_paths'" in captured.err


def test_compact_and_week_dates_fail(tmp_path: Path, capsys):
    """A compact date (20261108) and a week date (2026-W45-7) each fail with the YYYY-MM-DD message."""
    for bad_date in ("20261108", "2026-W45-7"):
        text = (
            "---\n"
            "engines:\n"
            "  duplication:\n"
            "    exclude_paths:\n"
            f"      # category D, entry {bad_date}, expiry 2027-03-08, rule duplication: reason\n"
            '      - "foo.py"\n'
        )
        _setup_tmp_codacy(tmp_path, text)
        assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
        captured = capsys.readouterr()
        assert "expected YYYY-MM-DD" in captured.err


def test_pattern_matching_only_directory_fails(tmp_path: Path, capsys):
    """A pattern matching only an empty directory fails."""
    (tmp_path / "emptydir").mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
        '      - "emptydir"\n'
    )
    codacy_file = tmp_path / ".codacy.yml"
    codacy_file.write_text(text, encoding="utf-8")
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
    captured = capsys.readouterr()
    assert "matches no files under" in captured.err


def test_absolute_and_dotdot_patterns_fail(tmp_path: Path, capsys):
    """An absolute pattern and a ../x pattern fail with the relative-path message without raising."""
    for pattern in ("/absolute/path.py", "../x"):
        text = (
            "---\n"
            "engines:\n"
            "  duplication:\n"
            "    exclude_paths:\n"
            "      # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
            f'      - "{pattern}"\n'
        )
        _setup_tmp_codacy(tmp_path, text)
        assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 1
        captured = capsys.readouterr()
        assert "must be relative to the repository root without '..'" in captured.err


def test_doublestar_recursive_file_match_passes(tmp_path: Path):
    """A pattern somedir/** with a file two levels below somedir passes."""
    nested = tmp_path / "somedir" / "level1" / "level2"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "nested_file.py").touch()
    text = (
        "---\n"
        "engines:\n"
        "  duplication:\n"
        "    exclude_paths:\n"
        "      # category D, entry 2026-11-08, expiry 2027-03-08, rule duplication: reason\n"
        '      - "somedir/**"\n'
    )
    codacy_file = tmp_path / ".codacy.yml"
    codacy_file.write_text(text, encoding="utf-8")
    assert check_mod.main(["--root", str(tmp_path)], today=TODAY) == 0
