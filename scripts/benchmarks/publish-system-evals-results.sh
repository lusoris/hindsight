#!/usr/bin/env bash
# Publish system-evals (knowledge-page convergence) results to the dashboard repo's gh-pages branch.
#
# Reads a system-evals JSON (from `pytest evals --output`), enriches it with
# commit + workflow metadata, then pushes:
#   data/system-evals/<timestamp>-<short_sha>.json
#   data/system-evals-index.json   (manifest, newest first)
# to vectorize-io/hindsight-continuous-performance-monitor (gh-pages). The static
# site's system-evals.html reads data/system-evals-index.json + the run JSONs and
# charts the correct rate (higher is better) and the trap count (must be 0).
#
# Required env:
#   PERF_DASHBOARD_TOKEN   PAT with Contents:write on the dashboard repo
#
# Usage:
#   ./scripts/benchmarks/publish-system-evals-results.sh path/to/system-evals-results.json

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/benchmarks/_publish-common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_publish-common.sh"

publish_init "${1:-}" "<system-evals-results.json>"

DATA_SUBDIR="system-evals"
publish_git_metadata "$DATA_SUBDIR"

echo "Publishing system-evals run for ${SHORT_SHA} → ${DATA_FILE}"

# ─────────────────────────────────────────────────────────────────────────
# Enrich the input JSON (the benchmark already carries timestamp + metrics)
# ─────────────────────────────────────────────────────────────────────────
ENRICHED_TMP=$(mktemp)

jq \
  --arg sha "$SHA" \
  --arg short_sha "$SHORT_SHA" \
  --arg subject "$SUBJECT" \
  --arg author "$AUTHOR" \
  --arg author_date "$AUTHOR_DATE" \
  --arg commit_url "$COMMIT_URL" \
  --arg pr_number "$PR_NUMBER" \
  --arg pr_url "$PR_URL" \
  --arg run_id "$RUN_ID" \
  --arg run_url "$RUN_URL" \
  '. + {
    commit: {
      sha: $sha,
      short_sha: $short_sha,
      subject: $subject,
      author: $author,
      author_date: $author_date,
      url: $commit_url,
      pr_number: ($pr_number | if . == "" then null else tonumber end),
      pr_url: (if $pr_url == "" then null else $pr_url end)
    },
    workflow_run: (if $run_url == "" then null else {id: $run_id, url: $run_url} end)
  }' "$INPUT_JSON" > "$ENRICHED_TMP"

RUN_TIMESTAMP=$(jq -r '.timestamp' "$ENRICHED_TMP")
CORRECT_RATE=$(jq -r '.correct_rate' "$ENRICHED_TMP")
TRAP_COUNT=$(jq -r '.trap_count // 0' "$ENRICHED_TMP")
TOTAL=$(jq -r '.total // 0' "$ENRICHED_TMP")
MODE=$(jq -r '.mode // "full"' "$ENRICHED_TMP")

# ─────────────────────────────────────────────────────────────────────────
# Clone dashboard repo's gh-pages branch
# ─────────────────────────────────────────────────────────────────────────
publish_clone

# ─────────────────────────────────────────────────────────────────────────
# Update manifest (data/system-evals-index.json)
# ─────────────────────────────────────────────────────────────────────────
NEW_ENTRY=$(jq -n \
  --arg sha "$SHA" \
  --arg short_sha "$SHORT_SHA" \
  --arg subject "$SUBJECT" \
  --arg author "$AUTHOR" \
  --arg author_date "$AUTHOR_DATE" \
  --arg commit_url "$COMMIT_URL" \
  --arg pr_url "$PR_URL" \
  --arg run_url "$RUN_URL" \
  --arg data_file "$DATA_FILE" \
  --arg timestamp "$RUN_TIMESTAMP" \
  --argjson correct_rate "$CORRECT_RATE" \
  --argjson trap_count "$TRAP_COUNT" \
  --argjson total "$TOTAL" \
  --arg mode "$MODE" \
  '{
    sha: $sha,
    short_sha: $short_sha,
    subject: $subject,
    author: $author,
    author_date: $author_date,
    commit_url: $commit_url,
    pr_url: (if $pr_url == "" then null else $pr_url end),
    run_url: (if $run_url == "" then null else $run_url end),
    data_file: $data_file,
    timestamp: $timestamp,
    correct_rate: $correct_rate,
    trap_count: $trap_count,
    total: $total,
    mode: $mode
  }')

publish_index_update "$NEW_ENTRY" "$DATA_SUBDIR" "system-evals-index.json"

# ─────────────────────────────────────────────────────────────────────────
# Commit and push
# ─────────────────────────────────────────────────────────────────────────
publish_push "system-evals"
