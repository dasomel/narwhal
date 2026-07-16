#!/bin/bash
set -euo pipefail

ALL_PASS=true

echo "=== Host Preflight Checks ==="

# a. No kind cluster
if { docker ps --format '{{.Names}}' 2>/dev/null || true; } | grep -E -- '-control-plane|-worker|^kind' >/dev/null; then
  echo "[FAIL] Kind cluster is running. Please stop it first. (Kind 클러스터가 실행 중입니다.)"
  ALL_PASS=false
else
  echo "[OK] No conflicting kind cluster found."
fi

# b. Host 1-min load
LOAD_AVG=$(sysctl -n vm.loadavg | awk '{print $2}')
MAX_LOAD=${PREFLIGHT_MAX_LOAD:-10}
if awk "BEGIN {exit !($LOAD_AVG < $MAX_LOAD)}"; then
  echo "[OK] Host 1-min load is $LOAD_AVG (< $MAX_LOAD)."
else
  echo "[FAIL] Host load $LOAD_AVG is too high (>= $MAX_LOAD). (호스트 부하가 너무 높습니다.)"
  ALL_PASS=false
fi

# c. memory_pressure
MEM_FREE=$(memory_pressure -Q 2>/dev/null | awk '/System-wide memory free percentage:/ {print $5}' | tr -d '%' || echo "100")
MIN_MEM=${PREFLIGHT_MIN_MEMFREE:-20}
if [ -n "$MEM_FREE" ] && [ "$MEM_FREE" -ge "$MIN_MEM" ]; then
  echo "[OK] Memory free is $MEM_FREE% (>= $MIN_MEM%)."
else
  echo "[FAIL] Memory free $MEM_FREE% is too low (< $MIN_MEM%). (가용 메모리가 부족합니다.)"
  ALL_PASS=false
fi

# d. kubectl get nodes
if ! KUBECTL_OUT=$(kubectl get nodes 2>/dev/null); then
  echo "[FAIL] kubectl get nodes failed. (Kubernetes 클러스터에 연결할 수 없습니다.)"
  ALL_PASS=false
elif echo "$KUBECTL_OUT" | tail -n +2 | grep -i "NotReady" >/dev/null; then
  echo "[FAIL] Found NotReady nodes in cluster. (NotReady 상태의 노드가 있습니다.)"
  ALL_PASS=false
else
  echo "[OK] All Kubernetes nodes are Ready."
fi

if [ "$ALL_PASS" = false ]; then
  echo "Preflight checks failed."
  exit 1
fi
echo "Preflight checks passed."
exit 0
