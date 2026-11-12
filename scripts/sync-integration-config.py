#!/usr/bin/env python3
"""Sync shared integration user configuration into individual packages.

Coding-agent integration packages (aider, github-copilot, openhands, devin-desktop)
read user settings from ``~/.hindsight/<agent>.json``. Because each package is released
to PyPI independently, they cannot share a common Python package dependency. Instead,
a single canonical source file is synchronized into each package.

In default mode, this script updates any missing or drifting target copies.
With --check, it verifies that every copy matches the rendered source and
exits with 1 if any target differs or is missing.

usage: sync-integration-config.py
       sync-integration-config.py --check
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SOURCE = Path("hindsight-integrations/_shared/user_config.py")

TARGETS: tuple[Path, ...] = (
    Path("hindsight-integrations/aider/hindsight_aider/_user_config.py"),
    Path("hindsight-integrations/github-copilot/hindsight_copilot/_user_config.py"),
    Path("hindsight-integrations/openhands/hindsight_openhands/_user_config.py"),
    Path("hindsight-integrations/devin-desktop/hindsight_devin_desktop/_user_config.py"),
)

HEADER = (
    "# Generated from hindsight-integrations/_shared/user_config.py by\n"
    "# scripts/sync-integration-config.py. Do not edit: change the source and rerun\n"
    "# the script; lint.sh runs it with --check and fails on any difference.\n"
)

MAX_PASSES = 3


def render(source_text: str) -> str:
    """Return rendered target content by prepending HEADER to the source text."""
    return HEADER + source_text


@dataclass(frozen=True)
class _SyncResult:
    """Outcome of one pass over the targets."""

    drift: bool
    """At least one target was missing or differed from the rendering."""
    written: bool
    """At least one target was (re)written; always False with ``--check``."""


def _write_target(target_path: Path, rendered: str) -> None:
    """Atomically write ``rendered`` to ``target_path`` via a temporary file."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile creates its file 0600 and os.replace keeps that mode, so
    # without this every synced target would end up owner-only. Keep an existing
    # target's mode; give a new one what a plain open() would (0666 minus the umask,
    # which can only be read by setting it).
    try:
        mode = stat.S_IMODE(target_path.stat().st_mode)
    except FileNotFoundError:
        current_umask = os.umask(0)
        os.umask(current_umask)
        mode = 0o666 & ~current_umask
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=target_path.parent,
        delete=False,
    )
    tmp_path = Path(tmp.name)
    try:
        with tmp:
            tmp.write(rendered)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, target_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _sync_targets(root: Path, rendered: str, check: bool) -> _SyncResult:
    """Check or update targets under ``root`` against ``rendered``."""
    any_drift = False
    any_written = False
    for target in TARGETS:
        target_path = root / target
        if not target_path.is_file():
            is_different = True
        else:
            with open(target_path, encoding="utf-8", newline="") as f:
                is_different = f.read() != rendered

        if is_different:
            any_drift = True
            if check:
                print(
                    f"{target}: out of date with {SOURCE}; run scripts/sync-integration-config.py",
                    file=sys.stderr,
                )
            else:
                _write_target(target_path, rendered)
                any_written = True
                print(f"updated {target}")

    return _SyncResult(drift=any_drift, written=any_written)


def main(argv: list[str] | None = None) -> int:
    """Sync shared user config into integration targets, or verify with ``--check`` that all match."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="exit 1 if any target is missing or drifts")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root directory",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    source_path = root / SOURCE
    if not source_path.is_file():
        print(f"{SOURCE}: shared source missing", file=sys.stderr)
        return 2

    if args.check:
        with open(source_path, encoding="utf-8", newline="") as f:
            source_text = f.read()
        rendered = render(source_text)
        return 1 if _sync_targets(root, rendered, check=True).drift else 0

    for _ in range(MAX_PASSES):
        with open(source_path, encoding="utf-8", newline="") as f:
            source_text = f.read()
        rendered = render(source_text)
        if not _sync_targets(root, rendered, check=False).written:
            return 0
        with open(source_path, encoding="utf-8", newline="") as f:
            new_source_text = f.read()
        if new_source_text == source_text:
            return 0

    print(f"{SOURCE}: changed while syncing; rerun scripts/sync-integration-config.py", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
