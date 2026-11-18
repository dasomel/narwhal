#!/bin/bash
set -euo pipefail

NAMESPACE="platform-system"
MODE="aborted"
MAX_ROUNDS="${APISIX_RECOVERY_MAX_ROUNDS:-3}"
DIVERGENCE_WAIT_SECONDS="${APISIX_DIVERGENCE_WAIT_SECONDS:-30}"
DIVERGENCE_POLL_INTERVAL="${APISIX_DIVERGENCE_POLL_INTERVAL:-5}"
PF_WAIT_ATTEMPTS="${APISIX_PF_WAIT_ATTEMPTS:-10}"

RESOURCE_TYPES=(apisixupstreams.apisix.apache.org apisixroutes.apisix.apache.org)
SYNC_RESOURCE_TYPES=(apisixupstreams.apisix.apache.org apisixtlses.apisix.apache.org apisixroutes.apisix.apache.org)

PF_PID=""
PF_PORT=""
TMP_DIR=""
LAST_HAD_404=false

usage() {
  cat <<'EOF'
Usage: recover-apisix-resource-sync.sh [NAMESPACE] [--divergence|--check-divergence|--all]

Modes:
  (default)             Recover CRs stuck in ResourceSyncAborted
  --divergence          Compare APISIX Admin API counts (routes/upstreams/ssls) against CRs
  --check-divergence    Alias for --divergence
  --all                 Run ResourceSyncAborted recovery followed by divergence check/resync
EOF
}

parse_args() {
  local seen_ns=false
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --divergence|--check-divergence)
        MODE="divergence"
        ;;
      --all)
        MODE="all"
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      -*)
        echo "ERROR: unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
      *)
        if [ "${seen_ns}" = true ]; then
          echo "ERROR: unexpected extra argument: $1" >&2
          usage >&2
          exit 2
        fi
        NAMESPACE="$1"
        seen_ns=true
        ;;
    esac
    shift
  done
}

stop_pf() {
  if [ -n "${PF_PID}" ]; then
    kill "${PF_PID}" 2>/dev/null || true
    wait "${PF_PID}" 2>/dev/null || true
    PF_PID=""
  fi
}

cleanup() {
  stop_pf
  if [ -n "${TMP_DIR}" ] && [ -d "${TMP_DIR}" ]; then
    rm -rf "${TMP_DIR}"
  fi
}

aborted_resources() {
  local kind="$1"
  kubectl get "${kind}" -n "${NAMESPACE}" -o json | python3 -c '
import json, sys
for item in json.load(sys.stdin).get("items", []):
    conditions = item.get("status", {}).get("conditions", [])
    if any("ResourceSyncAborted" in (str(c.get("type", "")) + " " + str(c.get("reason", "")) + " " + str(c.get("message", ""))) for c in conditions):
        print(item["metadata"]["name"])
'
}

recover_aborted() {
  local round count kind names name manifest remaining
  for ((round=1; round<=MAX_ROUNDS; round++)); do
    count=0
    for kind in "${RESOURCE_TYPES[@]}"; do
      names=$(aborted_resources "${kind}")
      while IFS= read -r name; do
        [ -n "${name}" ] || continue
        count=$((count + 1))
        echo "APISIX recovery round ${round}/${MAX_ROUNDS}: recreating ${kind}/${name}"
        manifest=$(kubectl get "${kind}" "${name}" -n "${NAMESPACE}" -o json | python3 -c '
import json, sys
obj=json.load(sys.stdin); obj.pop("status", None); meta=obj["metadata"]
for key in ("uid", "resourceVersion", "managedFields", "creationTimestamp", "generation"): meta.pop(key, None)
print(json.dumps(obj))
')
        kubectl delete "${kind}" "${name}" -n "${NAMESPACE}" --wait=true
        printf '%s\n' "${manifest}" | kubectl apply -f -
      done <<<"${names}"
    done
    [ "${count}" -gt 0 ] || break
    sleep 5
  done
  remaining=""
  for kind in "${RESOURCE_TYPES[@]}"; do
    names=$(aborted_resources "${kind}")
    if [ -n "${names}" ]; then remaining="${remaining}${kind}: ${names//$'\n'/ }\n"; fi
  done
  if [ -n "${remaining}" ]; then
    printf 'ERROR: APISIX resources remain ResourceSyncAborted after %s rounds:\n%b' "${MAX_ROUNDS}" "${remaining}" >&2
    return 1
  fi
  echo "APISIX ResourceSyncAborted recovery complete."
}

resolve_admin_key() {
  local errf out err rc=0 key=""
  TMP_DIR=$(mktemp -d)
  chmod 700 "${TMP_DIR}"
  errf="${TMP_DIR}/kubectl.err"
  out=$(kubectl get secret apisix-admin-key -n "${NAMESPACE}" -o 'jsonpath={.data.key}' 2>"${errf}") || rc=$?
  err=$(cat "${errf}" 2>/dev/null || true)
  rm -f "${errf}"
  if [ "${rc}" -ne 0 ]; then
    if [ "${rc}" -ne 127 ]; then
      case "${err}" in
        *"Error from server (NotFound)"*) out="" ;;
        *)
          echo "ERROR: failed to read Secret ${NAMESPACE}/apisix-admin-key." >&2
          return 1
          ;;
      esac
    else
      echo "ERROR: kubectl not found." >&2
      return 1
    fi
  fi
  if [ -n "${out}" ]; then
    key=$(printf '%s' "${out}" | base64 -d 2>/dev/null) || {
      echo "ERROR: Secret ${NAMESPACE}/apisix-admin-key key is not valid base64." >&2
      return 1
    }
  fi
  case "${key}" in
    ''|*'{'*|*'$'*)
      key=$(kubectl get configmap apisix -n "${NAMESPACE}" -o 'jsonpath={.data.config\.yaml}' 2>/dev/null \
        | awk '/^[[:space:]]*-[[:space:]]*key:/ {k=$3; gsub(/"/, "", k)} /^[[:space:]]*name:[[:space:]]*"?admin"?[[:space:]]*$/ && !f {print k; f=1}' || true)
      ;;
  esac
  case "${key}" in
    ''|*'{'*|*'$'*)
      echo "ERROR: no usable APISIX admin key found in Secret ${NAMESPACE}/apisix-admin-key or ConfigMap ${NAMESPACE}/apisix." >&2
      return 1
      ;;
  esac
  # Store key in a 0600 file in TMP_DIR so it never passes through command argv.
  ( umask 077; printf '%s' "${key}" > "${TMP_DIR}/admin.key" )
}

start_pf() {
  local attempt
  for attempt in 1 2 3 4 5; do
    PF_PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
    kubectl port-forward -n "${NAMESPACE}" svc/apisix-admin "${PF_PORT}:9180" >/dev/null 2>&1 &
    PF_PID=$!
    sleep 1
    if kill -0 "${PF_PID}" 2>/dev/null; then
      return 0
    fi
    wait "${PF_PID}" 2>/dev/null || true
    PF_PID=""
  done
  echo "ERROR: kubectl port-forward to ${NAMESPACE}/svc/apisix-admin failed to start." >&2
  return 1
}

admin_get_count() {
  local endpoint="$1"
  local attempt resp code body parsed
  for ((attempt=1; attempt<=PF_WAIT_ATTEMPTS; attempt++)); do
    resp=$(printf 'header = "X-API-KEY: %s"\n' "$(cat "${TMP_DIR}/admin.key")" \
      | curl -s -m 5 -K - -w '\n%{http_code}' "http://127.0.0.1:${PF_PORT}/apisix/admin/${endpoint}" 2>/dev/null || true)
    code="${resp##*$'\n'}"
    if [ -n "${code}" ] && [ "${code}" != "000" ]; then
      break
    fi
    code="000"
    sleep 1
  done
  if [ "${code}" = "200" ]; then
    body="${resp%$'\n'*}"
    parsed=$(printf '%s' "${body}" | python3 -c '
import json, sys
data = json.load(sys.stdin)
if isinstance(data.get("total"), int):
    print(data["total"])
elif isinstance(data.get("list"), list):
    print(len(data["list"]))
else:
    sys.exit(2)
' 2>/dev/null) || {
      echo "ERROR: unexpected JSON payload from APISIX Admin API /apisix/admin/${endpoint}." >&2
      return 1
    }
    printf '%s' "${parsed}"
    return 0
  elif [ "${code}" = "404" ]; then
    # Empty etcd prefixes return HTTP 404 until at least one key has been written.
    : > "${TMP_DIR}/had_404"
    printf '0'
    return 0
  elif [ "${code}" = "401" ] || [ "${code}" = "403" ]; then
    echo "ERROR: APISIX Admin API rejected credentials on /apisix/admin/${endpoint} (HTTP ${code})." >&2
    return 1
  else
    echo "ERROR: APISIX Admin API GET /apisix/admin/${endpoint} failed (HTTP ${code})." >&2
    return 1
  fi
}

read_expected_counts() {
  kubectl get apisixroutes.apisix.apache.org -n "${NAMESPACE}" -o json > "${TMP_DIR}/routes.json"
  kubectl get apisixtlses.apisix.apache.org -n "${NAMESPACE}" -o json > "${TMP_DIR}/tls.json"
  python3 - "${TMP_DIR}/routes.json" "${TMP_DIR}/tls.json" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    routes_doc = json.load(f)
with open(sys.argv[2], encoding="utf-8") as f:
    tls_doc = json.load(f)
exp_routes = 0
upstreams = set()
for item in routes_doc.get("items") or []:
    spec = item.get("spec") or {}
    http_rules = spec.get("http") or []
    stream_rules = spec.get("stream") or []
    exp_routes += len(http_rules) + len(stream_rules)
    for rule in http_rules:
        for u in rule.get("upstreams") or []:
            if isinstance(u, dict) and u.get("name"):
                upstreams.add(("name", u["name"]))
        for b in rule.get("backends") or []:
            if isinstance(b, dict) and b.get("serviceName"):
                upstreams.add(("backend", b.get("serviceName"), b.get("servicePort")))
        b = rule.get("backend")
        if isinstance(b, dict) and b.get("serviceName"):
            upstreams.add(("backend", b.get("serviceName"), b.get("servicePort")))
exp_ssls = len(tls_doc.get("items") or [])
print(f"{exp_routes} {len(upstreams)} {exp_ssls}")
PYEOF
}

read_actual_counts() {
  local r u s had_404=false
  rm -f "${TMP_DIR}/had_404"
  r=$(admin_get_count "routes") || return 1
  u=$(admin_get_count "upstreams") || return 1
  s=$(admin_get_count "ssls") || return 1
  if [ -f "${TMP_DIR}/had_404" ]; then
    had_404=true
  fi
  printf '%s %s %s %s' "${r}" "${u}" "${s}" "${had_404}"
}

touch_apisix_crs() {
  local round="$1"
  local ts kind names
  ts="$(date +%s)-r${round}"
  for kind in "${SYNC_RESOURCE_TYPES[@]}"; do
    names=$(kubectl get "${kind}" -n "${NAMESPACE}" -o name)
    if [ -n "${names}" ]; then
      kubectl annotate "${kind}" -n "${NAMESPACE}" --all "narwhal.io/resync=${ts}" --overwrite
    fi
  done
}

counts_converged() {
  local act_r="$1" act_u="$2" act_s="$3" exp_r="$4" exp_u="$5" exp_s="$6"
  [ "${act_r}" -ge "${exp_r}" ] && [ "${act_u}" -ge "${exp_u}" ] && [ "${act_s}" -ge "${exp_s}" ]
}

recover_divergence() {
  local expected_line actual_line exp_r exp_u exp_s act_r act_u act_s round elapsed
  resolve_admin_key
  start_pf

  expected_line=$(read_expected_counts)
  read -r exp_r exp_u exp_s <<<"${expected_line}"
  actual_line=$(read_actual_counts)
  read -r act_r act_u act_s LAST_HAD_404 <<<"${actual_line}"
  if counts_converged "${act_r}" "${act_u}" "${act_s}" "${exp_r}" "${exp_u}" "${exp_s}"; then
    echo "APISIX state in sync (${NAMESPACE}): routes=${act_r}/${exp_r}, upstreams=${act_u}/${exp_u}, ssls=${act_s}/${exp_s}."
    return 0
  fi

  echo "APISIX state diverged (${NAMESPACE}): routes=${act_r}/${exp_r}, upstreams=${act_u}/${exp_u}, ssls=${act_s}/${exp_s}; triggering resync."
  for ((round=1; round<=MAX_ROUNDS; round++)); do
    echo "APISIX divergence recovery round ${round}/${MAX_ROUNDS}: annotating CRs with narwhal.io/resync"
    touch_apisix_crs "${round}"
    elapsed=0
    while true; do
      actual_line=$(read_actual_counts)
      read -r act_r act_u act_s LAST_HAD_404 <<<"${actual_line}"
      if counts_converged "${act_r}" "${act_u}" "${act_s}" "${exp_r}" "${exp_u}" "${exp_s}"; then
        echo "APISIX divergence recovered (${NAMESPACE}): routes=${act_r}/${exp_r}, upstreams=${act_u}/${exp_u}, ssls=${act_s}/${exp_s}."
        return 0
      fi
      if [ "${elapsed}" -ge "${DIVERGENCE_WAIT_SECONDS}" ]; then
        break
      fi
      sleep "${DIVERGENCE_POLL_INTERVAL}"
      elapsed=$((elapsed + DIVERGENCE_POLL_INTERVAL))
    done
  done

  echo "ERROR: APISIX state still diverged after ${MAX_ROUNDS} rounds (${NAMESPACE}): routes=${act_r}/${exp_r}, upstreams=${act_u}/${exp_u}, ssls=${act_s}/${exp_s}." >&2
  if [ "${LAST_HAD_404}" = true ]; then
    echo "HINT: Admin API returned HTTP 404 on an empty prefix; see docs/common/apisix-etcd-recovery.md if the controller is stuck at startup." >&2
  fi
  return 1
}

trap cleanup EXIT INT TERM
parse_args "$@"

case "${MODE}" in
  aborted)
    recover_aborted
    ;;
  divergence)
    recover_divergence
    ;;
  all)
    recover_aborted
    recover_divergence
    ;;
esac
