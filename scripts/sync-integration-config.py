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
import sys
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


def render(source_text: str) -> str:
    """Return rendered target content by prepending HEADER to the source text."""
    return HEADER + source_text


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

    with open(source_path, encoding="utf-8", newline="") as f:
        source_text = f.read()
    rendered = render(source_text)

    any_drift = False
    for target in TARGETS:
        target_path = root / target
        if not target_path.is_file():
            is_different = True
        else:
            with open(target_path, encoding="utf-8", newline="") as f:
                is_different = f.read() != rendered

        if is_different:
            any_drift = True
            if args.check:
                print(
                    f"{target}: out of date with {SOURCE}; run scripts/sync-integration-config.py",
                    file=sys.stderr,
                )
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(rendered)
                print(f"updated {target}")

    if args.check and any_drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
