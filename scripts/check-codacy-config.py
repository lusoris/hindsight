#!/usr/bin/env python3
"""Validate .codacy.yml exclusions against category limits, engine scopes, and active files.

Every Codacy exclusion is an exception to a scanner rule, so each list item must sit
directly under a comment of the form

    # category <A|B|C|D>, entry YYYY-MM-DD, expiry YYYY-MM-DD, rule <engine>: <reason>

where the expiry is not past and stays within the category's window from entry
(B 3, C 10, D 120 days), the rule names the engine the item is listed under, and
the path pattern still matches a file. The reader is an allow-list: any line it
does not recognise fails, so a new key in .codacy.yml has to be taught here first
instead of passing unchecked.

usage: check-codacy-config.py [--root ROOT]
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BLANK_RE = re.compile(r"^\s*$")
DOC_START_RE = re.compile(r"^---\s*$")
COMMENT_LINE_RE = re.compile(r"^\s*#")
KEY_RE = re.compile(r"^( *)([a-z_]+):\s*$")
LIST_ITEM_RE = re.compile(r'^ +- "?([^"\s]+)"?\s*$')
COMMENT_RE = re.compile(r"^ *# category ([ABCD]), entry (\S+), expiry (\S+), rule ([a-z_]+): (\S.*)$")

INTERVALS: dict[str, int] = {
    "B": 3,
    "C": 10,
    "D": 120,
}


@dataclass
class _Section:
    """Where the reader is in the YAML tree: inside top-level ``engines``, and under which engine."""

    in_engines: bool = False
    engine: str | None = None


def _enter_key(section: _Section, indent: int, key: str) -> None:
    if indent == 0:
        section.in_engines = key == "engines"
        section.engine = None
    elif section.in_engines and indent == 2:
        section.engine = key


def _date_problems(category: str, entry_str: str, expiry_str: str, today: datetime.date) -> list[str]:
    try:
        entry = datetime.date.fromisoformat(entry_str)
        expiry = datetime.date.fromisoformat(expiry_str)
    except ValueError:
        return [f"invalid date in entry {entry_str!r} or expiry {expiry_str!r}; expected YYYY-MM-DD"]
    problems: list[str] = []
    if entry > today:
        problems.append(f"entry date {entry_str} is in the future")
    if expiry < today:
        problems.append(f"exception expired on {expiry_str}")
    max_days = INTERVALS.get(category)
    if max_days is not None and expiry > entry + datetime.timedelta(days=max_days):
        problems.append(f"category {category} expiry {expiry_str} exceeds {max_days} days from entry {entry_str}")
    return problems


def _item_problems(
    comment_line: str | None, pattern: str, engine: str | None, root: Path, today: datetime.date
) -> list[str]:
    """Problems with one exclusion item, given the line directly above it (``None`` on line 1)."""
    if comment_line is None or not COMMENT_LINE_RE.match(comment_line):
        return ["missing exception comment"]
    match = COMMENT_RE.match(comment_line)
    if not match:
        return ["malformed exception comment"]
    category, entry_str, expiry_str, rule, _reason = match.groups()
    problems = _date_problems(category, entry_str, expiry_str, today)
    if rule != engine:
        problems.append(f"rule {rule!r} does not match current engine {engine!r}")
    if not any(root.glob(pattern)):
        problems.append(f"pattern {pattern!r} matches no files under {root}")
    return problems


def check_text(text: str, root: Path, today: datetime.date) -> list[str]:
    """Problems in ``text`` (a .codacy.yml), each as ``.codacy.yml:<line>: <message>``."""
    errors: list[str] = []
    lines = text.splitlines()
    section = _Section()
    for i, line in enumerate(lines):
        if BLANK_RE.match(line) or DOC_START_RE.match(line) or COMMENT_LINE_RE.match(line):
            continue
        key_match = KEY_RE.match(line)
        if key_match:
            _enter_key(section, len(key_match.group(1)), key_match.group(2))
            continue
        item_match = LIST_ITEM_RE.match(line)
        if item_match:
            above = lines[i - 1] if i > 0 else None
            problems = _item_problems(above, item_match.group(1), section.engine, root, today)
        else:
            problems = ["unrecognised line; extend scripts/check-codacy-config.py"]
        errors.extend(f".codacy.yml:{i + 1}: {msg}" for msg in problems)
    return errors


def main(argv: list[str] | None = None, today: datetime.date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root directory",
    )
    args = parser.parse_args(argv)

    if today is None:
        # UTC, so a run on a laptop and a run in CI agree on which day an expiry falls.
        today = datetime.datetime.now(datetime.UTC).date()

    root: Path = args.root
    codacy_path = root / ".codacy.yml"
    try:
        text = codacy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors = [f".codacy.yml: file not found under {root}"]
    except UnicodeDecodeError:
        errors = [".codacy.yml: file is not valid UTF-8; cannot verify exclusions"]
    else:
        errors = check_text(text, root, today)
    for err in errors:
        print(err, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
