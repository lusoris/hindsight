#!/usr/bin/env python3
"""Refuse an osv-scanner.toml that osv-scanner would skip or apply to nothing.

osv-scanner logs "Ignored invalid config file" and carries on when a config does
not parse, so a broken exception file silently drops every exception in its
directory. This check fails instead. For each osv-scanner.toml it is given it
requires:

- a strict TOML parse (duplicate keys refused);
- only the keys listed below (allow-list, not a deny-list);
- id, ignoreUntil (a date) and reason on every [[IgnoredVulns]] entry, with the
  reason naming its exception category;
- no id listed twice in one file;
- a lockfile next to it, since osv-scanner applies the file only to lockfiles in
  its own directory (the gate passes no --config).

With --changed it instead reads the paths a pull request changes from stdin,
one per line, and fails when any of them is an exception file or part of this
gate, unless OSV_EXCEPTION_APPROVED=1. The osv-exception-approval workflow sets
that only when the pull request carries the osv-exception label, which needs
triage or write access to apply, so the author of a pull request cannot add an
exception for an advisory their own change brings in, nor weaken the check.
That workflow runs from the base branch (pull_request_target) and executes no
pull request code, so the pull request cannot change the copy that judges it.

scripts/check-osv-config.sh builds the file lists from git.

usage: check-osv-config.py FILE...
       git diff --name-only BASE HEAD | check-osv-config.py --changed
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

# isort: split
# tomllib is stdlib from 3.11 on; the split keeps the block sorted for linters
# that assume an older target and would file it under third-party.
try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("check-osv-config: needs Python 3.11 or newer (tomllib)")

TOP_KEYS = {"IgnoredVulns"}
ENTRY_KEYS = {"id", "ignoreUntil", "reason"}
LOCKFILES = {
    "Cargo.lock",
    "go.mod",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}

# Paths whose change needs the osv-exception label: every exception file, and
# the files that make up this gate.
GATED = re.compile(
    r"(?:^|/)osv-scanner\.toml$"
    r"|^scripts/check-osv-config\.(?:py|sh)$"
    r"|^\.github/workflows/osv-(?:scanner|exception-approval)\.yml$"
)


def check_changed(paths: list[str]) -> int:
    gated = [p for p in paths if GATED.search(p)]
    if not gated:
        print(f"check-osv-config: {len(paths)} paths changed, none gated")
        return 0
    if os.environ.get("OSV_EXCEPTION_APPROVED") == "1":
        print(f"check-osv-config: {len(gated)} gated paths changed, approved by label")
        return 0
    for p in gated:
        print(f"{p}: changed without the osv-exception label", file=sys.stderr)
    return 1


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        return [f"{path}: does not parse ({exc}); osv-scanner would skip it"]
    for key in sorted(set(data) - TOP_KEYS):
        errors.append(f"{path}: unknown top-level key {key!r}")
    seen: set[str] = set()
    for n, entry in enumerate(data.get("IgnoredVulns", []), start=1):
        where = f"{path}: IgnoredVulns #{n}"
        for key in sorted(set(entry) - ENTRY_KEYS):
            errors.append(f"{where}: unknown key {key!r}")
        for key in sorted(ENTRY_KEYS - set(entry)):
            errors.append(f"{where}: missing {key!r}")
        vid = entry.get("id")
        if vid in seen:
            errors.append(f"{where}: id {vid} listed twice")
        seen.add(vid)
        if "ignoreUntil" in entry and not isinstance(entry["ignoreUntil"], datetime.date):
            errors.append(f"{where}: ignoreUntil is not a date")
        if not str(entry.get("reason", "")).startswith("category "):
            errors.append(f"{where}: reason must start with its exception category")
    if not any((path.parent / name).exists() for name in LOCKFILES):
        errors.append(f"{path}: no lockfile in {path.parent}; the file applies to nothing")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--changed", action="store_true", help="gate paths read from stdin")
    args = parser.parse_args()
    configs: list[Path] = args.files
    if args.changed:
        if configs:
            parser.error("--changed reads paths from stdin, not arguments")
        return check_changed([line for line in sys.stdin.read().splitlines() if line])
    if not configs:
        print("check-osv-config: no osv-scanner.toml given", file=sys.stderr)
        return 1
    errors = [e for p in configs for e in check_file(p)]
    for e in errors:
        print(e, file=sys.stderr)
    print(f"check-osv-config: {len(configs)} files, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
