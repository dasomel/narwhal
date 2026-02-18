#!/usr/bin/env bash
# PreToolUse Hook: 편집 전 검증
# JSON 출력으로 hookSpecificOutput.permissionDecision 사용 (공식 프로토콜)
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // empty')

if [[ -z "${FILE_PATH}" ]]; then
  exit 0
fi

# .vagrant 폴더 수정 방지
if [[ "${FILE_PATH}" == *".vagrant/"* ]]; then
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: ".vagrant/ 폴더는 직접 수정할 수 없습니다."
    }
  }'
  exit 0
fi

# 민감한 파일 경고 (ask로 사용자 확인 요청)
case "${FILE_PATH}" in
  *kubeconfig*|*.pem|*.key|*.env)
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: "민감한 파일입니다. 비밀번호/토큰 하드코딩 금지!"
      }
    }'
    exit 0
    ;;
esac

exit 0
