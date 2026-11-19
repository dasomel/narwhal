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
  --divergence          Compare APISIX Admin API objects (routes/stream_routes/upstreams/ssls) against CRs
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

admin_get_ids() {
  local endpoint="$1"
  local attempt resp code body
  rm -f "${TMP_DIR}/404_${endpoint}"
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
    printf '%s' "${body}" > "${TMP_DIR}/body_${endpoint}.json"
    if ! python3 - "${TMP_DIR}/body_${endpoint}.json" "${TMP_DIR}/actual_${endpoint}.json" <<'PYEOF' 2>/dev/null
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
raw_list = data.get("list")
if raw_list == {}:
    raw_list = []
if not isinstance(raw_list, list):
    sys.exit(2)
ids = []
for idx, entry in enumerate(raw_list):
    if not isinstance(entry, dict):
        sys.exit(2)
    val = entry.get("value") if isinstance(entry.get("value"), dict) else entry
    obj_id = val.get("id")
    if not obj_id and isinstance(entry.get("key"), str) and "/" in entry["key"]:
        obj_id = entry["key"].rsplit("/", 1)[-1]
    if obj_id is not None and str(obj_id) != "":
        ids.append(str(obj_id))
    else:
        ids.append(f"__anon_{idx}")
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(ids, f)
PYEOF
    then
      echo "ERROR: unexpected JSON payload from APISIX Admin API /apisix/admin/${endpoint}." >&2
      return 1
    fi
    return 0
  elif [ "${code}" = "404" ]; then
    # Empty etcd prefixes return HTTP 404 until at least one key has been written.
    : > "${TMP_DIR}/404_${endpoint}"
    printf '[]' > "${TMP_DIR}/actual_${endpoint}.json"
    return 0
  elif [ "${code}" = "401" ] || [ "${code}" = "403" ]; then
    echo "ERROR: APISIX Admin API rejected credentials on /apisix/admin/${endpoint} (HTTP ${code})." >&2
    return 1
  else
    echo "ERROR: APISIX Admin API GET /apisix/admin/${endpoint} failed (HTTP ${code})." >&2
    return 1
  fi
}

read_expected_ids() {
  local need_svc
  kubectl get apisixroutes.apisix.apache.org -n "${NAMESPACE}" -o json > "${TMP_DIR}/routes.json"
  kubectl get apisixtlses.apisix.apache.org -n "${NAMESPACE}" -o json > "${TMP_DIR}/tls.json"
  need_svc=$(python3 - "${TMP_DIR}/routes.json" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    doc = json.load(f)
for item in doc.get("items") or []:
    spec = item.get("spec") or {}
    for r in spec.get("http") or []:
        for b in (r.get("backends") or []) + ([r["backend"]] if isinstance(r.get("backend"), dict) else []):
            if isinstance(b, dict) and b.get("servicePort") is not None and not str(b["servicePort"]).isdigit():
                print("1"); sys.exit(0)
    for r in spec.get("stream") or []:
        b = r.get("backend")
        if isinstance(b, dict) and b.get("servicePort") is not None and not str(b["servicePort"]).isdigit():
            print("1"); sys.exit(0)
print("0")
PYEOF
)
  if [ "${need_svc}" = "1" ]; then
    kubectl get services -n "${NAMESPACE}" -o json > "${TMP_DIR}/services.json" 2>/dev/null || printf '{"items":[]}' > "${TMP_DIR}/services.json"
  else
    printf '{"items":[]}' > "${TMP_DIR}/services.json"
  fi
  python3 - "${NAMESPACE}" "${TMP_DIR}/routes.json" "${TMP_DIR}/tls.json" "${TMP_DIR}/services.json" "${TMP_DIR}/expected.json" <<'PYEOF'
import json, sys, zlib

default_ns, routes_path, tls_path, svc_path, out_path = sys.argv[1:6]

def gen_id(raw: str) -> str:
    """Match apisix-ingress-controller 1.8.0 pkg/id/idgen.go GenID (crc32 IEEE %x)."""
    if not raw:
        return ""
    return format(zlib.crc32(raw.encode("utf-8")) & 0xFFFFFFFF, "x")

def compose_upstream_name(ns: str, svc: str, subset: str, port: str, gran: str) -> str:
    parts = [ns, svc]
    if subset:
        parts.append(subset)
    parts.append(port)
    if gran == "service":
        parts.append("service")
    return "_".join(parts)

with open(routes_path, encoding="utf-8") as f:
    routes_doc = json.load(f)
with open(tls_path, encoding="utf-8") as f:
    tls_doc = json.load(f)
with open(svc_path, encoding="utf-8") as f:
    raw_svc = f.read().strip()
svc_doc = json.loads(raw_svc) if raw_svc else {"items": []}

svc_ports = {}
for svc in svc_doc.get("items") or []:
    meta = svc.get("metadata") or {}
    sns = meta.get("namespace") or default_ns
    sname = meta.get("name") or ""
    for p in (svc.get("spec") or {}).get("ports") or []:
        if p.get("name") and p.get("port") is not None:
            svc_ports[(sns, sname, p["name"])] = str(p["port"])

def add_backend_upstream(upstreams: set, ns: str, b: dict) -> None:
    if not isinstance(b, dict) or not b.get("serviceName"):
        return
    sp = b.get("servicePort")
    if sp is None:
        return
    port_str = str(sp)
    if not port_str.isdigit():
        port_str = svc_ports.get((ns, b["serviceName"], port_str), port_str)
    name = compose_upstream_name(
        ns,
        b["serviceName"],
        b.get("subset") or "",
        port_str,
        b.get("resolveGranularity") or "",
    )
    upstreams.add(gen_id(name))

exp_routes = set()
exp_stream_routes = set()
exp_upstreams = set()
exp_ssls = set()

for item in routes_doc.get("items") or []:
    meta = item.get("metadata") or {}
    ns = meta.get("namespace") or default_ns
    cr_name = meta.get("name") or ""
    spec = item.get("spec") or {}
    for rule in spec.get("http") or []:
        rule_name = rule.get("name") or ""
        exp_routes.add(gen_id(f"{ns}_{cr_name}_{rule_name}"))
        for u in rule.get("upstreams") or []:
            if isinstance(u, dict) and u.get("name"):
                exp_upstreams.add(gen_id(f"{ns}_{u['name']}"))
        for b in rule.get("backends") or []:
            add_backend_upstream(exp_upstreams, ns, b)
        if isinstance(rule.get("backend"), dict):
            add_backend_upstream(exp_upstreams, ns, rule["backend"])
    for rule in spec.get("stream") or []:
        rule_name = rule.get("name") or ""
        exp_stream_routes.add(gen_id(f"{ns}_{cr_name}_{rule_name}_tcp"))
        if isinstance(rule.get("backend"), dict):
            add_backend_upstream(exp_upstreams, ns, rule["backend"])

for item in tls_doc.get("items") or []:
    meta = item.get("metadata") or {}
    ns = meta.get("namespace") or default_ns
    cr_name = meta.get("name") or ""
    exp_ssls.add(gen_id(f"{ns}_{cr_name}"))

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "routes": sorted(exp_routes),
            "stream_routes": sorted(exp_stream_routes),
            "upstreams": sorted(exp_upstreams),
            "ssls": sorted(exp_ssls),
        },
        f,
    )
PYEOF
}

read_actual_state() {
  local ep
  for ep in routes stream_routes upstreams ssls; do
    # APISIX answers HTTP 400 on /stream_routes when stream proxy is disabled (seen live
    # 2026-11-19). With no stream rules in the CRs there is nothing to compare, so skip it;
    # if CRs do declare stream rules, a failing fetch stays an error.
    if [ "${ep}" = "stream_routes" ] && python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["stream_routes"] else 1)' "${TMP_DIR}/expected.json"; then
      admin_get_ids "${ep}" || return 1
    elif [ "${ep}" = "stream_routes" ]; then
      printf '[]' > "${TMP_DIR}/actual_${ep}.json"
    else
      admin_get_ids "${ep}" || return 1
    fi
  done
  python3 - "${TMP_DIR}" <<'PYEOF'
import json, os, sys

tmp_dir = sys.argv[1]
with open(os.path.join(tmp_dir, "expected.json"), encoding="utf-8") as f:
    expected = json.load(f)

total_missing = 0
total_extra = 0
had_404 = False
stats = {}

for ep in ("routes", "stream_routes", "upstreams", "ssls"):
    with open(os.path.join(tmp_dir, f"actual_{ep}.json"), encoding="utf-8") as f:
        act_list = json.load(f)
    exp_set = set(expected[ep])
    act_set = set(act_list)
    missing = exp_set - act_set
    # Count excess duplicates if any plus objects whose ID is not in expected CR set.
    extra_count = len(act_set - exp_set) + max(0, len(act_list) - len(act_set))
    matched = len(exp_set) - len(missing)
    if os.path.exists(os.path.join(tmp_dir, f"404_{ep}")) and exp_set:
        had_404 = True
    total_missing += len(missing)
    total_extra += extra_count
    stats[ep] = (matched, len(exp_set), extra_count)

print(
    total_missing,
    total_extra,
    "true" if had_404 else "false",
    stats["routes"][0], stats["routes"][1],
    stats["stream_routes"][0], stats["stream_routes"][1],
    stats["upstreams"][0], stats["upstreams"][1],
    stats["ssls"][0], stats["ssls"][1],
    stats["routes"][2], stats["stream_routes"][2],
    stats["upstreams"][2], stats["ssls"][2],
)
PYEOF
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

warn_extra_objects() {
  local total_extra="$1" extra_r="$2" extra_sr="$3" extra_u="$4" extra_s="$5"
  if [ "${total_extra}" -gt 0 ]; then
    echo "WARN: APISIX contains ${total_extra} extra object(s) not derived from CRs in ${NAMESPACE} (routes=${extra_r}, stream_routes=${extra_sr}, upstreams=${extra_u}, ssls=${extra_s})." >&2
  fi
}

recover_divergence() {
  local state_line total_missing total_extra
  local matched_r exp_r matched_sr exp_sr matched_u exp_u matched_s exp_s
  local extra_r extra_sr extra_u extra_s round elapsed
  resolve_admin_key
  start_pf

  read_expected_ids
  state_line=$(read_actual_state)
  read -r total_missing total_extra LAST_HAD_404 \
    matched_r exp_r matched_sr exp_sr matched_u exp_u matched_s exp_s \
    extra_r extra_sr extra_u extra_s <<<"${state_line}"
  if [ "${total_missing}" -eq 0 ]; then
    echo "APISIX state in sync (${NAMESPACE}): routes=${matched_r}/${exp_r}, stream_routes=${matched_sr}/${exp_sr}, upstreams=${matched_u}/${exp_u}, ssls=${matched_s}/${exp_s}."
    warn_extra_objects "${total_extra}" "${extra_r}" "${extra_sr}" "${extra_u}" "${extra_s}"
    return 0
  fi

  echo "APISIX state diverged (${NAMESPACE}): routes=${matched_r}/${exp_r}, stream_routes=${matched_sr}/${exp_sr}, upstreams=${matched_u}/${exp_u}, ssls=${matched_s}/${exp_s} (missing=${total_missing}); triggering resync."
  warn_extra_objects "${total_extra}" "${extra_r}" "${extra_sr}" "${extra_u}" "${extra_s}"
  for ((round=1; round<=MAX_ROUNDS; round++)); do
    echo "APISIX divergence recovery round ${round}/${MAX_ROUNDS}: annotating CRs with narwhal.io/resync"
    touch_apisix_crs "${round}"
    elapsed=0
    while true; do
      state_line=$(read_actual_state)
      read -r total_missing total_extra LAST_HAD_404 \
        matched_r exp_r matched_sr exp_sr matched_u exp_u matched_s exp_s \
        extra_r extra_sr extra_u extra_s <<<"${state_line}"
      if [ "${total_missing}" -eq 0 ]; then
        echo "APISIX divergence recovered (${NAMESPACE}): routes=${matched_r}/${exp_r}, stream_routes=${matched_sr}/${exp_sr}, upstreams=${matched_u}/${exp_u}, ssls=${matched_s}/${exp_s}."
        warn_extra_objects "${total_extra}" "${extra_r}" "${extra_sr}" "${extra_u}" "${extra_s}"
        return 0
      fi
      if [ "${elapsed}" -ge "${DIVERGENCE_WAIT_SECONDS}" ]; then
        break
      fi
      sleep "${DIVERGENCE_POLL_INTERVAL}"
      elapsed=$((elapsed + DIVERGENCE_POLL_INTERVAL))
    done
  done

  echo "ERROR: APISIX state still diverged after ${MAX_ROUNDS} rounds (${NAMESPACE}): routes=${matched_r}/${exp_r}, stream_routes=${matched_sr}/${exp_sr}, upstreams=${matched_u}/${exp_u}, ssls=${matched_s}/${exp_s} (missing=${total_missing})." >&2
  warn_extra_objects "${total_extra}" "${extra_r}" "${extra_sr}" "${extra_u}" "${extra_s}"
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
