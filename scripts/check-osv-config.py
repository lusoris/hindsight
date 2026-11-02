#!/usr/bin/env python3
"""Refuse an osv-scanner.toml that osv-scanner would skip or apply to nothing.

osv-scanner logs "Ignored invalid config file" and carries on when a config does
not parse, so a broken exception file silently drops every exception in its
directory. This check fails instead. For each tracked osv-scanner.toml it
requires:

- a strict TOML parse (duplicate keys refused);
- only the keys listed below (allow-list, not a deny-list);
- id, ignoreUntil (a date) and reason on every [[IgnoredVulns]] entry, with the
  reason naming its exception category;
- no id listed twice in one file;
- a lockfile next to it, since osv-scanner applies the file only to lockfiles in
  its own directory (the gate passes no --config).

With --changed-since BASE it instead fails when any osv-scanner.toml differs
between BASE and HEAD, unless OSV_EXCEPTION_APPROVED=1. The gate sets that only
when the pull request carries the osv-exception label, which needs triage or
write access to apply, so the author of a pull request cannot add an exception
for an advisory their own change brings in.

usage: check-osv-config.py [ROOT]
       check-osv-config.py --changed-since BASE [ROOT]
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
import tomllib
from pathlib import Path

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


PATHSPEC = ["--", "osv-scanner.toml", "**/osv-scanner.toml"]


def _git(root: Path, *args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p]


def _tracked_configs(root: Path) -> list[Path]:
    return [root / p for p in _git(root, "ls-files", "-z", *PATHSPEC)]


def check_changed(root: Path, base: str) -> int:
    changed = _git(root, "diff", "-z", "--name-only", base, "HEAD", *PATHSPEC)
    if not changed:
        print("check-osv-config: no exception file changed")
        return 0
    if os.environ.get("OSV_EXCEPTION_APPROVED") == "1":
        print(f"check-osv-config: {len(changed)} exception files changed, approved by label")
        return 0
    for p in changed:
        print(f"{p}: exception file changed without the osv-exception label", file=sys.stderr)
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
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--changed-since", metavar="BASE")
    args = parser.parse_args()
    root: Path = args.root
    if args.changed_since:
        return check_changed(root, args.changed_since)
    configs = _tracked_configs(root)
    if not configs:
        print("check-osv-config: no osv-scanner.toml tracked", file=sys.stderr)
        return 1
    errors = [e for p in configs for e in check_file(p)]
    for e in errors:
        print(e, file=sys.stderr)
    print(f"check-osv-config: {len(configs)} files, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
