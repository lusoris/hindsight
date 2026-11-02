#!/usr/bin/env bash
# Feed check-osv-config.py the osv-scanner.toml files git knows about.
#
#   check-osv-config.sh            check every tracked exception file
#   check-osv-config.sh BASE       fail on gated paths changed since BASE,
#                                  unless OSV_EXCEPTION_APPROVED=1 (the
#                                  osv-exception-approval workflow runs the
#                                  same check from the base branch in CI)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# The checker needs Python 3.11+ (tomllib). Prefer the uv environment that
# scripts/hooks/lint.sh syncs; CI runners carry a new enough python3.
python=python3
if [[ -x .venv/bin/python ]]; then
    python=.venv/bin/python
fi

if [[ $# -eq 0 ]]; then
    # Without -r, xargs runs the check once even with no input, and the check
    # fails when it is given no file.
    git ls-files -z -- osv-scanner.toml '**/osv-scanner.toml' | xargs -0 "$python" scripts/check-osv-config.py
else
    git diff --name-only "$1" HEAD | "$python" scripts/check-osv-config.py --changed
fi
