#!/usr/bin/env bash
# PostToolUse Hook: Detect repeated edits to the same file
# Passes warning to Claude via additionalContext
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // empty')

if [[ -z "${FILE_PATH}" ]]; then
  exit 0
fi

CACHE_DIR="${CLAUDE_PROJECT_DIR:-.}/.claude/cache"
MISTAKE_LOG="${CACHE_DIR}/mistake-candidates.jsonl"
EDIT_HISTORY="${CACHE_DIR}/.edit-history"

mkdir -p "${CACHE_DIR}"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "${TIMESTAMP}|${FILE_PATH}" >> "${EDIT_HISTORY}"

# Count edits to the same file in the last 10 minutes
CUTOFF=$(date -u -v-10M +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "10 minutes ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "")

if [[ -n "${CUTOFF}" ]]; then
  MATCHES=$(grep "${FILE_PATH}" "${EDIT_HISTORY}" 2>/dev/null || true)
  COUNT=$(echo "${MATCHES}" | awk -F'|' -v cutoff="${CUTOFF}" '$1 >= cutoff' | wc -l | tr -d ' ')
else
  COUNT=$(grep -c "${FILE_PATH}" "${EDIT_HISTORY}" 2>/dev/null || echo "0")
fi

if [[ "${COUNT}" -ge 3 ]]; then
  echo "{\"timestamp\":\"${TIMESTAMP}\",\"file\":\"${FILE_PATH}\",\"count\":${COUNT},\"type\":\"repeated_edit\"}" >> "${MISTAKE_LOG}"
  jq -n --arg f "${FILE_PATH}" --arg c "${COUNT}" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: ("[Warning] " + $f + " has been edited " + $c + " times. Consider reviewing your approach.")
    }
  }'
fi

# Trim history
if [[ -f "${EDIT_HISTORY}" ]] && [[ $(wc -l < "${EDIT_HISTORY}") -gt 1000 ]]; then
  tail -500 "${EDIT_HISTORY}" > "${EDIT_HISTORY}.tmp"
  mv "${EDIT_HISTORY}.tmp" "${EDIT_HISTORY}"
fi

exit 0
