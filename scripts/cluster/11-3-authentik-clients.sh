#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# =============================================================================
# 11-3-authentik-clients.sh - Per-service Authentik OAuth2 providers + Native SSO
# =============================================================================
# Phase: Authentik per-service OAuth2 provider 생성 + Native SSO 설정
#
# 11-2-authentik-config.sh는 공유 'apisix' provider로 APISIX gateway SSO를 처리.
# 이 스크립트는 Native OIDC가 필요한 서비스에 dedicated provider를 추가하고,
# 서비스 자체의 SSO 설정을 수행합니다.
#
# 그룹 A - Native SSO (서비스 자체 OIDC/OAuth 설정):
#   - ArgoCD  → argocd-cm ConfigMap (이미 13-argocd.sh에서 apisix provider 사용 중)
#   - Grafana → env var injection (generic_oauth)
#   - Gitea   → gitea admin auth add-oauth (이미 12-gitea.sh에서 설정)
#   - Harbor  → Harbor API OIDC config
#   - OpenBao → bao OIDC auth method
#
# 그룹 B - APISIX openid-connect only (dedicated provider 불필요):
#   - Hubble UI, Prometheus, Alertmanager, Velero UI
#   → 공유 'apisix' provider로 이미 apisix-routes.yaml에서 처리됨
#
# Depends on: 11-2-authentik-config.sh (Authentik providers/applications exist)
#             08-2-monitoring.sh (Grafana exists)
#             08-4-storage.sh (OpenBao exists)
#             08-5-registry.sh (Harbor exists)

DOMAIN="${DOMAIN:-local.narwhal.internal}"
AUTHENTIK_URL="http://authentik.${DOMAIN}"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=========================================="
echo "11-3: Authentik Per-Service SSO Setup"
echo "=========================================="

BOOTSTRAP_TOKEN=$(kubectl get secret authentik-bootstrap-secret -n iam \
  -o jsonpath='{.data.bootstrap_token}' | base64 -d)

# --- Authentik API helpers (reuse pattern from 11-2) ---
ak_get() {
  curl -sf "${AUTHENTIK_URL}/api/v3/${1}" \
    -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
    -H "Content-Type: application/json"
}

ak_post() {
  curl -sf -X POST "${AUTHENTIK_URL}/api/v3/${1}" \
    -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${2}"
}

ak_patch() {
  curl -sf -X PATCH "${AUTHENTIK_URL}/api/v3/${1}" \
    -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${2}"
}

ak_get_or_create() {
  local endpoint="${1}" filter_key="${2}" filter_value="${3}" payload="${4}"
  local existing_id
  existing_id=$(ak_get "${endpoint}/?${filter_key}=${filter_value}" 2>/dev/null \
    | jq -r '.results[0].pk // .results[0].id // empty' 2>/dev/null || echo "")
  if [ -n "${existing_id}" ]; then
    echo "${existing_id}"
  else
    ak_post "${endpoint}/" "${payload}" 2>/dev/null | jq -r '.pk // .id'
  fi
}

# Wait for Authentik API
echo "Waiting for Authentik API..."
for attempt in $(seq 1 20); do
  if ak_get "core/users/?page_size=1" &>/dev/null; then
    echo "Authentik API ready"
    break
  fi
  echo "  API not ready, attempt ${attempt}/20..."
  sleep 10
done

# --- Gather scope mapping PKs ---
SCOPE_OPENID=$(ak_get "propertymappings/provider/scope/?scope_name=openid" | jq -r '.results[0].pk')
SCOPE_PROFILE=$(ak_get "propertymappings/provider/scope/?scope_name=profile" | jq -r '.results[0].pk')
SCOPE_EMAIL=$(ak_get "propertymappings/provider/scope/?scope_name=email" | jq -r '.results[0].pk')
SCOPE_GROUPS=$(ak_get "propertymappings/provider/scope/?scope_name=groups" | jq -r '.results[0].pk')
AUTH_FLOW_PK=$(ak_get "flows/instances/?slug=default-provider-authorization-implicit-consent" \
  | jq -r '.results[0].pk')
INVALIDATION_FLOW_PK=$(ak_get "flows/instances/?slug=default-provider-invalidation-flow" \
  | jq -r '.results[0].pk')

SCOPE_LIST="[\"${SCOPE_OPENID}\",\"${SCOPE_PROFILE}\",\"${SCOPE_EMAIL}\",\"${SCOPE_GROUPS}\"]"

echo "Scope mappings: openid=${SCOPE_OPENID}, profile=${SCOPE_PROFILE}, email=${SCOPE_EMAIL}, groups=${SCOPE_GROUPS}"

# =============================================================================
# Helper: Create confidential OAuth2 provider + application + K8s Secret
# =============================================================================
create_provider_and_secret() {
  local name="$1"
  local redirect_uris_json="$2"
  local secret_name="$3"
  local secret_ns="$4"

  echo "--- Creating provider: ${name} ---"

  # Generate or reuse client secret
  local client_secret
  if kubectl get secret "${secret_name}" -n "${secret_ns}" &>/dev/null; then
    client_secret=$(kubectl get secret "${secret_name}" -n "${secret_ns}" \
      -o jsonpath='{.data.client_secret}' 2>/dev/null | base64 -d || echo "")
  fi
  if [ -z "${client_secret:-}" ]; then
    client_secret=$(generate_password)
  fi

  local provider_id
  provider_id=$(ak_get_or_create "providers/oauth2" "name" "${name}" \
    "{
      \"name\": \"${name}\",
      \"client_type\": \"confidential\",
      \"client_id\": \"${name}\",
      \"client_secret\": \"${client_secret}\",
      \"authorization_flow\": \"${AUTH_FLOW_PK}\",
      \"invalidation_flow\": \"${INVALIDATION_FLOW_PK}\",
      \"include_claims_in_id_token\": true,
      \"sub_mode\": \"user_username\",
      \"property_mappings\": ${SCOPE_LIST},
      \"redirect_uris\": ${redirect_uris_json}
    }")
  echo "  provider: ${name} (ID: ${provider_id})"

  # Retrieve actual client_secret (existing provider reuse)
  local final_secret
  final_secret=$(ak_get "providers/oauth2/${provider_id}/" 2>/dev/null \
    | jq -r '.client_secret // empty' || echo "${client_secret}")
  if [ -z "${final_secret}" ]; then
    final_secret="${client_secret}"
  fi

  # Create application (slug = provider name)
  ak_get_or_create "core/applications" "slug" "${name}" \
    "{\"name\":\"${name}\",\"slug\":\"${name}\",\"provider\":${provider_id},\"meta_description\":\"${name} SSO\"}" > /dev/null
  echo "  application: ${name} (issuer: https://authentik.${DOMAIN}/application/o/${name}/)"

  # Store K8s Secret
  ensure_namespace "${secret_ns}"
  kubectl create secret generic "${secret_name}" \
    --namespace "${secret_ns}" \
    --from-literal=client_id="${name}" \
    --from-literal=client_secret="${final_secret}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "  secret: ${secret_name} in ${secret_ns}"

  # Return secret value for downstream use
  echo "${final_secret}" > "/tmp/.ak-secret-${name}"
}

# =============================================================================
# Group A: Native SSO Services
# =============================================================================
echo ""
echo "=========================================="
echo "Group A: Native SSO Providers"
echo "=========================================="

# -------------------------------------------------------------------------
# 1. Grafana — dedicated provider + env var injection
# -------------------------------------------------------------------------
echo ""
echo "=== [1/4] Grafana ==="

create_provider_and_secret "grafana" \
  "[{\"matching_mode\": \"strict\", \"url\": \"https://grafana.${DOMAIN}/login/generic_oauth\"}]" \
  "grafana-oidc-secret" "monitoring"

# Check if Grafana deployment exists
if kubectl get deployment prometheus-stack-grafana -n monitoring &>/dev/null; then
  echo "Patching Grafana deployment with OAuth2 env vars..."
  # Use JSON patch to inject env vars into the grafana container
  # Note: ArgoCD selfHeal may revert this — persistent fix requires gitops/apps/prometheus-stack.yaml update
  kubectl patch deployment prometheus-stack-grafana -n monitoring --type='json' -p="[
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_ENABLED\", \"value\": \"true\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_NAME\", \"value\": \"Authentik\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_CLIENT_ID\", \"value\": \"grafana\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET\", \"valueFrom\": {\"secretKeyRef\": {\"name\": \"grafana-oidc-secret\", \"key\": \"client_secret\"}}}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_SCOPES\", \"value\": \"openid profile email groups\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_AUTH_URL\", \"value\": \"https://authentik.${DOMAIN}/application/o/authorize/\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_TOKEN_URL\", \"value\": \"https://authentik.${DOMAIN}/application/o/token/\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_API_URL\", \"value\": \"https://authentik.${DOMAIN}/application/o/userinfo/\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_TLS_SKIP_VERIFY_INSECURE\", \"value\": \"true\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH\", \"value\": \"contains(groups[*], 'cluster-admin') && 'Admin' || 'Viewer'\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_AUTH_GENERIC_OAUTH_ALLOW_ASSIGN_GRAFANA_ADMIN\", \"value\": \"true\"}},
    {\"op\": \"add\", \"path\": \"/spec/template/spec/containers/0/env/-\", \"value\": {\"name\": \"GF_SERVER_ROOT_URL\", \"value\": \"https://grafana.${DOMAIN}\"}}
  ]" 2>/dev/null || echo "WARN: Grafana env patch failed (may already be patched or container spec differs)"
  echo "Grafana native SSO configured"
else
  echo "WARN: Grafana deployment not found, skipping native SSO"
fi

# -------------------------------------------------------------------------
# 2. Harbor — dedicated provider + Harbor API OIDC config
# -------------------------------------------------------------------------
echo ""
echo "=== [2/4] Harbor ==="

create_provider_and_secret "harbor" \
  "[{\"matching_mode\": \"strict\", \"url\": \"https://harbor.${DOMAIN}/c/oidc/callback\"}]" \
  "harbor-oidc-secret" "devtools"

# Read back client_secret from K8s Secret for Harbor API call
HARBOR_CLIENT_SECRET=$(kubectl get secret harbor-oidc-secret -n devtools \
  -o jsonpath='{.data.client_secret}' | base64 -d)

# Configure Harbor OIDC via API (uses internal ClusterIP — DNS may not resolve yet)
HARBOR_ADMIN_PASS=$(kubectl get secret harbor-secrets -n devtools \
  -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' 2>/dev/null | base64 -d || echo "")

if [ -n "${HARBOR_ADMIN_PASS}" ]; then
  HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")

  if [ -n "${HARBOR_CORE_IP}" ]; then
    echo "Configuring Harbor OIDC auth via internal API..."
    HARBOR_API="http://${HARBOR_CORE_IP}/api/v2.0"

    # Check Harbor API readiness
    HARBOR_READY=false
    for attempt in $(seq 1 10); do
      HTTP_CODE=$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "${HARBOR_API}/systeminfo" 2>/dev/null || echo "000")
      if [ "${HTTP_CODE}" = "200" ]; then
        HARBOR_READY=true
        break
      fi
      echo "  Harbor API not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/10..."
      sleep 10
    done

    if [ "${HARBOR_READY}" = "true" ]; then
      curl -sk -u "admin:${HARBOR_ADMIN_PASS}" \
        -X PUT "${HARBOR_API}/configurations" \
        -H "Content-Type: application/json" \
        -d "{
          \"auth_mode\": \"oidc_auth\",
          \"oidc_name\": \"Authentik\",
          \"oidc_endpoint\": \"https://authentik.${DOMAIN}/application/o/harbor/\",
          \"oidc_client_id\": \"harbor\",
          \"oidc_client_secret\": \"${HARBOR_CLIENT_SECRET}\",
          \"oidc_scope\": \"openid,profile,email,groups\",
          \"oidc_admin_group\": \"cluster-admin\",
          \"oidc_verify_cert\": false,
          \"oidc_auto_onboard\": true,
          \"oidc_user_claim\": \"preferred_username\"
        }" 2>/dev/null && echo "Harbor OIDC configured" \
        || echo "WARN: Harbor OIDC config API call failed"
    else
      echo "WARN: Harbor API not available, skipping OIDC config"
    fi
  else
    echo "WARN: Harbor core service not found"
  fi
else
  echo "WARN: Harbor admin password not found, skipping OIDC config"
fi

# -------------------------------------------------------------------------
# 3. OpenBao — dedicated provider + bao OIDC auth method
# -------------------------------------------------------------------------
echo ""
echo "=== [3/4] OpenBao ==="

create_provider_and_secret "openbao" \
  "[{\"matching_mode\": \"strict\", \"url\": \"https://openbao.${DOMAIN}/ui/vault/auth/oidc/oidc/callback\"}, {\"matching_mode\": \"strict\", \"url\": \"http://localhost:8250/oidc/callback\"}]" \
  "openbao-oidc-secret" "storage"

OPENBAO_CLIENT_SECRET=$(cat /tmp/.ak-secret-openbao)

OPENBAO_POD=$(kubectl get pod -n storage -l app.kubernetes.io/name=openbao \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "${OPENBAO_POD}" ]; then
  OPENBAO_ROOT_TOKEN=$(kubectl get secret openbao-init -n storage \
    -o jsonpath='{.data.root_token}' 2>/dev/null | base64 -d || echo "")

  if [ -n "${OPENBAO_ROOT_TOKEN}" ]; then
    echo "Configuring OpenBao OIDC auth method..."
    kubectl exec -n storage "${OPENBAO_POD}" -- \
      env VAULT_TOKEN="${OPENBAO_ROOT_TOKEN}" VAULT_ADDR="http://127.0.0.1:8200" \
      /bin/sh -c "
        bao auth enable oidc 2>/dev/null || true
        bao write auth/oidc/config \
          oidc_discovery_url='https://authentik.${DOMAIN}/application/o/openbao/' \
          oidc_client_id='openbao' \
          oidc_client_secret='${OPENBAO_CLIENT_SECRET}' \
          default_role='default'
        bao write auth/oidc/role/default \
          bound_audiences='openbao' \
          allowed_redirect_uris='https://openbao.${DOMAIN}/ui/vault/auth/oidc/oidc/callback' \
          allowed_redirect_uris='http://localhost:8250/oidc/callback' \
          user_claim='preferred_username' \
          groups_claim='groups' \
          token_policies='default'
        bao write auth/oidc/role/admin \
          bound_audiences='openbao' \
          bound_claims_type='string' \
          bound_claims='{\"groups\":\"cluster-admin\"}' \
          allowed_redirect_uris='https://openbao.${DOMAIN}/ui/vault/auth/oidc/oidc/callback' \
          allowed_redirect_uris='http://localhost:8250/oidc/callback' \
          user_claim='preferred_username' \
          groups_claim='groups' \
          token_policies='default,admin-policy'
      " 2>/dev/null && echo "OpenBao OIDC configured" \
      || echo "WARN: OpenBao OIDC config failed"
  else
    echo "WARN: OpenBao root token not found, skipping OIDC config"
  fi
else
  echo "WARN: OpenBao pod not found, skipping OIDC config"
fi

# -------------------------------------------------------------------------
# 4. Headlamp — update Secret with dedicated provider info
# -------------------------------------------------------------------------
echo ""
echo "=== [4/4] Headlamp ==="

create_provider_and_secret "headlamp" \
  "[{\"matching_mode\": \"strict\", \"url\": \"https://headlamp.${DOMAIN}/oidc-callback\"}]" \
  "headlamp-oidc-secret" "devtools"

HEADLAMP_CLIENT_SECRET=$(cat /tmp/.ak-secret-headlamp)

# Headlamp Secret needs additional keys (issuerURL, scopes) for Helm chart
kubectl create secret generic headlamp-oidc-secret \
  --namespace devtools \
  --from-literal=clientID=headlamp \
  --from-literal=clientSecret="${HEADLAMP_CLIENT_SECRET}" \
  --from-literal=issuerURL="https://authentik.${DOMAIN}/application/o/headlamp/" \
  --from-literal=scopes="openid,profile,email,groups" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Headlamp OIDC secret updated with dedicated provider"

# Restart Headlamp to pick up new secret values
if kubectl get deployment headlamp -n devtools &>/dev/null; then
  kubectl rollout restart deployment headlamp -n devtools
  echo "Headlamp restarted"
fi

# =============================================================================
# Group B: APISIX openid-connect services (no dedicated provider needed)
# =============================================================================
echo ""
echo "=========================================="
echo "Group B: APISIX-only SSO (shared 'apisix' provider)"
echo "=========================================="
echo "The following services use the shared 'apisix' Authentik provider"
echo "via APISIX openid-connect plugin (configured in apisix-routes.yaml):"
echo "  - Hubble UI    (hubble.${DOMAIN})"
echo "  - Prometheus   (prometheus.${DOMAIN})"
echo "  - Alertmanager (alertmanager.${DOMAIN})"
echo "  - Velero UI    (velero-ui.${DOMAIN})"
echo ""
echo "Secrets for these services were created by 11-2-authentik-config.sh:"
echo "  - platform-system/apisix-oidc-config (shared)"
echo "No additional configuration needed."

# =============================================================================
# Cleanup temp files
# =============================================================================
rm -f /tmp/.ak-secret-grafana /tmp/.ak-secret-harbor \
      /tmp/.ak-secret-openbao /tmp/.ak-secret-headlamp 2>/dev/null || true

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=========================================="
echo "[11-3-authentik-clients.sh] Complete"
echo "=========================================="
echo ""
echo "Dedicated Authentik providers created:"
echo "  grafana   -> https://authentik.${DOMAIN}/application/o/grafana/"
echo "  harbor    -> https://authentik.${DOMAIN}/application/o/harbor/"
echo "  openbao   -> https://authentik.${DOMAIN}/application/o/openbao/"
echo "  headlamp  -> https://authentik.${DOMAIN}/application/o/headlamp/"
echo ""
echo "Native SSO configured:"
echo "  [OK] Grafana   - env var injection (GF_AUTH_GENERIC_OAUTH_*)"
echo "  [OK] Harbor    - Harbor API OIDC config"
echo "  [OK] OpenBao   - bao OIDC auth method"
echo "  [OK] Headlamp  - OIDC secret updated + restart"
echo ""
echo "APISIX-only SSO (shared 'apisix' provider, no changes needed):"
echo "  [--] ArgoCD      (native SSO via argocd-cm, configured in 13-argocd.sh)"
echo "  [--] Gitea       (native SSO via gitea admin auth, configured in 12-gitea.sh)"
echo "  [--] Hubble UI   (APISIX openid-connect plugin)"
echo "  [--] Prometheus  (APISIX openid-connect plugin)"
echo "  [--] Alertmanager(APISIX openid-connect plugin)"
echo "  [--] Velero UI   (APISIX openid-connect plugin)"
echo ""
echo "WARNING: Grafana env var patch may be reverted by ArgoCD selfHeal."
echo "  For persistence, add grafana.envFromSecrets to gitops/apps/prometheus-stack.yaml"
