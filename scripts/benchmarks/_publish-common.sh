#!/usr/bin/env bash
# Shared library for benchmark publish scripts.
#
# Provides common functions for publishing benchmark results to the
# continuous performance monitor dashboard (gh-pages).
#
# shellcheck disable=SC2034

ENRICHED_TMP=""
WORK=""

_publish_cleanup() {
  if [ -n "${ENRICHED_TMP:-}" ] && [ -f "${ENRICHED_TMP}" ]; then
    rm -f "$ENRICHED_TMP"
  fi
  if [ -n "${WORK:-}" ] && [ -d "${WORK}" ]; then
    rm -rf "$WORK"
  fi
}

publish_init() {
  set -euo pipefail
  local usage_hint="${2:-<results.json>}"
  INPUT_JSON="${1:?usage: $0 ${usage_hint}}"
  DASHBOARD_REPO="${DASHBOARD_REPO:-vectorize-io/hindsight-continuous-performance-monitor}"
  HINDSIGHT_REPO="${HINDSIGHT_REPO:-vectorize-io/hindsight}"

  if [ ! -f "$INPUT_JSON" ]; then
    echo "Input JSON not found: $INPUT_JSON" >&2
    exit 1
  fi
  : "${PERF_DASHBOARD_TOKEN:?PERF_DASHBOARD_TOKEN must be set}"

  trap _publish_cleanup EXIT
}

publish_git_metadata() {
  local subdir="${1:-${DATA_SUBDIR:-}}"

  SHA=$(git rev-parse HEAD)
  SHORT_SHA=$(git rev-parse --short=8 HEAD)
  SUBJECT=$(git log -1 --pretty=%s)
  AUTHOR=$(git log -1 --pretty=%an)
  AUTHOR_DATE=$(git log -1 --pretty=%aI)
  COMMIT_URL="https://github.com/${HINDSIGHT_REPO}/commit/${SHA}"

  PR_NUMBER=""
  PR_URL=""
  if command -v gh >/dev/null 2>&1; then
    PR_NUMBER=$(gh api "repos/${HINDSIGHT_REPO}/commits/${SHA}/pulls" \
      --jq '.[0].number // empty' 2>/dev/null || true)
    if [ -n "$PR_NUMBER" ]; then
      PR_URL="https://github.com/${HINDSIGHT_REPO}/pull/${PR_NUMBER}"
    fi
  fi

  RUN_ID="${GITHUB_RUN_ID:-}"
  RUN_URL=""
  if [ -n "$RUN_ID" ]; then
    RUN_REPO="${GITHUB_REPOSITORY:-$HINDSIGHT_REPO}"
    RUN_URL="https://github.com/${RUN_REPO}/actions/runs/${RUN_ID}"
  fi

  TIMESTAMP_FILE=$(date -u +%Y%m%dT%H%M%SZ)
  TIMESTAMP_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  if [ -n "$subdir" ]; then
    DATA_FILE="data/${subdir}/${TIMESTAMP_FILE}-${SHORT_SHA}.json"
  else
    DATA_FILE="data/${TIMESTAMP_FILE}-${SHORT_SHA}.json"
  fi
}

publish_clone() {
  WORK=$(mktemp -d)
  git clone --quiet --depth 1 --branch gh-pages \
    "https://x-access-token:${PERF_DASHBOARD_TOKEN}@github.com/${DASHBOARD_REPO}.git" \
    "$WORK"
}

publish_index_update() {
  local entry="${1:?publish_index_update requires entry JSON}"
  local data_subdir="${2:-}"
  local index_filename="${3:?publish_index_update requires index filename}"

  local target_dir="$WORK/data"
  if [ -n "$data_subdir" ]; then
    target_dir="$WORK/data/$data_subdir"
  fi
  mkdir -p "$target_dir"

  : "${ENRICHED_TMP:?ENRICHED_TMP must be set before publish_index_update}"
  : "${DATA_FILE:?DATA_FILE must be set before publish_index_update}"
  cp "$ENRICHED_TMP" "$WORK/$DATA_FILE"

  local index_file
  if [[ "$index_filename" == data/* ]]; then
    index_file="$WORK/$index_filename"
  else
    index_file="$WORK/data/$index_filename"
  fi

  if [ ! -f "$index_file" ]; then
    echo '{"runs": []}' > "$index_file"
  fi

  local updated_index
  updated_index=$(jq \
    --argjson entry "$entry" \
    '.runs = ([$entry] + (.runs // [])) | .updated_at = (now | todateiso8601)' \
    "$index_file")
  echo "$updated_index" > "$index_file"
}

publish_push() {
  local prefix_or_msg="${1:?usage: publish_push <prefix_or_msg>}"
  local commit_msg
  if [[ "$prefix_or_msg" == *:* ]] || [[ "$prefix_or_msg" == *" "* ]]; then
    commit_msg="$prefix_or_msg"
  else
    commit_msg="${prefix_or_msg}: add results for ${SHORT_SHA}"
  fi

  cd "$WORK"
  git config user.name 'hindsight-perf-bot'
  git config user.email 'hindsight-perf-bot@users.noreply.github.com'
  git add data/
  if git diff --cached --quiet; then
    echo "No changes to commit (this shouldn't happen — skipping push)" >&2
    exit 0
  fi
  git commit --quiet -m "$commit_msg"

  # Retry push once if rejected — concurrent runs from different jobs could race.
  if ! git push --quiet origin gh-pages; then
    echo "Push rejected, pulling and retrying..." >&2
    git pull --quiet --rebase origin gh-pages
    git push --quiet origin gh-pages
  fi

  echo "Published ${DATA_FILE} to ${DASHBOARD_REPO} gh-pages"
}
