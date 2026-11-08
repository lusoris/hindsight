#!/usr/bin/env python3
"""Refuse inline scanner suppressions that scanner tooling would ignore or misuse.

Semgrep and Opengrep accept inline suppression markers (nosem and nosemgrep),
which silently disable security rules. Without structured metadata and strict
expiry dates, suppressions become permanent unreviewed exemptions or silently
bind to nothing. This check scans tracked files for suppression markers and
enforces fleet policy:

- every marker semgrep honours (nosem or nosemgrep, any case, anywhere on the line) must be the
  one accepted form '<comment leader> nosemgrep: <id>[, <id>...] -- <reason>', spaced exactly;
- every suppression must name one or more valid rule IDs;
- every suppression must give a non-empty reason after ' -- ';
- the reason must declare a fleet exception category (A, B, C, or D);
- the reason must declare a valid ISO expiry date (expires YYYY-MM-DD);
- expired suppressions fail;
- categories B (<=3 days), C (<=10 days), and D (<=120 days) must not exceed
  their category duration window from today;
- comment-only suppressions must bind to an immediately following non-blank,
  non-comment code line; suppressions on code lines bind to their own line.

scripts/check-scanner-suppressions.sh feeds tracked files containing markers to this script.

usage: check-scanner-suppressions.py FILE...
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CATEGORY_INTERVALS: dict[str, int] = {
    "B": 3,
    "C": 10,
    "D": 120,
}
VALID_CATEGORIES: set[str] = {"A", "B", "C", "D"}

COMMENT_PREFIXES: tuple[str, ...] = ("//", "#", "/*", "*", "<!--")

# Any spelling semgrep honours counts as a marker, wherever it sits on the line. Measured with
# semgrep 1.183: matching ignores case (`// NOSEMGREP` suppresses), and a marker after other words
# (`fetch(u); // see nosemgrep docs`) still suppresses its own line. A first draft only counted a
# marker right after the comment leader, in lower case, and so missed both.
MARKER_RE = re.compile(r"\bnosem(?:grep)?\b", re.IGNORECASE)

# The one accepted form. The spacing is exact on purpose: with two spaces after the colon
# (`nosemgrep:  rule-x`) semgrep 1.183 drops the rule ids and suppresses every rule on the line,
# and without a space after the comment leader (`//nosemgrep:`) it ignores a trailing marker.
ACCEPTED_RE = re.compile(
    r"(?://|#|/\*|\*|<!--)\s+nosemgrep: (?P<ids>[A-Za-z0-9_.\-]+(?:, [A-Za-z0-9_.\-]+)*) -- (?P<reason>\S.*)$"
)

CATEGORY_RE = re.compile(r"\bcategory\s+([A-Za-z0-9_]+)\b")
EXPIRES_RE = re.compile(r"\bexpires\s+(\S+)")


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    message: str


def check_text(path: str, text: str, today: datetime.date) -> list[Problem]:
    problems: list[Problem] = []
    lines = text.splitlines()

    for line_idx, line in enumerate(lines):
        line_no = line_idx + 1
        markers = MARKER_RE.findall(line)
        if not markers:
            continue

        stripped = line.strip()
        is_comment_only = any(stripped.startswith(prefix) for prefix in COMMENT_PREFIXES)

        # Binding check: comment-only suppression must bind to non-blank, non-comment line
        if is_comment_only:
            if line_idx + 1 >= len(lines):
                problems.append(Problem(path, line_no, "suppression comment binds to nothing (last line of file)"))
            else:
                next_stripped = lines[line_idx + 1].strip()
                if not next_stripped:
                    problems.append(
                        Problem(path, line_no, "suppression comment binds to nothing (followed by blank line)")
                    )
                elif any(next_stripped.startswith(prefix) for prefix in COMMENT_PREFIXES):
                    problems.append(
                        Problem(path, line_no, "suppression comment binds to nothing (followed by comment)")
                    )

        if len(markers) > 1:
            problems.append(Problem(path, line_no, "line contains multiple scanner suppression markers"))
            continue

        match = ACCEPTED_RE.search(line)
        if not match:
            problems.append(
                Problem(
                    path,
                    line_no,
                    "malformed scanner suppression; expected 'nosemgrep: <id>[, <id>...] -- <reason>'",
                )
            )
            continue

        clean_reason = match.group("reason").strip()
        if clean_reason.endswith("*/"):
            clean_reason = clean_reason[:-2].strip()
        elif clean_reason.endswith("-->"):
            clean_reason = clean_reason[:-3].strip()

        if not clean_reason:
            problems.append(Problem(path, line_no, "suppression reason is empty"))
            continue

        cat: str | None = None
        cat_match = CATEGORY_RE.search(clean_reason)
        if not cat_match:
            problems.append(
                Problem(
                    path,
                    line_no,
                    "suppression reason missing category (must contain 'category X' with X in A, B, C, D)",
                )
            )
        else:
            found_cat = cat_match.group(1)
            if found_cat not in VALID_CATEGORIES:
                problems.append(
                    Problem(
                        path,
                        line_no,
                        f"invalid exception category {found_cat!r}; must be one of A, B, C, D",
                    )
                )
            else:
                cat = found_cat

        exp_match = EXPIRES_RE.search(clean_reason)
        if not exp_match:
            problems.append(
                Problem(
                    path,
                    line_no,
                    "suppression reason missing expiry (must contain 'expires YYYY-MM-DD')",
                )
            )
        else:
            raw_date = exp_match.group(1).rstrip(",;.)")
            try:
                expiry_date = datetime.date.fromisoformat(raw_date)
            except ValueError:
                problems.append(
                    Problem(
                        path,
                        line_no,
                        f"invalid expiry date {raw_date!r}; expected YYYY-MM-DD",
                    )
                )
            else:
                if expiry_date < today:
                    problems.append(
                        Problem(
                            path,
                            line_no,
                            f"suppression expired on {expiry_date.isoformat()} (today is {today.isoformat()})",
                        )
                    )
                elif cat and cat in CATEGORY_INTERVALS:
                    max_days = CATEGORY_INTERVALS[cat]
                    max_expiry = today + datetime.timedelta(days=max_days)
                    if expiry_date > max_expiry:
                        problems.append(
                            Problem(
                                path,
                                line_no,
                                f"expiry {expiry_date.isoformat()} exceeds category {cat} limit of {max_days} days "
                                f"(latest allowed: {max_expiry.isoformat()})",
                            )
                        )

    return problems


def main(argv: list[str] | None = None, today: datetime.date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("files", nargs="*", help="files to scan for scanner suppressions")
    args = parser.parse_args(argv)

    if not args.files:
        return 0

    if today is None:
        today = datetime.date.today()

    all_problems: list[Problem] = []
    for file_path in args.files:
        p = Path(file_path)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            all_problems.append(Problem(file_path, 1, f"cannot read file: {exc}"))
            continue
        all_problems.extend(check_text(file_path, text, today))

    for prob in all_problems:
        print(f"{prob.path}:{prob.line}: {prob.message}", file=sys.stderr)

    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
