#!/usr/bin/env bash
# Publish locomo benchmark results to the dashboard repo's gh-pages branch.
#
# Input is AMB's run report (outputs/locomo/<memory>/<mode>/<split>.json), whose
# field names and accuracy scale differ from the series charted here — see the
# normalisation at the top of the jq filter below. The in-repo runner that used
# to produce this file was removed once AMB took over both datasets.
#
# Strips the heavy per-question payload (kept only in the upload artifact),
# enriches with commit + workflow metadata, then pushes:
#   data/locomo/<timestamp>-<short_sha>.json
#   data/locomo-index.json   (manifest, newest first)
# to vectorize-io/hindsight-continuous-performance-monitor (gh-pages).
#
# Required env:
#   PERF_DASHBOARD_TOKEN   PAT with Contents:write on the dashboard repo
#
# Usage:
#   ./scripts/benchmarks/publish-locomo-results.sh path/to/benchmark_results.json

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/benchmarks/_publish-common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_publish-common.sh"

publish_init "${1:-}" "<benchmark_results.json>"

DATA_SUBDIR="locomo"
publish_git_metadata "$DATA_SUBDIR"

echo "Publishing locomo run for ${SHORT_SHA} → ${DATA_FILE}"

# ─────────────────────────────────────────────────────────────────────────
# Strip detailed_results (per-question detail) and enrich
#
# detailed_results is per-question {question, predicted_answer, reasoning,
# retrieved_memories, ...} and bloats each run to several MB. We keep only
# accuracy stats per item, which is enough for trend charts; the full data
# remains available as a workflow artifact.
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
  --arg timestamp "$TIMESTAMP_ISO" \
  '
    # AMB shape -> the series this dashboard has always charted. AMB reports a
    # 0-1 fraction in .accuracy, its own counts, and a flat .results array; the
    # published series is a PERCENTAGE with num_items (conversations) and
    # total_questions, 86 runs deep. Normalise rather than restart the chart —
    # publishing .accuracy raw would read as a drop from ~88 to ~1.
    # .results is the per-query payload (answers, contexts, raw responses,
    # ~400KB a run) and stays in the workflow artifact only, exactly as
    # detailed_results did before it.
    (if .accuracy != null then {
        overall_accuracy: (.accuracy * 100),
        num_items: ([.results[]?.meta.sample_id // empty] | unique | length),
        total_questions: .total_queries,
      } else {} end) as $amb
    | del(.results)
    | . + $amb
    # Drop per-question payloads from each item (pre-AMB shape).
    | .item_results = (.item_results // [] | map(.metrics |= del(.detailed_results)))
    | . + {
        timestamp: $timestamp,
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
      }
  ' "$INPUT_JSON" > "$ENRICHED_TMP"

OVERALL=$(jq -r '.overall_accuracy' "$ENRICHED_TMP")
NUM_ITEMS=$(jq -r '.num_items // (.item_results | length)' "$ENRICHED_TMP")
TOTAL_QUESTIONS=$(jq -r '.total_questions // 0' "$ENRICHED_TMP")

# ─────────────────────────────────────────────────────────────────────────
# Clone dashboard repo's gh-pages branch
# ─────────────────────────────────────────────────────────────────────────
publish_clone

# ─────────────────────────────────────────────────────────────────────────
# Update manifest (data/locomo-index.json)
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
  --arg timestamp "$TIMESTAMP_ISO" \
  --argjson overall "$OVERALL" \
  --argjson num_items "$NUM_ITEMS" \
  --argjson total_questions "$TOTAL_QUESTIONS" \
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
    overall_accuracy: $overall,
    num_items: $num_items,
    total_questions: $total_questions
  }')

publish_index_update "$NEW_ENTRY" "$DATA_SUBDIR" "locomo-index.json"

# ─────────────────────────────────────────────────────────────────────────
# Commit and push
# ─────────────────────────────────────────────────────────────────────────
publish_push "locomo"
