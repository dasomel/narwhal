#!/bin/bash
# Show all Narwhal cluster credentials in one place.
# Run on master-1: bash scripts/test/show-credentials.sh
# Or from host: vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/show-credentials.sh"
# Against Kakao from a workstation: PROVIDER=kakao scripts/test/show-credentials.sh --context narwhal-kakao
#
# Every secret lookup is provider-independent — the namespaces are identical on
# both — so PROVIDER only selects the domain and the node-access section. That
# section used to hardcode the Vagrant values, which meant running this against
# Kakao printed another cluster's addresses with no indication they were wrong.

set -u

PROVIDER="${PROVIDER:-vagrant}"
[ "${PROVIDER}" = "local" ] && PROVIDER="vagrant"   # verify-isolation.sh spells it 'local'
CONTEXT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --context) CONTEXT="${2:-}"; shift 2 ;;
    --context=*) CONTEXT="${1#*=}"; shift ;;
    --provider) PROVIDER="${2:-}"; shift 2 ;;
    --provider=*) PROVIDER="${1#*=}"; shift ;;
    -h|--help)
      echo "usage: $0 [--provider vagrant|kakao] [--context <kubectl-context>]"
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "${PROVIDER}" in
  vagrant) DOMAIN="${DOMAIN:-local.narwhal.internal}" ;;
  kakao)   DOMAIN="${DOMAIN:-kakao.narwhal.internal}" ;;
  *) echo "unknown PROVIDER '${PROVIDER}' (expected vagrant or kakao)" >&2; exit 2 ;;
esac

# Every kubectl call goes through this so --context reaches all of them. The
# timeout matters: this script makes ~25 secret lookups, and against an
# unreachable cluster kubectl's default retry made the run hang for minutes
# instead of printing "(missing)" and moving on.
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-10s}"
kubectl() {
  if [ -n "${CONTEXT}" ]; then
    command kubectl --context "${CONTEXT}" --request-timeout="${REQUEST_TIMEOUT}" "$@"
  else
    command kubectl --request-timeout="${REQUEST_TIMEOUT}" "$@"
  fi
}

# Colors (falls back to empty when not a TTY)
if [ -t 1 ]; then
  BOLD=$'\e[1m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; CYAN=$'\e[36m'; RESET=$'\e[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
fi

# Helper: read secret key, base64-decode, fall back to label when missing
get_secret() {
  local ns=$1 name=$2 key=$3
  kubectl get secret "${name}" -n "${ns}" -o jsonpath="{.data.${key}}" 2>/dev/null \
    | base64 -d 2>/dev/null || echo "(missing)"
}

row() {
  printf "  %-18s %s\n" "$1" "$2"
}

section() {
  echo ""
  echo "${BOLD}${CYAN}=== $1 ===${RESET}"
}

echo "${BOLD}Narwhal Cluster Credentials${RESET}"
echo "Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Provider: ${PROVIDER}   Domain: ${DOMAIN}${CONTEXT:+   Context: ${CONTEXT}}"

# Reach the API once before the ~25 secret lookups. Per-call --request-timeout
# is not enough on its own: against a halted cluster kubectl still retries API
# discovery, so one lookup costs ~15s and the full run took over five minutes to
# print nothing but "(missing)". Measured against a down Vagrant cluster.
if ! kubectl get --raw /readyz >/dev/null 2>&1; then
  SERVER=$(command kubectl config view --minify ${CONTEXT:+--context "${CONTEXT}"} \
    -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)
  echo ""
  echo "${YELLOW}Cannot reach the cluster API${SERVER:+ at ${SERVER}} — nothing to read.${RESET}"
  case "${PROVIDER}" in
    kakao) echo "  The API LB certificate has no public-IP SAN, so a bastion tunnel is required:"
           echo "    ssh -f -N -L 6443:<api-lb-private>:6443 ubuntu@<bastion>" ;;
    *)     echo "  Are the VMs running?  vagrant status" ;;
  esac
  exit 1
fi

# -----------------------------------------------------------------------------
section "Keycloak (IAM) — https://keycloak.${DOMAIN}"
# -----------------------------------------------------------------------------
KC_ADMIN_USER=$(get_secret iam keycloak-initial-admin username)
KC_ADMIN_PASS=$(get_secret iam keycloak-initial-admin password)
row "Admin user"     "${KC_ADMIN_USER}"
row "Admin password" "${KC_ADMIN_PASS}"
echo ""
echo "  ${YELLOW}Realm 'narwhal' users (pre-seeded by 11-2-keycloak-config.sh)${RESET}"
if kubectl get secret keycloak-user-passwords -n iam >/dev/null 2>&1; then
  for user in admin dev view guest; do
    pw=$(get_secret iam keycloak-user-passwords "${user}")
    row "${user}" "${pw}"
  done
else
  echo "  (keycloak-user-passwords secret not found)"
fi

# -----------------------------------------------------------------------------
section "ArgoCD — https://argocd.${DOMAIN}"
# -----------------------------------------------------------------------------
ARGO_PASS=$(get_secret devtools argocd-initial-admin-secret password)
row "Admin user"     "admin"
row "Admin password" "${ARGO_PASS}"

# -----------------------------------------------------------------------------
section "Gitea — https://gitea.${DOMAIN}"
# -----------------------------------------------------------------------------
GITEA_USER=$(get_secret devtools gitea-admin username)
GITEA_PASS=$(get_secret devtools gitea-admin admin-password)
row "Admin user"     "${GITEA_USER:-gitea-admin}"
row "Admin password" "${GITEA_PASS}"

# -----------------------------------------------------------------------------
section "Harbor — https://harbor.${DOMAIN}"
# -----------------------------------------------------------------------------
HB_PASS=$(get_secret devtools harbor-secrets HARBOR_ADMIN_PASSWORD)
row "Admin user"     "admin"
row "Admin password" "${HB_PASS}"
HB_SECRETKEY=$(get_secret devtools harbor-shared-secrets secretKey)
HB_CORE=$(get_secret devtools harbor-shared-secrets secret)
HB_JOBSVC=$(get_secret devtools harbor-shared-secrets JOBSERVICE_SECRET)
row "secretKey"      "${HB_SECRETKEY}"
row "core secret"    "${HB_CORE}"
row "jobservice sec" "${HB_JOBSVC}"

# -----------------------------------------------------------------------------
section "Grafana — https://grafana.${DOMAIN}"
# -----------------------------------------------------------------------------
GRAFANA_USER=$(get_secret monitoring grafana-secrets admin-user)
GRAFANA_PASS=$(get_secret monitoring grafana-secrets admin-password)
row "Admin user"     "${GRAFANA_USER:-admin}"
row "Admin password" "${GRAFANA_PASS}"

# -----------------------------------------------------------------------------
section "OpenBao — https://openbao.${DOMAIN}"
# -----------------------------------------------------------------------------
# 08-4-storage.sh initialises OpenBao and writes keys to one of these secret names
for ob_secret in openbao-init openbao-keys openbao-root-token; do
  for key in root-token root_token; do
    OB_ROOT=$(get_secret storage "${ob_secret}" "${key}")
    [[ "${OB_ROOT}" != "(missing)" && -n "${OB_ROOT}" ]] && break 2
  done
done
OB_UNSEAL=$(get_secret storage "${ob_secret}" unseal-key)
[[ "${OB_UNSEAL}" == "(missing)" || -z "${OB_UNSEAL}" ]] && OB_UNSEAL=$(get_secret storage "${ob_secret}" unseal_keys_b64)
if [[ -n "${OB_ROOT}" && "${OB_ROOT}" != "(missing)" ]]; then
  row "Secret name" "${ob_secret}"
  row "Root token"  "${OB_ROOT}"
  row "Unseal key"  "${OB_UNSEAL}"
else
  echo "  (OpenBao not initialised — run: kubectl exec -n storage openbao-0 -- bao operator init)"
fi

# -----------------------------------------------------------------------------
section "APISIX Admin API"
# -----------------------------------------------------------------------------
# Vuln 2 fix: admin key lives in the apisix-admin-key Secret (08-1-networking.sh)
APISIX_KEY=$(get_secret platform-system apisix-admin-key key)
row "Admin URL"      "http://apisix-admin.platform-system.svc.cluster.local:9180/apisix/admin"
row "Admin API key"  "${APISIX_KEY}"

# -----------------------------------------------------------------------------
section "PostgreSQL (CNPG narwhal-db)"
# -----------------------------------------------------------------------------
# 07-cnpg.sh bundles everything into a single narwhal-db-credentials secret
DB_USER=$(get_secret database narwhal-db-credentials username)
DB_PASS=$(get_secret database narwhal-db-credentials password)
HARBOR_DB_PASS=$(get_secret database narwhal-db-credentials harbor-password)
GITEA_DB_PASS=$(get_secret database narwhal-db-credentials gitea-password)
KEYCLOAK_DB_USER=$(get_secret iam keycloak-db-secret username)
KEYCLOAK_DB_PASS=$(get_secret iam keycloak-db-secret password)
row "Host (rw)"      "narwhal-db-rw.database.svc.cluster.local:5432"
row "app user"       "${DB_USER}"
row "app password"   "${DB_PASS}"
echo ""
echo "  ${YELLOW}Service-level DB credentials${RESET}"
row "keycloak" "${KEYCLOAK_DB_USER} / ${KEYCLOAK_DB_PASS}"
row "harbor"   "harbor / ${HARBOR_DB_PASS}"
row "gitea"    "gitea / ${GITEA_DB_PASS}"

# -----------------------------------------------------------------------------
section "OIDC client secrets (Keycloak → platform-system)"
# -----------------------------------------------------------------------------
for svc in hubble prometheus velero-ui nfs-quota-agent oauth2-proxy; do
  cs=$(get_secret platform-system "${svc}-oidc-secret" client_secret)
  [[ "${cs}" == "(missing)" || -z "${cs}" ]] && continue
  row "${svc}" "${cs}"
done

# -----------------------------------------------------------------------------
section "SSH / Node access (PROVIDER=${PROVIDER})"
# -----------------------------------------------------------------------------
# Read the node addresses off the cluster rather than hardcoding them. The old
# fixed 192.168.56.x list was wrong the moment it was read against anything but
# Vagrant, and it also went stale on Kakao when the VPC moved to 172.17.0.0/24.
node_ips() {
  local selector=$1
  kubectl get nodes -l "${selector}" \
    -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{" "}{end}' 2>/dev/null
}
# Negation belongs in the label selector; kubectl's jsonpath has no `!` filter.
MASTER_IPS=$(node_ips 'node-role.kubernetes.io/control-plane')
WORKER_IPS=$(node_ips '!node-role.kubernetes.io/control-plane')
row "Masters"        "${MASTER_IPS:-(unreachable)}"
row "Workers"        "${WORKER_IPS:-(unreachable)}"

if [ "${PROVIDER}" = "kakao" ]; then
  # The public addresses live in the OpenTofu state, which only exists in a repo
  # checkout — this script also runs on master-1, where it does not. Fall back to
  # the environment, then to a label, so the section degrades instead of lying.
  TFSTATE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)/csp/kakao-cloud/terraform/terraform.tfstate"
  tf_ip() {
    [ -f "${TFSTATE}" ] || return 0
    python3 -c '
import json, sys
path, want = sys.argv[1], sys.argv[2]
try:
    state = json.load(open(path))
except Exception:
    sys.exit(0)
for res in state.get("resources", []):
    if res.get("name") != want:
        continue
    for inst in res.get("instances", []):
        ip = inst.get("attributes", {}).get("public_ip")
        if ip:
            print(ip)
            sys.exit(0)
' "${TFSTATE}" "$1" 2>/dev/null
  }
  BASTION_IP="${BASTION_IP:-$(tf_ip bastion_public)}"
  API_LB_IP="${API_LB_IP:-$(tf_ip master_lb_public)}"
  INGRESS_LB_IP="${INGRESS_LB_IP:-$(tf_ip worker_lb_public)}"
  KEYPAIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)/csp/kakao-cloud/terraform/KPAAS_KEYPAIR.pem"
  row "SSH user"       "ubuntu (key: ${KEYPAIR})"
  row "Bastion"        "${BASTION_IP:-(set BASTION_IP; tfstate not readable here)}"
  row "API LB"         "${API_LB_IP:-(unknown)}:6443 — cert has no public-IP SAN, tunnel via the bastion"
  row "Ingress LB"     "${INGRESS_LB_IP:-(unknown)}:443 (APISIX gateway NodePort 31443)"
  echo ""
  echo "  ${YELLOW}Nodes sit on private VPC addresses; reach them through the bastion:${RESET}"
  echo "    ssh -J ubuntu@${BASTION_IP:-<bastion>} ubuntu@<node-ip>"
  echo "    ssh -f -N -L 6443:<api-lb-private>:6443 ubuntu@${BASTION_IP:-<bastion>}   # then kubectl works"
else
  row "SSH user"       "vagrant (key: \$(pwd)/.vagrant/machines/<name>/vmware_desktop/private_key)"
  row "VIP"            "192.168.56.100 (kube-apiserver HA, kube-vip)"
  row "MetalLB LB"     "192.168.56.200 (APISIX gateway)"
fi

echo ""
echo "${GREEN}Done.${RESET} To export only one: ${YELLOW}kubectl get secret <name> -n <ns> -o jsonpath='{.data.<key>}' | base64 -d${RESET}"
