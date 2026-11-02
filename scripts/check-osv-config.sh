#!/usr/bin/env bash
# Feed check-osv-config.py the osv-scanner.toml files git knows about.
#
#   check-osv-config.sh            check every tracked exception file
#   check-osv-config.sh BASE       fail on exception files changed since BASE,
#                                  unless OSV_EXCEPTION_APPROVED=1
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
pathspec=(-- osv-scanner.toml '**/osv-scanner.toml')

if [[ $# -eq 0 ]]; then
    # Without -r, xargs runs the check once even with no input, and the check
    # fails when it is given no file.
    git ls-files -z "${pathspec[@]}" | xargs -0 python3 scripts/check-osv-config.py
else
    git diff -z --name-only "$1" HEAD "${pathspec[@]}" | xargs -0 python3 scripts/check-osv-config.py --changed
fi
