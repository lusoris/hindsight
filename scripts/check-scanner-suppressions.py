#!/usr/bin/env python3
"""Refuse inline scanner suppressions that scanner tooling would ignore or misuse.

Semgrep and Opengrep accept inline suppression markers (nosem and nosemgrep), and Bandit accepts nosec,
which silently disable security rules. Without structured metadata and strict
expiry dates, suppressions become permanent unreviewed exemptions or silently
bind to nothing. This check scans tracked files for suppression markers and
enforces fleet policy:

- every marker semgrep honours (nosem or nosemgrep, any case, anywhere on the line) must be the
  one accepted form '<comment leader> nosemgrep: <id>[, <id>...] -- <reason>', spaced exactly;
- every suppression must name one or more valid rule IDs;
- every Semgrep suppression must give a non-empty reason after ' -- ';
- Bandit uses '<code>  # nosec B<nnn>[, B<nnn>...] # <reason>': the marker trails
  the code it suppresses, uses test ids only, and puts the reason after a second '#';
- Bandit reasons follow the same category and expiry rules below;
- the reason must declare a fleet exception category (A, B, C, or D);
- the reason must declare a valid ISO expiry date (expires YYYY-MM-DD);
- expired suppressions fail;
- categories B (<=3 days), C (<=10 days), and D (<=120 days) must not exceed
  their category duration window from today;
- comment-only Semgrep suppressions must bind to an immediately following non-blank,
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
# Bandit honours only lower-case nosec, but any case counts here so an upper-case
# marker that leaves the finding live fails instead of passing unchecked.
MARKER_RE = re.compile(r"\bnosem(?:grep)?\b|\bnosec\b", re.IGNORECASE)

# The one accepted form. The spacing is exact on purpose: with two spaces after the colon
# (`nosemgrep:  rule-x`) semgrep 1.183 drops the rule ids and suppresses every rule on the line,
# and without a space after the comment leader (`//nosemgrep:`) it ignores a trailing marker.
# The id list is captured loosely (up to the first ` -- `) and split in _marker_problems: the first
# draft spelled it `id(?:, id)*` inside this pattern, and that nested quantifier is what the
# regex-dos rule (Opengrep, via Codacy) flags. Ids hold no ` -- `, so both read the same lines.
ACCEPTED_RE = re.compile(r"(?://|#|/\*|\*|<!--)\s+nosemgrep: (?P<ids>[A-Za-z0-9_.,\- ]+?) -- (?P<reason>\S.*)$")
RULE_ID_RE = re.compile(r"[A-Za-z0-9_.\-]+")

# Measured with Bandit 1.9.4: a second '#' ends its id list, keeping the reason
# out of it. The '--' form warns on reason words and can widen suppression when
# a reason contains a test id. Require ids only, never test names or blanket markers.
NOSEC_ACCEPTED_RE = re.compile(r"\S\s+# nosec (?P<ids>[^#]+?) #(?P<reason>.*)$")
BANDIT_ID_RE = re.compile(r"B\d{3}")
MALFORMED_NOSEC = "malformed scanner suppression; expected '<code>  # nosec B<nnn>[, B<nnn>...] # <reason>'"
NOSEC_COMMENT_ONLY = "nosec marker on a comment-only line suppresses nothing; put it after the code it suppresses"

CATEGORY_RE = re.compile(r"\bcategory\s+([A-Za-z0-9_]+)\b")
EXPIRES_RE = re.compile(r"\bexpires\s+(\S+)")

MALFORMED = "malformed scanner suppression; expected 'nosemgrep: <id>[, <id>...] -- <reason>'"


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    message: str


def _binding_problem(lines: list[str], line_idx: int) -> str | None:
    """Why a comment-only marker on ``lines[line_idx]`` suppresses nothing, or ``None`` when it binds."""
    if line_idx + 1 >= len(lines):
        return "suppression comment binds to nothing (last line of file)"
    next_stripped = lines[line_idx + 1].strip()
    if not next_stripped:
        return "suppression comment binds to nothing (followed by blank line)"
    if next_stripped.startswith(COMMENT_PREFIXES):
        return "suppression comment binds to nothing (followed by comment)"
    return None


def _strip_comment_close(reason: str) -> str:
    """``reason`` without a trailing block-comment closer (``*/`` or ``-->``), stripped."""
    reason = reason.strip()
    for closer in ("*/", "-->"):
        if reason.endswith(closer):
            return reason[: -len(closer)].strip()
    return reason


def _category_problem(reason: str) -> str | None:
    """Why ``reason`` names no valid exception category, or ``None`` when it names one of A-D."""
    cat_match = CATEGORY_RE.search(reason)
    if not cat_match:
        return "suppression reason missing category (must contain 'category X' with X in A, B, C, D)"
    if cat_match.group(1) not in VALID_CATEGORIES:
        return f"invalid exception category {cat_match.group(1)!r}; must be one of A, B, C, D"
    return None


def _expiry_problem(reason: str, today: datetime.date) -> str | None:
    """Why the expiry in ``reason`` is missing, invalid, past, or beyond its category's window from ``today``.

    ``None`` when the expiry is valid.
    """
    exp_match = EXPIRES_RE.search(reason)
    if not exp_match:
        return "suppression reason missing expiry (must contain 'expires YYYY-MM-DD')"
    raw_date = exp_match.group(1).rstrip(",;.)")
    try:
        expiry_date = datetime.date.fromisoformat(raw_date)
    except ValueError:
        return f"invalid expiry date {raw_date!r}; expected YYYY-MM-DD"
    if expiry_date < today:
        return f"suppression expired on {expiry_date.isoformat()} (today is {today.isoformat()})"
    # The cap only applies to a valid category; a missing or invalid one is reported on its own.
    cat_match = CATEGORY_RE.search(reason)
    max_days = CATEGORY_INTERVALS.get(cat_match.group(1)) if cat_match else None
    if max_days is None:
        return None
    max_expiry = today + datetime.timedelta(days=max_days)
    if expiry_date > max_expiry:
        return (
            f"expiry {expiry_date.isoformat()} exceeds category {cat_match.group(1)} limit of {max_days} days "
            f"(latest allowed: {max_expiry.isoformat()})"
        )
    return None


def _marker_problems(line: str, today: datetime.date) -> list[str]:
    """Problems with the one marker on ``line``, in the order the checks run."""
    match = ACCEPTED_RE.search(line)
    if not match or not all(RULE_ID_RE.fullmatch(rule) for rule in match.group("ids").split(", ")):
        return [MALFORMED]
    reason = _strip_comment_close(match.group("reason"))
    if not reason:
        return ["suppression reason is empty"]
    return [msg for msg in (_category_problem(reason), _expiry_problem(reason, today)) if msg]


def _nosec_problems(line: str, today: datetime.date) -> list[str]:
    """Problems with a Bandit marker, which must trail the code it suppresses."""
    if line.strip().startswith(COMMENT_PREFIXES):
        return [NOSEC_COMMENT_ONLY]
    match = NOSEC_ACCEPTED_RE.search(line)
    if not match or not all(BANDIT_ID_RE.fullmatch(rule) for rule in match.group("ids").split(", ")):
        return [MALFORMED_NOSEC]
    reason = match.group("reason").strip()
    if not reason:
        return ["suppression reason is empty"]
    return [m for m in (_category_problem(reason), _expiry_problem(reason, today)) if m]


def check_text(path: str, text: str, today: datetime.date) -> list[Problem]:
    """Problems with every scanner suppression marker in ``text``, reported against ``path``."""
    problems: list[Problem] = []
    lines = text.splitlines()

    for line_idx, line in enumerate(lines):
        line_no = line_idx + 1
        markers = MARKER_RE.findall(line)
        if not markers:
            continue
        if list(map(str.lower, markers)) == ["nosec"]:
            problems.extend(Problem(path, line_no, msg) for msg in _nosec_problems(line, today))
            continue

        # A comment-only marker suppresses the next line, so it must have a code line to bind to.
        if line.strip().startswith(COMMENT_PREFIXES):
            binding = _binding_problem(lines, line_idx)
            if binding:
                problems.append(Problem(path, line_no, binding))

        if len(markers) > 1:
            problems.append(Problem(path, line_no, "line contains multiple scanner suppression markers"))
            continue

        problems.extend(Problem(path, line_no, msg) for msg in _marker_problems(line, today))

    return problems


def main(argv: list[str] | None = None, today: datetime.date | None = None) -> int:
    """Check the files in ``argv``, print each problem to stderr, and return 1 if there were any."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("files", nargs="*", help="files to scan for scanner suppressions")
    args = parser.parse_args(argv)

    if not args.files:
        return 0

    if today is None:
        # UTC, so a run on a laptop and a run in CI agree on which day an expiry falls.
        today = datetime.datetime.now(datetime.UTC).date()

    all_problems: list[Problem] = []
    for file_path in args.files:
        p = Path(file_path)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # The wrapper's `git grep -I` drops only files with NUL bytes, so a text file in another
            # encoding gets here with a marker semgrep may still honour; skipping it read as clean.
            all_problems.append(Problem(file_path, 1, "file is not valid UTF-8; cannot verify suppressions"))
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
