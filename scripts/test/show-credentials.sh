#!/bin/bash
# Show all Narwhal cluster credentials in one place.
# Run on master-1: bash scripts/test/show-credentials.sh
# Or from host: vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/show-credentials.sh"

set -u

DOMAIN="${DOMAIN:-local.narwhal.internal}"

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
section "SSH / Node access"
# -----------------------------------------------------------------------------
row "Vagrant user"   "vagrant / (vagrant key: \$(pwd)/.vagrant/machines/<name>/vmware_desktop/private_key)"
row "Master IPs"     "192.168.56.10-12 (master-1/2/3)"
row "Worker IPs"     "192.168.56.21-23 (worker-1/2/3)"
row "VIP"            "192.168.56.100 (kube-apiserver HA)"
row "MetalLB LB"     "192.168.56.200 (APISIX gateway)"

echo ""
echo "${GREEN}Done.${RESET} To export only one: ${YELLOW}kubectl get secret <name> -n <ns> -o jsonpath='{.data.<key>}' | base64 -d${RESET}"
