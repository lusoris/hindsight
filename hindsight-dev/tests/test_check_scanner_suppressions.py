"""Tests for scripts/check-scanner-suppressions.py."""

from __future__ import annotations

import datetime
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Load check-scanner-suppressions.py directly via importlib (no subprocess)
CHECKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check-scanner-suppressions.py"
spec = importlib.util.spec_from_file_location("check_scanner_suppressions", CHECKER_PATH)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)

sys.modules["check_scanner_suppressions"] = checker
spec.loader.exec_module(checker)

check_text = checker.check_text
main = checker.main

TODAY = datetime.date(2026, 11, 8)


def test_01_accepted_trailing_form_on_code_line_passes() -> None:
    text = "const x = 1; // nosemgrep: rule-a -- reason category C, expires 2026-11-18\n"
    assert check_text("test.ts", text, TODAY) == []


def test_02_accepted_comment_only_line_followed_by_code_passes() -> None:
    text = "// nosemgrep: rule-a -- reason category C, expires 2026-11-18\nconst x = 1;\n"
    assert check_text("test.ts", text, TODAY) == []


def test_03_two_rule_ids_pass() -> None:
    text = "// nosemgrep: a.b, c_d-e -- reason category C, expires 2026-11-18\nconst x = 1;\n"
    assert check_text("test.ts", text, TODAY) == []


def test_04_bare_double_slash_nosemgrep_fails() -> None:
    text = "// nosemgrep\nconst x = 1;\n"
    problems = check_text("test.ts", text, TODAY)
    assert len(problems) > 0


def test_05_bare_hash_nosem_fails() -> None:
    text = "# nosem\nconst x = 1;\n"
    problems = check_text("test.py", text, TODAY)
    assert len(problems) > 0


def test_06_nosemgrep_without_reason_fails() -> None:
    text = "// nosemgrep: rule-x\nconst x = 1;\n"
    problems = check_text("test.ts", text, TODAY)
    assert len(problems) > 0


def test_07_reason_without_category_fails() -> None:
    text = "// nosemgrep: rule-x -- reason without category expires 2026-11-18\nconst x = 1;\n"
    problems = check_text("test.ts", text, TODAY)
    assert len(problems) > 0


def test_08_reason_without_expires_fails() -> None:
    text = "// nosemgrep: rule-x -- reason with category C but no expiry date\nconst x = 1;\n"
    problems = check_text("test.ts", text, TODAY)
    assert len(problems) > 0


def test_09_invalid_date_fails() -> None:
    text = "// nosemgrep: rule-x -- reason category C, expires 2026-13-40\nconst x = 1;\n"
    problems = check_text("test.ts", text, TODAY)
    assert len(problems) > 0


def test_10_expired_date_fails() -> None:
    text = "// nosemgrep: rule-x -- reason category C, expires 2026-11-07\nconst x = 1;\n"
    problems = check_text("test.ts", text, TODAY)
    assert len(problems) > 0


def test_11_category_b_window() -> None:
    bad = "// nosemgrep: rule-x -- reason category B, expires 2026-11-12\nconst x = 1;\n"
    good = "// nosemgrep: rule-x -- reason category B, expires 2026-11-11\nconst x = 1;\n"
    assert len(check_text("test.ts", bad, TODAY)) > 0
    assert check_text("test.ts", good, TODAY) == []


def test_12_category_d_window() -> None:
    good = "// nosemgrep: rule-x -- reason category D, expires 2027-03-08\nconst x = 1;\n"
    bad = "// nosemgrep: rule-x -- reason category D, expires 2027-03-09\nconst x = 1;\n"
    assert check_text("test.ts", good, TODAY) == []
    assert len(check_text("test.ts", bad, TODAY)) > 0


def test_13_category_a_far_expiry_passes() -> None:
    text = "// nosemgrep: rule-x -- reason category A, expires 2030-01-01\nconst x = 1;\n"
    assert check_text("test.ts", text, TODAY) == []


def test_14_comment_only_marker_binding() -> None:
    """A comment-only marker before a blank line, a comment, or the end of the file binds to nothing."""
    blank_after = "// nosemgrep: rule-x -- reason category C, expires 2026-11-18\n\nconst x = 1;\n"
    last_line = "// nosemgrep: rule-x -- reason category C, expires 2026-11-18\n"
    comment_after = "// nosemgrep: rule-x -- reason category C, expires 2026-11-18\n// comment\nconst x = 1;\n"

    probs_blank = check_text("test.ts", blank_after, TODAY)
    assert any("binds to nothing" in p.message for p in probs_blank)

    probs_last = check_text("test.ts", last_line, TODAY)
    assert any("binds to nothing" in p.message for p in probs_last)

    probs_comment = check_text("test.ts", comment_after, TODAY)
    assert any("binds to nothing" in p.message for p in probs_comment)


def test_15_file_with_no_marker_gives_zero_problems() -> None:
    text = "const a = 1;\nconst b = 2;\n"
    assert check_text("test.ts", text, TODAY) == []


def test_16_main_exit_codes(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.ts"
    bad_file.write_text("// nosemgrep\nconst x = 1;\n", encoding="utf-8")

    good_file = tmp_path / "good.ts"
    good_file.write_text(
        "const x = 1; // nosemgrep: rule-a -- reason category C, expires 2026-11-18\n",
        encoding="utf-8",
    )

    assert main([str(bad_file)], today=TODAY) == 1
    assert main([str(good_file)], today=TODAY) == 0
    assert main([], today=TODAY) == 0


def test_22_file_that_is_not_utf8_fails(tmp_path: Path, capsys) -> None:
    # Latin-1 text with no NUL byte passes the wrapper's `git grep -I`; it must not read as clean.
    latin1 = tmp_path / "latin1.py"
    latin1.write_bytes("x = 1  # nosemgrep: rule-a -- café category C, expires 2026-11-18\n".encode("latin-1"))
    assert main([str(latin1)], today=TODAY) == 1
    assert "not valid UTF-8" in capsys.readouterr().err


# Cases 18-21 are markers semgrep 1.183 honours (or silently ignores) that a narrower reader missed.


def test_18_upper_case_marker_fails() -> None:
    text = "// NOSEMGREP\nconst x = 1;\n"
    assert len(check_text("test.ts", text, TODAY)) > 0


def test_19_marker_after_other_words_on_a_code_line_fails() -> None:
    text = "const r = fetch(u); // see nosemgrep docs\n"
    assert len(check_text("test.ts", text, TODAY)) > 0


def test_20_two_spaces_after_colon_fails() -> None:
    text = "// nosemgrep:  rule-x -- reason category C, expires 2026-11-18\nconst x = 1;\n"
    assert len(check_text("test.ts", text, TODAY)) > 0


def test_21_no_space_after_comment_leader_fails() -> None:
    text = "const x = 1; //nosemgrep: rule-x -- reason category C, expires 2026-11-18\n"
    assert len(check_text("test.ts", text, TODAY)) > 0


def test_17_real_knowledge_base_proxy_file_passes() -> None:
    real_path = (
        Path(__file__).resolve().parents[2] / "hindsight-control-plane" / "src" / "lib" / "knowledge-base-proxy.ts"
    )
    text = real_path.read_text(encoding="utf-8")
    assert check_text(str(real_path), text, TODAY) == []


def test_23_nosec_with_ids_and_metadata_passes() -> None:
    text = "x = 1  # nosec B608 # bound values category D, expires 2027-01-01\n"
    assert check_text("test.py", text, TODAY) == []


@pytest.mark.parametrize(
    "marker,message",
    [
        ("# nosec B608, B110 # bound values category D, expires 2027-01-01", checker.UNKNOWN_BANDIT_ID),
        ("# nosec B999 # bound values category D, expires 2027-01-01", checker.UNKNOWN_BANDIT_ID),
        ("# nosec B608, B999 # bound values category D, expires 2027-01-01", checker.UNKNOWN_BANDIT_ID),
        ("# nosec B608", checker.MALFORMED_NOSEC),
        ("# nosec B608 #", "suppression reason is empty"),
        (
            "# nosec B608 # bound values category D",
            "suppression reason missing expiry (must contain 'expires YYYY-MM-DD')",
        ),
        (
            "# nosec B608 # bound values expires 2027-01-01",
            "suppression reason missing category (must contain 'category X' with X in A, B, C, D)",
        ),
        (
            "# nosec B608 # bound values category D, expires 2026-11-07",
            "suppression expired on 2026-11-07 (today is 2026-11-08)",
        ),
        (
            "# nosec B608 # bound values category D, expires 2027-03-09",
            "expiry 2027-03-09 exceeds category D limit of 120 days (latest allowed: 2027-03-08)",
        ),
        ("# nosec B608 -- bound values category D, expires 2027-01-01", checker.MALFORMED_NOSEC),
        ("# NOSEC B608 # bound values category D, expires 2027-01-01", checker.MALFORMED_NOSEC),
        ("# nosec # bound values category D, expires 2027-01-01", checker.MALFORMED_NOSEC),
        ("#nosec B608 # bound values category D, expires 2027-01-01", checker.MALFORMED_NOSEC),
        (
            "# nosec hardcoded_sql_expressions # bound values category D, expires 2027-01-01",
            checker.MALFORMED_NOSEC,
        ),
    ],
)
def test_24_nosec_invalid_forms_fail(marker: str, message: str) -> None:
    assert check_text("test.py", f"x = 1  {marker}\n", TODAY) == [checker.Problem("test.py", 1, message)]


def test_25_nosec_comment_only_suppresses_nothing() -> None:
    text = '# nosec B608 # bound values category D, expires 2027-01-01\nsql = f"SELECT {cols} FROM t"\n'
    assert check_text("test.py", text, TODAY) == [checker.Problem("test.py", 1, checker.NOSEC_COMMENT_ONLY)]


def test_26_nosec_with_another_marker_fails() -> None:
    text = "x = 1  # nosec B608 # see nosemgrep: rule-a -- bound values category D, expires 2027-01-01\n"
    assert check_text("test.py", text, TODAY) == [
        checker.Problem("test.py", 1, "line contains multiple scanner suppression markers")
    ]


# This is the entry date, not TODAY: TODAY's 120-day D window ends 2027-03-08,
# before the reviewed marker's expiry on 2027-03-12.
IMPORTER_ENTRY_DATE = datetime.date(2026, 11, 12)


def test_27_real_importer_marker_passes() -> None:
    path = CHECKER_PATH.parent.parent / "hindsight-api-slim/hindsight_api/engine/transfer/importer.py"
    text = path.read_text(encoding="utf-8")
    assert text.count("# nosec B608 #") == 1
    assert check_text(str(path), text, IMPORTER_ENTRY_DATE) == []


@pytest.mark.parametrize("marker", ["# nosec B608", "# nosecB608 # bound values category D, expires 2027-01-01"])
def test_28_wrapper_finds_nosec_markers(tmp_path: Path, marker: str) -> None:
    git_bin = shutil.which("git")
    bash_bin = shutil.which("bash")
    assert git_bin is not None and bash_bin is not None, "git and bash must be on PATH"

    # Keep inherited Git settings out of the fixture repository and ignore user/system config.
    fixture_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    fixture_env["GIT_CONFIG_GLOBAL"] = os.devnull
    fixture_env["GIT_CONFIG_NOSYSTEM"] = "1"

    def git(*args: str) -> None:
        """Run git with the fixture's isolated configuration."""
        subprocess.run([git_bin, *args], cwd=tmp_path, env=fixture_env, check=True)

    def run_wrapper() -> subprocess.CompletedProcess[str]:
        """Run the suppression wrapper in the fixture repository."""
        return subprocess.run(
            [bash_bin, "scripts/check-scanner-suppressions.sh"],
            cwd=tmp_path,
            env=fixture_env,
            capture_output=True,
            text=True,
            check=False,
        )

    git("init", "-q")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("check-scanner-suppressions.py", "check-scanner-suppressions.sh"):
        shutil.copyfile(CHECKER_PATH.parent / name, scripts / name)
    bad = tmp_path / "bad.py"
    bad.write_text(f"x = 1  {marker}\n", encoding="utf-8")
    git("add", "-A")
    result = run_wrapper()
    assert result.returncode == 1
    assert "bad.py:1:" in result.stderr
    assert checker.MALFORMED_NOSEC in result.stderr
    expiry = datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=30)
    bad.write_text(f"x = 1  # nosec B608 # bound values category D, expires {expiry.isoformat()}\n", encoding="utf-8")
    git("add", "-A")
    result = run_wrapper()
    assert result.returncode == 0


def test_29_nosec_without_separator_is_discovered_and_rejected() -> None:
    text = "x = 1  # nosecB608 # bound values category D, expires 2027-01-01\n"
    assert checker.MARKER_RE.findall(text) == ["nosec"]
    assert check_text("test.py", text, TODAY) == [checker.Problem("test.py", 1, checker.MALFORMED_NOSEC)]
