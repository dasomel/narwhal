#!/usr/bin/env bash
# PostToolUse Hook: Bash 명령 실행 후 검증
set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "${INPUT}" | jq -r '.tool_name // empty')
COMMAND=$(echo "${INPUT}" | jq -r '.tool_input.command // empty')
EXIT_CODE=$(echo "${INPUT}" | jq -r '.tool_response.exit_code // "0"')

# 성공한 명령은 무시
if [[ "${EXIT_CODE}" == "0" ]]; then
  exit 0
fi

CONTEXT=""

# vagrant ssh 실패 시
if [[ "${COMMAND}" == *"vagrant ssh"* ]]; then
  CONTEXT="[Hint] vagrant ssh 실패. VM이 실행 중인지 확인: vagrant status. 호스트 키 변경 시 vagrant ssh를 사용하세요 (직접 SSH 아님)."
fi

# kubectl 실패 시
if [[ "${COMMAND}" == *"kubectl"* ]]; then
  CONTEXT="[Hint] kubectl 실패. KUBECONFIG 설정 확인. VM 내에서 실행해야 하는 명령인지 확인. vagrant ssh master-1 -c \"kubectl ...\" 형태로 실행."
fi

# helm 실패 시
if [[ "${COMMAND}" == *"helm"* ]]; then
  CONTEXT="[Hint] helm 실패. ARM64 이미지 호환성 확인. --wait 플래그 제거 고려. VERSIONS.md와 차트 버전 일치 확인."
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
