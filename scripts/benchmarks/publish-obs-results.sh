#!/usr/bin/env bash
# Publish observation-dedup benchmark results to the dashboard repo's gh-pages branch.
#
# Reads an obs benchmark JSON (from `obs_benchmark.py --output`), enriches it with
# commit + workflow metadata, then pushes:
#   data/obs/<timestamp>-<short_sha>.json
#   data/obs-index.json   (manifest, newest first)
# to vectorize-io/hindsight-continuous-performance-monitor (gh-pages). The static
# site's obs.html reads data/obs-index.json + the run JSONs and charts the
# duplication rate (lower is better).
#
# Required env:
#   PERF_DASHBOARD_TOKEN   PAT with Contents:write on the dashboard repo
#
# Usage:
#   ./scripts/benchmarks/publish-obs-results.sh path/to/obs-results.json

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/benchmarks/_publish-common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_publish-common.sh"

publish_init "${1:-}" "<obs-results.json>"

DATA_SUBDIR="obs"
publish_git_metadata "$DATA_SUBDIR"

echo "Publishing obs run for ${SHORT_SHA} → ${DATA_FILE}"

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
DUP_RATE=$(jq -r '.overall_duplication_rate' "$ENRICHED_TMP")
TOTAL_OBS=$(jq -r '.total_observations // 0' "$ENRICHED_TMP")

# ─────────────────────────────────────────────────────────────────────────
# Clone dashboard repo's gh-pages branch
# ─────────────────────────────────────────────────────────────────────────
publish_clone

# ─────────────────────────────────────────────────────────────────────────
# Update manifest (data/obs-index.json)
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
  --argjson dup_rate "$DUP_RATE" \
  --argjson total_obs "$TOTAL_OBS" \
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
    overall_duplication_rate: $dup_rate,
    total_observations: $total_obs
  }')

publish_index_update "$NEW_ENTRY" "$DATA_SUBDIR" "obs-index.json"

# ─────────────────────────────────────────────────────────────────────────
# Commit and push
# ─────────────────────────────────────────────────────────────────────────
publish_push "obs"
