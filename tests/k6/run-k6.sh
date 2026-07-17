#!/bin/bash
set -euo pipefail

usage() {
  echo "Usage: $0 <login-flow|portal-browse|gateway-fanout|all> [--prom]"
  exit 1
}

if [ $# -lt 1 ]; then
  usage
fi

TARGET="$1"
PROM=false
if [ "${2:-}" == "--prom" ]; then
  PROM=true
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Running preflight checks..."
bash "$REPO_ROOT/scripts/test/preflight-host.sh" || exit 1

check_steady_state() {
  local phase="$1"
  echo "Checking steady-state ($phase)..."
  local not_running
  not_running=$(kubectl get pods --all-namespaces --no-headers 2>/dev/null | awk '$4!="Running" && $4!="Completed" && $4!="Succeeded" {print $0}' | wc -l | tr -d ' ' || echo "0")
  if [ "$not_running" -gt 0 ]; then
    echo "[WARN] Found $not_running pods not in Running/Completed state."
  fi

  if ! curl -sk https://portal.local.narwhal.internal/login -o /dev/null -w "%{http_code}" | grep -q 200; then
    if [ "$phase" = "before" ]; then
      echo "[FAIL] Portal login page is not returning 200. Aborting."
      exit 1
    else
      echo "[WARN] Portal login page is not returning 200."
    fi
  else
    echo "[OK] Portal login page returned 200."
  fi
}

check_steady_state "before"

if ! command -v k6 &> /dev/null; then
  echo "k6 could not be found. Please install it (e.g., brew install k6)."
  exit 1
fi

if [[ "$TARGET" == "login-flow" || "$TARGET" == "portal-browse" || "$TARGET" == "all" ]]; then
  echo "Fetching Keycloak credentials..."
  # Portal login uses realm 'narwhal' users, NOT the master-realm bootstrap admin
  # (keycloak-initial-admin/temp-admin cannot log in through the portal OIDC client).
  # Per-user passwords live in the keycloak-user-passwords secret (key = username).
  K6_USERNAME="${K6_USERNAME:-admin}"
  export K6_USERNAME
  export K6_PASSWORD=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath="{.data.${K6_USERNAME}}" | base64 -d)
  if [ -z "$K6_PASSWORD" ]; then
    echo "[FAIL] keycloak-user-passwords secret에서 '${K6_USERNAME}' 비밀번호를 읽지 못했습니다."
    exit 1
  fi
fi

mkdir -p "$REPO_ROOT/tests/k6/results"

run_k6() {
  local script_name="$1"
  local ts=$(date +%Y%m%d-%H%M%S)
  local script_path="$REPO_ROOT/tests/k6/${script_name}.js"
  
  if [ ! -f "$script_path" ]; then
    echo "Script not found: $script_path"
    exit 1
  fi

  local extra_args=""
  if [ "$PROM" = true ]; then
    extra_args="-o experimental-prometheus-rw --tag testid=${script_name}-${ts}"
  fi

  echo "Running $script_name..."
  # k6 exits non-zero when a threshold is crossed — that is a RESULT, not a
  # harness failure. Record it and keep running the remaining scenarios.
  local rc=0
  k6 run $extra_args --summary-export "$REPO_ROOT/tests/k6/results/${script_name}-${ts}.json" "$script_path" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[WARN] ${script_name}: k6 exit ${rc} (임계치 초과 또는 오류) — 계속 진행합니다."
    FAILED_SCRIPTS+=("$script_name")
  fi
}

PROM_PID=""
FAILED_SCRIPTS=()
if [ "$PROM" = true ]; then
  echo "Starting prometheus port-forward (keepalive)..."
  # A single port-forward silently dies during long runs; keep restarting it.
  (
    while true; do
      kubectl -n monitoring port-forward svc/prometheus-stack-kube-prom-prometheus 9090:9090 > /dev/null 2>&1 || true
      sleep 2
    done
  ) &
  PROM_PID=$!
  trap 'kill $PROM_PID 2>/dev/null || true; pkill -f "port-forward svc/prometheus-stack-kube-prom-prometheus" 2>/dev/null || true' EXIT

  export K6_PROMETHEUS_RW_SERVER_URL=http://127.0.0.1:9090/api/v1/write
  export K6_PROMETHEUS_RW_TREND_STATS='p(95),p(99),avg,max'

  # wait for port-forward
  sleep 3
fi

if [ "$TARGET" == "all" ]; then
  run_k6 "gateway-fanout"
  run_k6 "portal-browse"
  run_k6 "login-flow"
else
  run_k6 "$TARGET"
fi

check_steady_state "after"

if [ "${#FAILED_SCRIPTS[@]}" -gt 0 ]; then
  echo "[RESULT] 임계치 초과/실패 시나리오: ${FAILED_SCRIPTS[*]}"
  exit 1
fi
echo "[RESULT] 모든 시나리오 임계치 통과"
