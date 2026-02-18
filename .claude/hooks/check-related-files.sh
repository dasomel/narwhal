#!/usr/bin/env bash
# PostToolUse Hook: 파일 수정 시 연관 파일 알림
# additionalContext로 Claude에게 전달 (plain echo는 verbose에서만 보임)
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "${INPUT}" | jq -r '.tool_input.file_path // empty')

if [[ -z "${FILE_PATH}" ]]; then
  exit 0
fi

CONTEXT=""

case "${FILE_PATH}" in
  */scripts/master/*.sh)
    CONTEXT="[Reminder] VERSIONS.md의 버전과 동기화 확인 필요. Vagrantfile의 환경변수 확인."
    ;;
  */scripts/common/*.sh)
    CONTEXT="[Reminder] master/worker 모든 노드에 적용되는 공통 스크립트입니다."
    ;;
  */gitops/apps/*.yaml)
    CONTEXT="[Reminder] gitops/values/ 또는 gitops/resources/ 동기화 확인. ArgoCD가 자동 동기화합니다."
    ;;
  */gitops/resources/*.yaml)
    CONTEXT="[Reminder] 관련 앱 YAML 확인 필요."
    ;;
  */Vagrantfile)
    CONTEXT="[Reminder] 스크립트에서 참조하는 환경변수와 VERSIONS.md 동기화 확인."
    ;;
  */VERSIONS.md)
    CONTEXT="[Reminder] 관련 스크립트의 버전과 gitops/apps/*.yaml의 차트 버전 업데이트 필요."
    ;;
  *)
    exit 0
    ;;
esac

if [[ -n "${CONTEXT}" ]]; then
  jq -n --arg ctx "${CONTEXT}" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $ctx
    }
  }'
fi
