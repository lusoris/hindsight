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
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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
    list_key: str | None = None


def _enter_key(section: _Section, indent: int, key: str) -> list[str]:
    """Update ``section`` for a key line at ``indent`` and return any validation problems.

    Keys are allow-listed by position: ``engines`` at the top, any engine name directly
    under it, and ``exclude_paths`` under an engine. Any other key, or a known key at
    another position, fails, so a new .codacy.yml key cannot pass unchecked.
    """
    if indent == 0:
        section.in_engines = key == "engines"
        section.engine = None
        section.list_key = None
        known = section.in_engines
    elif indent == 2:
        section.engine = key if section.in_engines else None
        section.list_key = None
        known = section.in_engines
    else:
        # section.engine is only ever set inside engines, so this also implies in_engines.
        known = indent == 4 and section.engine is not None and key == "exclude_paths"
        section.list_key = key if known else None
    return [] if known else [f"unsupported key {key!r}; extend scripts/check-codacy-config.py"]


def _date_problems(category: str, entry_str: str, expiry_str: str, today: datetime.date) -> list[str]:
    """Problems with the entry and expiry dates for ``category`` as of ``today``."""
    if not DATE_RE.match(entry_str) or not DATE_RE.match(expiry_str):
        return [f"invalid date in entry {entry_str!r} or expiry {expiry_str!r}; expected YYYY-MM-DD"]
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


def _matches_a_file(root: Path, pattern: str) -> bool:
    """Return whether ``pattern`` (relative, without ``..``) matches a regular file inside ``root``."""
    resolved_root = root.resolve()

    def _is_file_in_root(p: Path) -> bool:
        # resolve() follows symlinks, so a link that points outside the repository does not count.
        try:
            return p.is_file() and p.resolve().is_relative_to(resolved_root)
        except (OSError, RuntimeError):
            # RuntimeError is how resolve() reports a symlink loop before Python 3.13.
            return False

    if any(_is_file_in_root(p) for p in root.glob(pattern)):
        return True
    # Python before 3.13 returns only directories for a pattern ending in '**', while
    # Codacy's 'dir/**' means every file below 'dir', so also look one level further.
    return pattern.endswith("**") and any(_is_file_in_root(p) for p in root.glob(pattern + "/*"))


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
    # Checked before globbing: Path.glob raises on an absolute pattern.
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        problems.append(f"pattern {pattern!r} must be relative to the repository root without '..'")
    elif not _matches_a_file(root, pattern):
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
            problems = _enter_key(section, len(key_match.group(1)), key_match.group(2))
            errors.extend(f".codacy.yml:{i + 1}: {msg}" for msg in problems)
            continue
        item_match = LIST_ITEM_RE.match(line)
        if item_match:
            if not (section.in_engines and section.engine is not None and section.list_key == "exclude_paths"):
                problems = ["list item outside engines.<engine>.exclude_paths"]
            else:
                above = lines[i - 1] if i > 0 else None
                problems = _item_problems(above, item_match.group(1), section.engine, root, today)
        else:
            problems = ["unrecognised line; extend scripts/check-codacy-config.py"]
        errors.extend(f".codacy.yml:{i + 1}: {msg}" for msg in problems)
    return errors


def main(argv: list[str] | None = None, today: datetime.date | None = None) -> int:
    """Parse arguments, check ``.codacy.yml`` under ``root``, print each problem to stderr, and return 1 if any."""
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
