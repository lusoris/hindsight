#!/usr/bin/env bash
# Publish perf-test results to the dashboard repo's gh-pages branch.
#
# Reads a perf-test --output JSON, enriches it with commit metadata, then
# pushes:
#   data/<timestamp>-<short_sha>.json   (the enriched run)
#   data/index.json                     (manifest, newest first)
# to vectorize-io/hindsight-continuous-performance-monitor (gh-pages).
#
# Required env:
#   PERF_DASHBOARD_TOKEN   PAT with Contents:write on the dashboard repo
#
# Usage:
#   ./scripts/benchmarks/publish-perf-results.sh path/to/perf-results.json

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/benchmarks/_publish-common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_publish-common.sh"

publish_init "${1:-}" "<perf-results.json>"

DATA_SUBDIR=""
publish_git_metadata "$DATA_SUBDIR"

echo "Publishing run for ${SHORT_SHA} → ${DATA_FILE}"

# ─────────────────────────────────────────────────────────────────────────
# Enrich the input JSON
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
RUN_SCALE=$(jq -r '.scale' "$ENRICHED_TMP")

# ─────────────────────────────────────────────────────────────────────────
# Clone dashboard repo's gh-pages branch
# ─────────────────────────────────────────────────────────────────────────
publish_clone

# ─────────────────────────────────────────────────────────────────────────
# Update manifest (data/index.json)
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
  --arg scale "$RUN_SCALE" \
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
    scale: $scale
  }')

publish_index_update "$NEW_ENTRY" "${DATA_SUBDIR:-}" "index.json"

# ─────────────────────────────────────────────────────────────────────────
# Commit and push
# ─────────────────────────────────────────────────────────────────────────
publish_push "perf"
