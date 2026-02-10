#!/usr/bin/env bash
# PreToolUse Hook: 편집 전 검증
set -euo pipefail

TARGET_FILE="${CLAUDE_TARGET_FILE:-}"

if [[ -z "${TARGET_FILE}" ]]; then
  exit 0
fi

# .vagrant 폴더 수정 방지
if [[ "${TARGET_FILE}" == *".vagrant/"* ]]; then
  echo "[BLOCKED] .vagrant/ 폴더는 직접 수정할 수 없습니다."
  exit 1
fi

# 민감한 파일 경고
case "${TARGET_FILE}" in
  *kubeconfig*|*.pem|*.key|*.env)
    echo "[WARNING] 민감한 파일입니다. 비밀번호/토큰 하드코딩 금지!"
    ;;
esac

exit 0
