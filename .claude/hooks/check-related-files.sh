#!/usr/bin/env bash
# PostToolUse Hook: 파일 수정 시 연관 파일 알림
set -euo pipefail

# 수정된 파일 경로 (환경변수로 전달됨)
MODIFIED_FILE="${CLAUDE_MODIFIED_FILE:-}"

if [[ -z "${MODIFIED_FILE}" ]]; then
  exit 0
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CACHE_DIR="${PROJECT_DIR}/.claude/cache"

# 패턴 매칭으로 관련 파일 알림
case "${MODIFIED_FILE}" in
  */scripts/master/*.sh)
    echo "[Reminder] VERSIONS.md의 버전과 동기화 확인 필요"
    echo "[Reminder] Vagrantfile의 환경변수 확인"
    ;;
  */scripts/common/*.sh)
    echo "[Reminder] master/worker 모든 노드에 적용됨"
    ;;
  */gitops/apps/*.yaml)
    echo "[Reminder] gitops/values/ 또는 gitops/resources/ 동기화 확인"
    echo "[Reminder] ArgoCD가 자동 동기화합니다"
    ;;
  */gitops/resources/*.yaml)
    echo "[Reminder] 관련 앱 YAML 확인 필요"
    ;;
  */Vagrantfile)
    echo "[Reminder] 스크립트에서 참조하는 환경변수 확인"
    echo "[Reminder] VERSIONS.md 동기화 확인"
    ;;
  */VERSIONS.md)
    echo "[Reminder] 관련 스크립트의 버전 업데이트 필요"
    echo "[Reminder] gitops/apps/*.yaml의 차트 버전 확인"
    ;;
esac
