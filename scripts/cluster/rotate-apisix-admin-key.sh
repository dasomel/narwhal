#!/bin/bash
# Rotate the APISIX Admin API admin + viewer keys on a live cluster (D-apisix-key).
#
# Order matters -- Secret -> APISIX config -> APISIX restart -> ingress controller -> verify:
#   1. new random admin/viewer keys replace the apisix-admin-key Secret (single source of truth;
#      APISIX reads APISIX_ADMIN_KEY/APISIX_VIEWER_KEY from it, the IC reads it via existingSecret)
#   2. a bootstrap configmap/apisix that still holds LITERAL keys is rewritten to the new values
#      (a GitOps-rendered configmap holds env placeholders and needs no change, so ArgoCD has
#      nothing to revert; no key is stored in git or in Helm values)
#   3. APISIX restarts and re-reads the keys
#   4. the ingress controller is (re)pointed at the Secret, re-labelled out of ambient mesh and
#      restarted (it reads the key only at start)
#   5. verified from inside the cluster: old admin key -> 401, new admin key -> 200, new
#      viewer key -> 200. Key values are never printed.
# Idempotent: re-running simply rotates again.
set -euo pipefail

# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh
# shellcheck source=scripts/common/lib.sh
source /home/vagrant/scripts/common/lib.sh

export KUBECONFIG="${KUBECONFIG:-/home/vagrant/.kube/config-local}"
NS=platform-system

# admin_status <key> -> HTTP status of GET /routes via the loopback of an APISIX pod.
# The key travels over stdin (curl -K -), never argv.
admin_status() {
  printf 'header = "X-API-KEY: %s"\n' "$1" | kubectl exec -i -n "${NS}" deploy/apisix -- \
    curl -s -o /dev/null -w '%{http_code}' -K - "http://127.0.0.1:9180/apisix/admin/routes" 2>/dev/null || true
}

OLD_ADMIN=$(read_secret_field apisix-admin-key "${NS}" key) || exit 1
OLD_VIEWER=$(read_secret_field apisix-admin-key "${NS}" viewer) || exit 1
CM_ADMIN=$(apisix_cm_key admin)
CM_VIEWER=$(apisix_cm_key viewer)
NEW_ADMIN=$(openssl rand -hex 16)
NEW_VIEWER=$(openssl rand -hex 16)

echo "1/5 Updating Secret ${NS}/apisix-admin-key..."
kubectl create secret generic apisix-admin-key -n "${NS}" \
  --from-literal=key="${NEW_ADMIN}" --from-literal=viewer="${NEW_VIEWER}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "2/5 Updating literal keys in configmap/apisix (if any)..."
cfg=$(mktemp); trap 'rm -f "${cfg}"' EXIT
kubectl get configmap apisix -n "${NS}" -o jsonpath='{.data.config\.yaml}' > "${cfg}"
patched=false
# swap_in_cfg <old> <new>: replace every literal <old> in ${cfg}; values go via env, not argv.
swap_in_cfg() {
  [ -n "$1" ] && grep -Fq -- "$1" "${cfg}" || return 0
  OLD="$1" NEW="$2" awk '{ while ((i = index($0, ENVIRON["OLD"])) > 0) $0 = substr($0, 1, i-1) ENVIRON["NEW"] substr($0, i+length(ENVIRON["OLD"])); print }' \
    "${cfg}" > "${cfg}.new" && mv "${cfg}.new" "${cfg}"
  patched=true
}
# Literal keys may be the Secret's (rotation of an already-converted cluster) or the bootstrap
# configmap's (legacy cluster still on the public default).
swap_in_cfg "${CM_ADMIN}" "${NEW_ADMIN}"; swap_in_cfg "${CM_VIEWER}" "${NEW_VIEWER}"
swap_in_cfg "${OLD_ADMIN}" "${NEW_ADMIN}"; swap_in_cfg "${OLD_VIEWER}" "${NEW_VIEWER}"
if [ "${patched}" = true ]; then
  kubectl create configmap apisix -n "${NS}" --from-file="config.yaml=${cfg}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "  configmap holds env placeholders only; nothing to change (GitOps-safe)"
fi

echo "3/5 Restarting APISIX..."
kubectl rollout restart deployment/apisix -n "${NS}"
kubectl rollout status deployment/apisix -n "${NS}" --timeout=180s

echo "4/5 Pointing the ingress controller at the Secret and restarting it..."
helm upgrade apisix-ingress-controller "$(chart apisix-ingress-controller)" \
  --namespace "${NS}" --version 0.14.1 --skip-crds --reuse-values \
  --set config.apisix.existingSecret=apisix-admin-key \
  --set config.apisix.existingSecretAdminKeyKey=key \
  --set config.apisix.adminKey=
# The chart cannot set pod-template labels (see 08-1); helm upgrade may drop the patch.
kubectl patch deployment apisix-ingress-controller -n "${NS}" --type=merge \
  -p '{"spec":{"template":{"metadata":{"labels":{"istio.io/dataplane-mode":"none"}}}}}'
kubectl rollout restart deployment/apisix-ingress-controller -n "${NS}"
kubectl rollout status deployment/apisix-ingress-controller -n "${NS}" --timeout=180s

echo "5/5 Verifying from inside the cluster..."
fail=0
for check in "old admin key:${OLD_ADMIN}:401" "new admin key:${NEW_ADMIN}:200" "new viewer key:${NEW_VIEWER}:200"; do
  label="${check%%:*}"; rest="${check#*:}"; key="${rest%%:*}"; want="${rest##*:}"
  [ -n "${key}" ] || continue
  got=$(admin_status "${key}")
  if [ "${got}" = "${want}" ]; then echo "  OK   ${label}: HTTP ${got}"
  else echo "  FAIL ${label}: expected HTTP ${want}, got '${got}'" >&2; fail=1; fi
done
if kubectl logs -n "${NS}" deploy/apisix-ingress-controller --since=60s 2>/dev/null \
    | grep -Eqi 'unauthorized|status.?code[^0-9]*401'; then
  echo "  FAIL ingress controller logs show Admin API 401" >&2; fail=1
else
  echo "  OK   ingress controller logs show no Admin API 401"
fi
[ "${fail}" -eq 0 ] || { echo "ERROR: rotation verification failed." >&2; exit 1; }
echo "APISIX admin/viewer keys rotated and verified."
