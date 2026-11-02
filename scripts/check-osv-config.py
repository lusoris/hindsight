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

usage: check-osv-config.py [ROOT]   (default: the repository root)
"""

from __future__ import annotations

import datetime
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


def _tracked_configs(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "osv-scanner.toml", "**/osv-scanner.toml"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [root / p for p in out.split("\0") if p]


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
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
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
