#!/usr/bin/env bash
# PostToolUse Hook: 자동 포맷팅
set -euo pipefail

MODIFIED_FILE="${CLAUDE_MODIFIED_FILE:-}"

if [[ -z "${MODIFIED_FILE}" ]]; then
  exit 0
fi

# 쉘 스크립트 포맷팅 검사 (shfmt 설치 시)
if [[ "${MODIFIED_FILE}" == *.sh ]]; then
  if command -v shfmt &>/dev/null; then
    # 포맷팅 차이만 확인 (자동 수정 안 함)
    if ! shfmt -d -i 2 "${MODIFIED_FILE}" &>/dev/null; then
      echo "[Format] 쉘 스크립트 포맷팅 권장: shfmt -w -i 2 ${MODIFIED_FILE}"
    fi
  fi
fi

# YAML 검증
if [[ "${MODIFIED_FILE}" == *.yaml ]] || [[ "${MODIFIED_FILE}" == *.yml ]]; then
  if command -v yq &>/dev/null; then
    if ! yq eval '.' "${MODIFIED_FILE}" &>/dev/null; then
      echo "[Error] YAML 문법 오류: ${MODIFIED_FILE}"
    fi
  fi
fi

exit 0
