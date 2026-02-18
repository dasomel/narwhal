#!/usr/bin/env bash
# PostToolUse Hook: 자동 포맷팅 검사
# additionalContext로 Claude에게 전달
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // empty')

if [[ -z "${FILE_PATH}" ]]; then
  exit 0
fi

CONTEXT=""

# 쉘 스크립트 포맷팅 검사
if [[ "${FILE_PATH}" == *.sh ]]; then
  if command -v shfmt &>/dev/null; then
    if ! shfmt -d -i 2 "${FILE_PATH}" &>/dev/null; then
      CONTEXT="[Format] 쉘 스크립트 포맷팅 차이 감지: shfmt -w -i 2 ${FILE_PATH}"
    fi
  fi
fi

# YAML 검증
if [[ "${FILE_PATH}" == *.yaml ]] || [[ "${FILE_PATH}" == *.yml ]]; then
  if command -v yq &>/dev/null; then
    if ! yq eval '.' "${FILE_PATH}" &>/dev/null 2>&1; then
      CONTEXT="[Error] YAML 문법 오류 감지: ${FILE_PATH}"
    fi
  fi
fi

if [[ -n "${CONTEXT}" ]]; then
  jq -n --arg ctx "${CONTEXT}" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $ctx
    }
  }'
fi

exit 0
