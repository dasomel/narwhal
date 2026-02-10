#!/usr/bin/env bash
# AgentStop Hook: 에이전트 종료 시 검증
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo ""
echo "=== Final Verification ==="

# 1. Vagrantfile 문법 검사
if [[ -f "${PROJECT_DIR}/Vagrantfile" ]]; then
  if ruby -c "${PROJECT_DIR}/Vagrantfile" &>/dev/null; then
    echo "[OK] Vagrantfile syntax"
  else
    echo "[FAIL] Vagrantfile syntax error!"
  fi
fi

# 2. 변경된 쉘 스크립트 검사
CHANGED_SCRIPTS=$(git -C "${PROJECT_DIR}" diff --name-only 2>/dev/null | grep '\.sh$' || true)
if [[ -n "${CHANGED_SCRIPTS}" ]]; then
  echo "[Check] Modified scripts:"
  for script in ${CHANGED_SCRIPTS}; do
    if command -v shellcheck &>/dev/null; then
      if shellcheck "${PROJECT_DIR}/${script}" &>/dev/null; then
        echo "  [OK] ${script}"
      else
        echo "  [WARN] ${script} - shellcheck warnings"
      fi
    else
      echo "  [?] ${script} - shellcheck not installed"
    fi
  done
fi

# 3. 변경된 YAML 검사
CHANGED_YAML=$(git -C "${PROJECT_DIR}" diff --name-only 2>/dev/null | grep -E '\.(yaml|yml)$' || true)
if [[ -n "${CHANGED_YAML}" ]]; then
  echo "[Check] Modified YAML files:"
  for yaml in ${CHANGED_YAML}; do
    if command -v yq &>/dev/null; then
      if yq eval '.' "${PROJECT_DIR}/${yaml}" &>/dev/null; then
        echo "  [OK] ${yaml}"
      else
        echo "  [FAIL] ${yaml} - YAML syntax error!"
      fi
    fi
  done
fi

echo "=========================="
