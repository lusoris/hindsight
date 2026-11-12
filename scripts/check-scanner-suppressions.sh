#!/usr/bin/env bash
# Feed check-scanner-suppressions.py the tracked files containing suppression markers.
#
#   check-scanner-suppressions.sh       check tracked files with nosem/nosemgrep/nosec
#
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

python=python3
if [[ -x .venv/bin/python ]]; then
    python=.venv/bin/python
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

# Exclude this wrapper, scripts/check-scanner-suppressions.py and
# hindsight-dev/tests/test_check_scanner_suppressions.py because they spell the marker pattern
# in their own implementation and planted test cases.
# -i: semgrep matches any case; upper-case nosec must reach the checker and fail.
set +e
git grep -l -z -i -I -E '\bnosem(grep)?\b|\bnosec\b' -- . \
    ':!scripts/check-scanner-suppressions.sh' \
    ':!scripts/check-scanner-suppressions.py' \
    ':!hindsight-dev/tests/test_check_scanner_suppressions.py' > "$tmp"
grep_rc=$?
set -e

if [[ $grep_rc -eq 1 ]]; then
    exit 0
elif [[ $grep_rc -ne 0 ]]; then
    exit "$grep_rc"
fi

xargs -0 -r "$python" scripts/check-scanner-suppressions.py < "$tmp" || exit 1
