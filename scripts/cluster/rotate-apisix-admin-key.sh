#!/bin/bash
# Rotate the APISIX Admin API admin + viewer keys on a live cluster (D-apisix-key).
#
# Order matters -- Secret -> APISIX config -> APISIX restart -> ingress controller -> portal -> verify:
#   1. new random admin/viewer keys replace the apisix-admin-key Secret (single source of truth;
#      APISIX reads APISIX_ADMIN_KEY/APISIX_VIEWER_KEY from it, the IC reads it via existingSecret)
#   2. a bootstrap configmap/apisix that still holds LITERAL keys is rewritten to the new values
#      (a GitOps-rendered configmap holds env placeholders and needs no change, so ArgoCD has
#      nothing to revert; no key is stored in git or in Helm values)
#   3. APISIX restarts and re-reads the keys
#   4. the ingress controller is (re)pointed at the Secret, re-labelled out of ambient mesh and
#      restarted (it reads the key only at start)
#   5. devtools/narwhal-portal-secrets (APISIX_API_KEY, APISIX_API_KEY_READONLY) is re-synced and
#      the portal restarted, so it does not keep the old key
#   6. verified through `kubectl port-forward svc/apisix-admin` (lands on the pod loopback, which
#      allow_admin permits): old admin key -> 401, new admin key -> 200, new viewer key -> 200.
# Rollback: any failure after step 1 restores the previous Secret/configmap/portal Secret,
# restarts APISIX, re-applies the IC and re-verifies the OLD key, so APISIX and the IC never stay
# on different keys. Key values never reach argv or the terminal; temp files are 0600 in a 0700
# directory removed on exit. Idempotent: re-running simply rotates again.
set -Eeuo pipefail
umask 077

SCRIPTS_DIR="${NARWHAL_SCRIPTS_DIR:-/home/vagrant/scripts}"
# shellcheck source=/dev/null
source "${SCRIPTS_DIR}/common/lib-charts.sh"
# shellcheck source=scripts/common/lib.sh
source "${SCRIPTS_DIR}/common/lib.sh"

export KUBECONFIG="${KUBECONFIG:-/home/vagrant/.kube/config-local}"
NS=platform-system
PORTAL_NS=devtools
PORTAL_SECRET=narwhal-portal-secrets

T=$(mktemp -d)
PF_PID=""
CHANGED=false
ROLLING_BACK=false
PORTAL_CHANGED=false
CM_CHANGED=false
KEEP_T=false

stop_pf() {
  if [ -n "${PF_PID}" ]; then kill "${PF_PID}" 2>/dev/null || true; wait "${PF_PID}" 2>/dev/null || true; PF_PID=""; fi
}

# put_key <file> <value>: value goes to a file via the printf builtin, never argv.
put_key() { printf '%s' "$2" > "$1"; }

# apply_secret <admin-file> <viewer-file>
apply_secret() {
  kubectl create secret generic apisix-admin-key -n "${NS}" \
    --from-file=key="$1" --from-file=viewer="$2" --dry-run=client -o yaml | kubectl apply -f -
}

# apply_ic: point the IC helm release at the Secret, re-label and restart it.
apply_ic() {
  helm upgrade apisix-ingress-controller "$(chart apisix-ingress-controller)" \
    --force-conflicts --namespace "${NS}" --version 0.14.1 --skip-crds --reuse-values \
    --set config.apisix.existingSecret=apisix-admin-key \
    --set config.apisix.existingSecretAdminKeyKey=key \
    --set config.apisix.adminKey=
  # The chart cannot set pod-template labels (see 08-1); helm upgrade may drop the patch.
  kubectl patch deployment apisix-ingress-controller -n "${NS}" --type=merge \
    -p '{"spec":{"template":{"metadata":{"labels":{"istio.io/dataplane-mode":"none"}}}}}'
  kubectl rollout restart deployment/apisix-ingress-controller -n "${NS}"
  kubectl rollout status deployment/apisix-ingress-controller -n "${NS}" --timeout=180s
}

restart_apisix() {
  kubectl rollout restart deployment/apisix -n "${NS}"
  kubectl rollout status deployment/apisix -n "${NS}" --timeout=180s
}

# sync_portal <admin-file> <viewer-file>: patch the portal Secret from files, restart the portal.
sync_portal() {
  local a v
  a=$(base64 < "$1" | tr -d '\n'); v=$(base64 < "$2" | tr -d '\n')
  printf '{"data":{"APISIX_API_KEY":"%s","APISIX_API_KEY_READONLY":"%s"}}' "${a}" "${v}" > "${T}/portal.patch"
  kubectl patch secret "${PORTAL_SECRET}" -n "${PORTAL_NS}" --type=merge --patch-file="${T}/portal.patch"
  kubectl rollout restart deployment/narwhal-portal -n "${PORTAL_NS}"
}

# start_pf: background `kubectl port-forward svc/apisix-admin` in THIS shell (not a command
# substitution, so PF_PID reaches the EXIT/INT/TERM trap). Retries with a new port when the
# bind fails (the process exits within a second).
start_pf() {
  for _ in 1 2 3 4 5; do
    PF_PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
    kubectl port-forward -n "${NS}" svc/apisix-admin "${PF_PORT}:9180" >/dev/null 2>&1 &
    PF_PID=$!
    sleep 1
    if kill -0 "${PF_PID}" 2>/dev/null; then return 0; fi
    wait "${PF_PID}" 2>/dev/null || true; PF_PID=""
  done
  echo "ERROR: kubectl port-forward to svc/apisix-admin failed to start." >&2
  return 1
}

# admin_status <key>: HTTP status of GET /routes through the running port-forward (pod
# loopback). Key travels over stdin (curl -K -), never argv. Prints 000 when unreachable.
admin_status() {
  local code="000"
  for _ in $(seq 1 20); do
    code=$(printf 'header = "X-API-KEY: %s"\n' "$1" | curl -s -o /dev/null -m 5 -w '%{http_code}' -K - \
      "http://127.0.0.1:${PF_PORT}/apisix/admin/routes" 2>/dev/null || true)
    [ "${code}" != 000 ] && [ -n "${code}" ] && break
    code=000; sleep 1
  done
  printf '%s' "${code}"
}

# verify_keys <label:keyfile:want>...: every check must return the wanted status.
verify_keys() {
  local c label file want got fail=0
  start_pf || return 1
  for c in "$@"; do
    label="${c%%:*}"; want="${c##*:}"; file="${c#*:}"; file="${file%:*}"
    got=$(admin_status "$(cat "${file}")")
    if [ "${got}" = "${want}" ]; then echo "  OK   ${label}: HTTP ${got}"
    else echo "  FAIL ${label}: expected HTTP ${want}, got '${got}'" >&2; fail=1; fi
  done
  stop_pf
  return "${fail}"
}

rollback() {
  ROLLING_BACK=true
  trap - EXIT ERR
  stop_pf
  echo "ROLLBACK: restoring the previous APISIX keys..." >&2
  local rc=0
  apply_secret "${T}/old_admin" "${T}/old_viewer" || rc=1
  if [ "${CM_CHANGED}" = true ]; then
    kubectl create configmap apisix -n "${NS}" --from-file="config.yaml=${T}/old_cfg" \
      --dry-run=client -o yaml | kubectl apply -f - || rc=1
  fi
  restart_apisix || rc=1
  apply_ic || rc=1
  if [ "${PORTAL_CHANGED}" = true ]; then sync_portal "${T}/old_admin" "${T}/old_viewer" || rc=1; fi
  verify_keys "old admin key restored:${T}/old_admin:200" || rc=1
  if [ "${rc}" -eq 0 ]; then
    echo "ROLLBACK COMPLETE: APISIX and the ingress controller are back on the previous keys; rotation FAILED." >&2
  else
    KEEP_T=true
    echo "ROLLBACK INCOMPLETE: APISIX and the ingress controller may be on different keys." >&2
    echo "  Re-run this script to converge both on one new key (it is idempotent), or restore manually from the backup" >&2
    echo "  in ${T} (0700; old_admin/old_viewer = previous keys, old_cfg = previous configmap config.yaml)." >&2
    echo "  Delete ${T} once the cluster is healthy: it holds key material." >&2
  fi
}

on_exit() {
  local rc=$?
  trap - EXIT ERR
  stop_pf
  if [ "${rc}" -ne 0 ] && [ "${CHANGED}" = true ] && [ "${ROLLING_BACK}" != true ]; then rollback; fi
  if [ "${KEEP_T}" != true ]; then rm -rf "${T}"; fi
  exit "${rc}"
}
trap on_exit EXIT
trap 'exit 1' ERR
trap 'exit 130' INT
trap 'exit 143' TERM

OLD_ADMIN=$(read_secret_field apisix-admin-key "${NS}" key) || exit 1
OLD_VIEWER=$(read_secret_field apisix-admin-key "${NS}" viewer) || exit 1
# #399 case: a GitOps-converted Secret may hold env placeholders; fall back to the configmap
# literal so the backup (and a rollback) restores a real key. Never roll back to a placeholder.
case "${OLD_ADMIN}" in ''|*'{'*|*'$'*) OLD_ADMIN=$(apisix_cm_key admin) ;; esac
case "${OLD_VIEWER}" in ''|*'{'*|*'$'*) OLD_VIEWER=$(apisix_cm_key viewer) ;; esac
case "${OLD_ADMIN}${OLD_VIEWER}" in *'{'*|*'$'*) OLD_ADMIN="" ;; esac
[ -n "${OLD_ADMIN}" ] && [ -n "${OLD_VIEWER}" ] || { echo "ERROR: Secret ${NS}/apisix-admin-key (and configmap/apisix) hold no restorable key; run 08-1-networking.sh first." >&2; exit 1; }
put_key "${T}/old_admin" "${OLD_ADMIN}"; put_key "${T}/old_viewer" "${OLD_VIEWER}"
put_key "${T}/new_admin" "$(openssl rand -hex 16)"; put_key "${T}/new_viewer" "$(openssl rand -hex 16)"
CM_ADMIN=$(apisix_cm_key admin)
CM_VIEWER=$(apisix_cm_key viewer)
kubectl get configmap apisix -n "${NS}" -o jsonpath='{.data.config\.yaml}' > "${T}/old_cfg"
cp "${T}/old_cfg" "${T}/cfg"

echo "1/6 Updating Secret ${NS}/apisix-admin-key..."
CHANGED=true
apply_secret "${T}/new_admin" "${T}/new_viewer"

echo "2/6 Updating literal keys in configmap/apisix (if any)..."
# swap_in_cfg <old> <new-file>: replace every literal <old> in the working config; values via env.
swap_in_cfg() {
  [ -n "$1" ] || return 0
  OLD="$1" awk 'index($0, ENVIRON["OLD"]) {f=1} END {exit !f}' "${T}/cfg" || return 0
  OLD="$1" NEW="$(cat "$2")" awk '{ while ((i = index($0, ENVIRON["OLD"])) > 0) $0 = substr($0, 1, i-1) ENVIRON["NEW"] substr($0, i+length(ENVIRON["OLD"])); print }' \
    "${T}/cfg" > "${T}/cfg.new" && mv "${T}/cfg.new" "${T}/cfg"
  CM_CHANGED=true
}
# Literal keys may be the bootstrap configmap's (legacy cluster) or the Secret's value.
swap_in_cfg "${CM_ADMIN}" "${T}/new_admin"; swap_in_cfg "${CM_VIEWER}" "${T}/new_viewer"
swap_in_cfg "${OLD_ADMIN}" "${T}/new_admin"; swap_in_cfg "${OLD_VIEWER}" "${T}/new_viewer"
if [ "${CM_CHANGED}" = true ]; then
  kubectl create configmap apisix -n "${NS}" --from-file="config.yaml=${T}/cfg" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "  configmap holds env placeholders only; nothing to change (GitOps-safe)"
fi

echo "3/6 Restarting APISIX..."
restart_apisix

echo "4/6 Pointing the ingress controller at the Secret and restarting it..."
apply_ic

echo "5/6 Re-syncing the portal's APISIX keys (${PORTAL_NS}/${PORTAL_SECRET})..."
if kubectl get secret "${PORTAL_SECRET}" -n "${PORTAL_NS}" >/dev/null 2>&1; then
  PORTAL_CHANGED=true
  sync_portal "${T}/new_admin" "${T}/new_viewer"
else
  echo "  ${PORTAL_SECRET} not present; run 13-2-narwhal-portal-bindings.sh after the portal is deployed"
fi

echo "6/6 Verifying through the Admin API..."
verify_keys "old admin key:${T}/old_admin:401" "new admin key:${T}/new_admin:200" "new viewer key:${T}/new_viewer:200"
if kubectl logs -n "${NS}" deploy/apisix-ingress-controller --since=60s 2>/dev/null \
    | grep -Eqi 'unauthorized|status.?code[^0-9]*401'; then
  echo "  FAIL ingress controller logs show Admin API 401" >&2
  exit 1
fi
echo "  OK   ingress controller logs show no Admin API 401"
echo "APISIX admin/viewer keys rotated and verified."
