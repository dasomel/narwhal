#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EXP_DIR="${SCRIPT_DIR}/experiments"
STRESS_TARGET="${SCRIPT_DIR}/stress-target.yaml"

if [ "$#" -ne 1 ]; then
  echo "사용법: $0 <실험명|list>"
  echo "예시: $0 istiod-kill"
  echo "      $0 list"
  exit 1
fi

ARG="$1"

if [ "$ARG" = "list" ]; then
  echo "사용 가능한 Chaos Mesh 실험 목록:"
  if [ -d "$EXP_DIR" ]; then
    ls -1 "$EXP_DIR" | sed 's/\.yaml$//'
  else
    echo "실험 디렉토리가 존재하지 않습니다."
  fi
  exit 0
fi

YAML_FILE="${EXP_DIR}/${ARG}.yaml"
if [ ! -f "$YAML_FILE" ]; then
  echo "오류: 실험 파일을 찾을 수 없습니다: ${YAML_FILE}"
  exit 1
fi

# G1 Safety Rule 검증
if grep -q -E "platform-system|apisix|etcd" "$YAML_FILE"; then
  echo "오류 (G1 안전 규칙 위반): 실험 파일에 허용되지 않는 대상(platform-system, apisix, etcd)이 포함되어 있습니다. 실행을 중단합니다."
  exit 1
fi

# 호스트 preflight 검증 (G2)
bash "${REPO_ROOT}/scripts/test/preflight-host.sh" || { echo "오류: preflight-host.sh 검증 실패"; exit 1; }

# pre steady-state 검사
get_non_running_completed_count() {
  # kubectl transient failure must not abort the run under set -e
  kubectl get pods -A --no-headers 2>/dev/null | awk '$4 !~ /^(Running|Completed|Succeeded)$/' | wc -l | tr -d ' ' || echo 999
}

BASELINE_COUNT=$(get_non_running_completed_count)
echo "베이스라인 비정상 파드 수: ${BASELINE_COUNT}"

HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" https://portal.local.narwhal.internal/login || echo 000)
if [ "$HTTP_STATUS" -ne 200 ]; then
  echo "오류: Steady-state 사전 검증 실패 (Portal /login 응답 코드: ${HTTP_STATUS}, 200 기대함)"
  exit 1
fi
echo "Portal 로그인 페이지 정상 상태 확인 (HTTP 200)"

# 타겟 네임스페이스 추출 및 어노테이션 추가
TARGET_NAMESPACES=$(yq eval '.spec.selector.namespaces[]' "$YAML_FILE" 2>/dev/null || true)
if [ -z "$TARGET_NAMESPACES" ]; then
  echo "경고: 실험 파일에서 타겟 네임스페이스를 추출하지 못했습니다."
else
  for ns in $TARGET_NAMESPACES; do
    if [ -n "$ns" ] && [ "$ns" != "null" ]; then
      echo "네임스페이스 '${ns}'에 chaos-mesh.org/inject=enabled 어노테이션을 적용합니다."
      kubectl annotate namespace "$ns" chaos-mesh.org/inject=enabled --overwrite
    fi
  done
fi

# Cleanup trap 정의
cleanup() {
  echo "실험 리소스 및 타겟 정리 중..."
  kubectl delete -f "$YAML_FILE" --ignore-not-found=true
  if [ "$ARG" = "worker-cpu-stress" ]; then
    kubectl delete -f "$STRESS_TARGET" --ignore-not-found=true
  fi
}
trap cleanup EXIT

# worker-cpu-stress 전처리
if [ "$ARG" = "worker-cpu-stress" ]; then
  echo "worker-cpu-stress 감지: stress-target 배포 중..."
  kubectl apply -f "$STRESS_TARGET"
  echo "stress-target 배포 완료. Ready 상태 대기 중..."
  kubectl rollout status deployment/chaos-stress-target -n chaos-testing --timeout=120s
fi

# 실험 실행
echo "Chaos Mesh 실험 리소스 적용: ${ARG}"
kubectl apply -f "$YAML_FILE"

# 대기 시간 계산
DURATION_STR=$(yq eval '.spec.duration' "$YAML_FILE" 2>/dev/null || true)
WAIT_TIME=90 # Default pod-kill 대기 시간

if [ -n "$DURATION_STR" ] && [ "$DURATION_STR" != "null" ]; then
  if [[ "$DURATION_STR" =~ ^([0-9]+)s$ ]]; then
    DURATION_SEC=${BASH_REMATCH[1]}
    WAIT_TIME=$(( DURATION_SEC + 60 ))
  elif [[ "$DURATION_STR" =~ ^([0-9]+)m$ ]]; then
    DURATION_SEC=$(( ${BASH_REMATCH[1]} * 60 ))
    WAIT_TIME=$(( DURATION_SEC + 60 ))
  fi
fi

echo "실험이 진행되는 동안 대기합니다: ${WAIT_TIME}초"
sleep "$WAIT_TIME"

# post steady-state 검사
echo "실험 완료 후 시스템 복구 상태 검증 중..."
RECOVERY_SUCCESS=false

for i in {1..12}; do
  CURRENT_COUNT=$(get_non_running_completed_count)
  HTTP_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" https://portal.local.narwhal.internal/login || echo 000)
  
  echo "복구 점검 [${i}/12]: 비정상 파드 수 = ${CURRENT_COUNT} (베이스라인: ${BASELINE_COUNT}), Portal /login = ${HTTP_STATUS}"
  
  if [ "$CURRENT_COUNT" -le "$BASELINE_COUNT" ] && [ "$HTTP_STATUS" -eq 200 ]; then
    RECOVERY_SUCCESS=true
    break
  fi
  sleep 15
done

if [ "$RECOVERY_SUCCESS" = true ]; then
  echo "======================================"
  echo "실험 결과: PASS"
  echo "======================================"
  exit 0
else
  echo "======================================"
  echo "실험 결과: FAIL"
  echo "======================================"
  exit 1
fi
