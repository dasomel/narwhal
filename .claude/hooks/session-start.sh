#!/usr/bin/env bash
# SessionStart Hook: 프로젝트 컨텍스트 로드 + 환경 변수 설정
set -euo pipefail

# CLAUDE_ENV_FILE로 환경 변수 주입 (Bash 명령에서 사용 가능)
if [[ -n "${CLAUDE_ENV_FILE:-}" ]]; then
  cat >> "${CLAUDE_ENV_FILE}" <<ENVEOF
export NARWHAL_PROJECT=true
export NARWHAL_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
ENVEOF
fi

# 프로젝트 컨텍스트를 Claude에게 전달
jq -n '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: "Narwhal IDP 프로젝트. K8s 1.35, 2-phase provisioning, HTTPS OIDC 필수. Master 6GB, Worker 4GB. vagrant ssh master-1 사용. 한국어 소통."
  }
}'
