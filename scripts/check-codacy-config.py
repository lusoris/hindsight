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
import itertools
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BLANK_RE = re.compile(r"^\s*$")
DOC_START_RE = re.compile(r"^---\s*$")
COMMENT_LINE_RE = re.compile(r"^\s*#")
KEY_RE = re.compile(r"^( *)([a-z_]+):\s*$")
LIST_ITEM_RE = re.compile(r'^( +)- "?([^"\s]+)"?\s*$')
COMMENT_RE = re.compile(r"^ *# category ([ABCD]), entry (\S+), expiry (\S+), rule ([a-z_]+): (\S.*)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXCLUDE_ITEM_INDENT = 6  # Two spaces per level: engines 0, engine 2, exclude_paths 4, its items 6.

INTERVALS: dict[str, int] = {
    "B": 3,
    "C": 10,
    "D": 120,
}


@dataclass
class _Section:
    """Where the reader is in the YAML tree: inside top-level ``engines``, which engine, which engine key."""

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


# Codacy documents exclude_paths as Java glob syntax (java.nio.file.FileSystem.getPathMatcher),
# matched against the path relative to the repository root. This follows OpenJDK's translation of
# that syntax to a regex (sun.nio.fs.Globs): '*' and '?' stay within one path component, '**'
# crosses '/', '{a,b}' groups do not nest, '[!x]' negates and '\' escapes. The standard library
# has no Java-glob matcher: fnmatch lets '*' cross '/', and Path.glob is a different dialect.
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a Codacy (Java NIO) glob pattern into a compiled regex."""
    return re.compile(_GlobTranslator(pattern).translate())


class _GlobTranslator:
    """Translate one Codacy glob into regex source, one construct per method.

    This was a single loop with a branch per construct; ruff's C901 caps a function at
    complexity 10, so each construct moved into its own method. Each method consumes its
    construct, advances ``i`` past it and returns the regex source for it.
    """

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.i = 0
        self.in_group = False

    def translate(self) -> str:
        """Return the regex source for the whole pattern, or raise ValueError if it is malformed."""
        handlers = {
            "\\": self._escape,
            "*": self._star,
            "?": self._question,
            "{": self._open_group,
            "}": self._close_group,
            ",": self._comma,
            "[": self._bracket,
        }
        parts: list[str] = []
        while self.i < len(self.pattern):
            parts.append(handlers.get(self.pattern[self.i], self._literal)())
        if self.in_group:
            raise ValueError("unterminated '{'")
        return "".join(parts)

    def _literal(self) -> str:
        """Match the current character, '/' included, as itself."""
        self.i += 1
        return re.escape(self.pattern[self.i - 1])

    def _escape(self) -> str:
        """Match the character after a '\\' as itself."""
        if self.i + 1 >= len(self.pattern):
            raise ValueError("trailing lone backslash")
        self.i += 2
        return re.escape(self.pattern[self.i - 1])

    def _star(self) -> str:
        """Let '**' cross '/' and keep a single '*' within one path component."""
        if self.pattern.startswith("**", self.i):
            self.i += 2
            return ".*"
        self.i += 1
        return "[^/]*"

    def _question(self) -> str:
        """Match one character other than '/'."""
        self.i += 1
        return "[^/]"

    def _open_group(self) -> str:
        """Open a '{a,b}' group; groups do not nest."""
        if self.in_group:
            raise ValueError("nested '{'")
        self.in_group = True
        self.i += 1
        return "(?:"

    def _close_group(self) -> str:
        """Close the open group, or match a '}' outside a group as itself."""
        self.i += 1
        if not self.in_group:
            return re.escape("}")
        self.in_group = False
        return ")"

    def _comma(self) -> str:
        """Separate alternatives inside a group, or match a ',' outside one as itself."""
        self.i += 1
        return "|" if self.in_group else re.escape(",")

    def _bracket(self) -> str:
        """Translate a '[...]' expression into a one-character class that never matches '/'."""
        n = len(self.pattern)
        self.i += 1
        negated = self.i < n and self.pattern[self.i] == "!"
        if negated:
            self.i += 1
        if self.i >= n:
            raise ValueError("unterminated '['")

        # Class members go through re.escape: besides '\', '^', '[', ']' and '-' it also
        # escapes '&', '~' and '|', which Python reads as possible set operations when doubled.
        class_body: list[str] = []
        if self.pattern[self.i] == "-":
            class_body.append(re.escape("-"))
            self.i += 1
        class_body.extend(self._bracket_members())

        if not class_body:
            raise ValueError("empty bracket expression")
        neg_prefix = "^" if negated else ""
        return f"(?!/)[{neg_prefix}{''.join(class_body)}]"

    def _bracket_members(self) -> list[str]:
        """Consume class members through the closing ']' and return each one as regex source."""
        members: list[str] = []
        range_start = ""  # The previous member when it can start a range, else empty.
        while self.i < len(self.pattern):
            member = self.pattern[self.i]
            self.i += 1
            if member == "]":
                return members
            if member == "/":
                raise ValueError("explicit '/' in bracket expression")
            if member != "-":
                members.append(re.escape(member))
                range_start = member
                continue
            if not range_start:
                raise ValueError("invalid range in bracket expression")
            if self.i >= len(self.pattern):
                raise ValueError("unterminated '['")
            range_end = self.pattern[self.i]
            self.i += 1
            if range_end == "]":
                # A '-' right before the closing ']' is a literal member.
                members.append(re.escape("-"))
                return members
            members.append(_bracket_range_end(range_start, range_end))
            range_start = ""
        raise ValueError("unterminated '['")


def _bracket_range_end(range_start: str, range_end: str) -> str:
    """Return the regex source that ends a class range ``range_start-range_end``."""
    if range_end == "/":
        raise ValueError("explicit '/' in bracket expression")
    if ord(range_end) < ord(range_start):
        raise ValueError(f"invalid range '{range_start}-{range_end}' in bracket expression")
    return f"-{re.escape(range_end)}"


_GLOB_WILDCARDS = frozenset("*?[{\\")


def _literal_dir_prefix(parts: list[str]) -> list[str]:
    """Return the leading literal components of a split pattern: the directory a walk can start from."""
    prefix = list(itertools.takewhile(lambda part: not _GLOB_WILDCARDS.intersection(part), parts))
    # All components are literal; all but the last component form the directory prefix.
    return prefix[:-1] if len(prefix) == len(parts) else prefix


def _is_file_in_root(path: Path, resolved_root: Path) -> bool:
    """Return whether ``path`` is a regular file whose resolved location lies inside ``resolved_root``."""
    # resolve() follows symlinks, so a link that points outside the repository does not count.
    try:
        return path.is_file() and path.resolve().is_relative_to(resolved_root)
    except (OSError, RuntimeError):
        # RuntimeError is how resolve() reports a symlink loop before Python 3.13.
        return False


def _matches_a_file(root: Path, pattern: str) -> bool:
    """Return whether ``pattern`` (relative, without ``..``) matches a regular file inside ``root``."""
    regex = _glob_to_regex(pattern)
    dir_parts = _literal_dir_prefix(pattern.split("/"))
    start = root.joinpath(*dir_parts) if dir_parts else root
    if not start.is_dir():
        return False

    resolved_root = root.resolve()
    # This used root.glob(pattern) before, which speaks a different dialect: on Python 3.11 and
    # 3.12 it raises ValueError on '**.py', on 3.13 it matches top-level files only, and before
    # 3.13 a trailing '**' yields directories only, so 'dir/**' needed a second 'dir/**/*' glob
    # to see the files below. Walking the tree and matching each relative path with the
    # Java-glob regex gives Codacy's semantics on every supported Python. The walk starts at
    # the pattern's literal directory prefix and skips .git. Untracked files in the checkout
    # count as matches, as they did before.
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for filename in filenames:
            path = Path(dirpath, filename)
            if regex.fullmatch(path.relative_to(root).as_posix()) and _is_file_in_root(path, resolved_root):
                return True
    return False


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
    # Checked before matching: an absolute or '..' pattern can never match a path relative to the root.
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        problems.append(f"pattern {pattern!r} must be relative to the repository root without '..'")
    else:
        try:
            matches = _matches_a_file(root, pattern)
        except ValueError as exc:
            problems.append(f"pattern {pattern!r} is not a valid Codacy glob: {exc}")
        else:
            if not matches:
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
            indent = len(item_match.group(1))
            pattern = item_match.group(2)
            if not (section.in_engines and section.engine is not None and section.list_key == "exclude_paths"):
                problems = ["list item outside engines.<engine>.exclude_paths"]
            elif indent != EXCLUDE_ITEM_INDENT:
                problems = [f"list item indented {indent} spaces; expected 6 under engines.<engine>.exclude_paths"]
            else:
                above = lines[i - 1] if i > 0 else None
                problems = _item_problems(above, pattern, section.engine, root, today)
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
