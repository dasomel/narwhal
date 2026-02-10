#!/usr/bin/env bash
# PostToolUse Hook: 같은 파일 반복 수정 감지
set -euo pipefail

MODIFIED_FILE="${CLAUDE_MODIFIED_FILE:-}"

if [[ -z "${MODIFIED_FILE}" ]]; then
  exit 0
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CACHE_DIR="${PROJECT_DIR}/.claude/cache"
MISTAKE_LOG="${CACHE_DIR}/mistake-candidates.jsonl"
EDIT_HISTORY="${CACHE_DIR}/.edit-history"

# 캐시 디렉토리 확인
mkdir -p "${CACHE_DIR}"

# 편집 히스토리 기록
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "${TIMESTAMP}|${MODIFIED_FILE}" >> "${EDIT_HISTORY}"

# 최근 10분간 같은 파일 수정 횟수 확인
CUTOFF=$(date -u -v-10M +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "10 minutes ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "")

if [[ -n "${CUTOFF}" ]]; then
  COUNT=$(grep "${MODIFIED_FILE}" "${EDIT_HISTORY}" 2>/dev/null | awk -F'|' -v cutoff="${CUTOFF}" '$1 >= cutoff' | wc -l | tr -d ' ')
else
  # 전체 카운트 (시간 비교 불가 시)
  COUNT=$(grep -c "${MODIFIED_FILE}" "${EDIT_HISTORY}" 2>/dev/null || echo "0")
fi

# 3회 이상 수정 시 경고
if [[ "${COUNT}" -ge 3 ]]; then
  echo "[Warning] '${MODIFIED_FILE}' 파일이 ${COUNT}회 수정되었습니다."
  echo "[Warning] 반복 수정 패턴이 감지됨 - 접근 방식 재검토 권장"

  # JSONL 로그 기록
  echo "{\"timestamp\":\"${TIMESTAMP}\",\"file\":\"${MODIFIED_FILE}\",\"count\":${COUNT},\"type\":\"repeated_edit\"}" >> "${MISTAKE_LOG}"
fi

# 편집 히스토리가 너무 크면 정리 (1000줄 초과)
if [[ -f "${EDIT_HISTORY}" ]] && [[ $(wc -l < "${EDIT_HISTORY}") -gt 1000 ]]; then
  tail -500 "${EDIT_HISTORY}" > "${EDIT_HISTORY}.tmp"
  mv "${EDIT_HISTORY}.tmp" "${EDIT_HISTORY}"
fi
