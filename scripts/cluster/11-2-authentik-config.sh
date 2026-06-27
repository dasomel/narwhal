#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 11-2-authentik-config.sh
# Phase: Authentik REST API 구성 (keycloak-realm + keycloak-clients 대체)
# - Groups: cluster-admin, developer, viewer, guest
# - Users: admin, dev, view, guest (그룹 할당 포함)
# - Custom scope mapping: groups 클레임 (Python expression)
# - OAuth2 Provider: kubernetes (public, K8s API server용)
# - OAuth2 Provider: apisix (confidential, APISIX gateway용)
# - Applications: kubernetes, apisix (slug → issuer URL 결정)
# - K8s Secrets: apisix-oidc-config, grafana-oauth-secret, headlamp-oidc-secret
# Depends on: 11-authentik.sh (Authentik pod ready, HTTPS 라우트 존재)
#
# NOTE: 재실행 시 기존 object는 update되지 않음 (creation-only idempotency).
# 재구성 필요 시 Authentik UI에서 해당 provider/application 삭제 후 재실행.

DOMAIN="${DOMAIN:-local.narwhal.internal}"
# Use external URL via dnsmasq+APISIX (in-cluster FQDN not resolvable from master node host)
AUTHENTIK_URL="http://authentik.${DOMAIN}"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Configuring Authentik via REST API ==="

BOOTSTRAP_TOKEN=$(kubectl get secret authentik-bootstrap-secret -n iam \
  -o jsonpath='{.data.bootstrap_token}' | base64 -d)

# Helper: GET
ak_get() {
  curl -sf "${AUTHENTIK_URL}/api/v3/${1}" \
    -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
    -H "Content-Type: application/json"
}

# Helper: POST
ak_post() {
  curl -sf -X POST "${AUTHENTIK_URL}/api/v3/${1}" \
    -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${2}"
}

# Helper: GET-or-create (idempotent for creation).
# Returns pk/id of existing or newly created object.
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
echo "Waiting for Authentik API to be ready..."
for attempt in $(seq 1 30); do
  if ak_get "core/users/?page_size=1" &>/dev/null; then
    echo "Authentik API ready"
    break
  fi
  echo "  API not ready, attempt ${attempt}/30..."
  sleep 10
done

#=========================================
# Groups
#=========================================
echo "=== Creating groups ==="

declare -A GROUP_IDS
for group_name in cluster-admin developer viewer guest; do
  GROUP_ID=$(ak_get_or_create "core/groups" "name" "${group_name}" \
    "{\"name\":\"${group_name}\",\"is_superuser\":false}")
  GROUP_IDS["${group_name}"]="${GROUP_ID}"
  echo "  -> group '${group_name}' (ID: ${GROUP_ID})"
done

#=========================================
# Users + passwords
#=========================================
echo "=== Creating users ==="

if ! kubectl get secret authentik-user-passwords -n iam &>/dev/null; then
  ADMIN_PASS="$(generate_password)"
  DEV_PASS="$(generate_password)"
  VIEW_PASS="$(generate_password)"
  GUEST_PASS="$(generate_password)"
  kubectl create secret generic authentik-user-passwords -n iam \
    --from-literal=admin="${ADMIN_PASS}" \
    --from-literal=dev="${DEV_PASS}" \
    --from-literal=view="${VIEW_PASS}" \
    --from-literal=guest="${GUEST_PASS}"
  echo "User passwords secret created (authentik-user-passwords in iam)"
else
  ADMIN_PASS="$(kubectl get secret authentik-user-passwords -n iam \
    -o jsonpath='{.data.admin}' | base64 -d)"
  DEV_PASS="$(kubectl get secret authentik-user-passwords -n iam \
    -o jsonpath='{.data.dev}' | base64 -d)"
  VIEW_PASS="$(kubectl get secret authentik-user-passwords -n iam \
    -o jsonpath='{.data.view}' | base64 -d)"
  GUEST_PASS="$(kubectl get secret authentik-user-passwords -n iam \
    -o jsonpath='{.data.guest}' | base64 -d)"
  echo "User passwords loaded from existing secret"
fi

# Create users with group assignments
for row in \
  "admin:${ADMIN_PASS}:${GROUP_IDS[cluster-admin]}:admin@local.narwhal.internal" \
  "dev:${DEV_PASS}:${GROUP_IDS[developer]}:dev@local.narwhal.internal" \
  "view:${VIEW_PASS}:${GROUP_IDS[viewer]}:view@local.narwhal.internal" \
  "guest:${GUEST_PASS}:${GROUP_IDS[guest]}:guest@local.narwhal.internal"; do

  IFS=':' read -r username password group_pk email <<< "${row}"

  USER_ID=$(ak_get_or_create "core/users" "username" "${username}" \
    "{\"username\":\"${username}\",\"name\":\"${username}\",\"email\":\"${email}\",\"type\":\"internal\",\"is_active\":true,\"groups\":[\"${group_pk}\"]}")

  # Set password (separate endpoint)
  curl -sf -X POST "${AUTHENTIK_URL}/api/v3/core/users/${USER_ID}/set_password/" \
    -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"${password}\"}" &>/dev/null || true

  echo "  -> user '${username}' (ID: ${USER_ID})"
done

#=========================================
# Custom scope mapping: groups claim
# Authentik built-in profile scope에 groups 클레임 없음 → 별도 생성
# ak_groups: User의 그룹 queryset (direct membership only)
#=========================================
echo "=== Creating groups scope mapping ==="

GROUPS_SCOPE_ID=$(ak_get_or_create "propertymappings/provider/scope" "scope_name" "groups" \
  "{\"name\":\"groups\",\"scope_name\":\"groups\",\"description\":\"User group membership list\",\"expression\":\"return [group.name for group in request.user.ak_groups.all()]\"}")
echo "  -> groups scope mapping (ID: ${GROUPS_SCOPE_ID})"

#=========================================
# Get built-in scope mapping PKs
#=========================================
echo "=== Gathering built-in scope mappings ==="

SCOPE_OPENID=$(ak_get "propertymappings/provider/scope/?scope_name=openid" | jq -r '.results[0].pk')
SCOPE_PROFILE=$(ak_get "propertymappings/provider/scope/?scope_name=profile" | jq -r '.results[0].pk')
SCOPE_EMAIL=$(ak_get "propertymappings/provider/scope/?scope_name=email" | jq -r '.results[0].pk')
echo "  -> openid: ${SCOPE_OPENID}, profile: ${SCOPE_PROFILE}, email: ${SCOPE_EMAIL}"

# Get authorization/invalidation flow PKs
AUTH_FLOW_PK=$(ak_get "flows/instances/?slug=default-provider-authorization-implicit-consent" \
  | jq -r '.results[0].pk')
INVALIDATION_FLOW_PK=$(ak_get "flows/instances/?slug=default-provider-invalidation-flow" \
  | jq -r '.results[0].pk')

#=========================================
# OAuth2 Provider: kubernetes (public)
# K8s API server: --oidc-client-id=kubernetes
# issuer: https://authentik.${DOMAIN}/application/o/kubernetes/
#
# redirect_uris: regex/.*  (kubectl-oidc-login이 localhost:PORT/callback 사용)
#=========================================
echo "=== Creating 'kubernetes' OAuth2 provider (public) ==="

PROVIDER_KUBERNETES_ID=$(ak_get_or_create "providers/oauth2" "name" "kubernetes" \
  "{
    \"name\": \"kubernetes\",
    \"client_type\": \"public\",
    \"client_id\": \"kubernetes\",
    \"authorization_flow\": \"${AUTH_FLOW_PK}\",
    \"invalidation_flow\": \"${INVALIDATION_FLOW_PK}\",
    \"include_claims_in_id_token\": true,
    \"sub_mode\": \"user_username\",
    \"access_code_validity\": \"minutes=1\",
    \"access_token_validity\": \"minutes=5\",
    \"refresh_token_validity\": \"days=30\",
    \"property_mappings\": [\"${SCOPE_OPENID}\",\"${SCOPE_PROFILE}\",\"${SCOPE_EMAIL}\",\"${GROUPS_SCOPE_ID}\"],
    \"redirect_uris\": [{\"matching_mode\": \"regex\", \"url\": \".*\"}]
  }")
echo "  -> kubernetes provider (ID: ${PROVIDER_KUBERNETES_ID})"

#=========================================
# OAuth2 Provider: apisix (confidential)
# APISIX openid-connect plugin: 모든 보호 라우트
# issuer: https://authentik.${DOMAIN}/application/o/apisix/
#
# sub_mode: user_username → preferred_username = Authentik username (OIDC 표준)
#=========================================
echo "=== Creating 'apisix' OAuth2 provider (confidential) ==="

# Generate or reuse apisix client secret
if ! kubectl get secret apisix-oidc-config -n platform-system &>/dev/null; then
  APISIX_CLIENT_SECRET=$(generate_password)
else
  APISIX_CLIENT_SECRET=$(kubectl get secret apisix-oidc-config -n platform-system \
    -o jsonpath='{.data.client_secret}' | base64 -d 2>/dev/null || generate_password)
fi

PROVIDER_APISIX_ID=$(ak_get_or_create "providers/oauth2" "name" "apisix" \
  "{
    \"name\": \"apisix\",
    \"client_type\": \"confidential\",
    \"client_id\": \"apisix\",
    \"client_secret\": \"${APISIX_CLIENT_SECRET}\",
    \"authorization_flow\": \"${AUTH_FLOW_PK}\",
    \"invalidation_flow\": \"${INVALIDATION_FLOW_PK}\",
    \"include_claims_in_id_token\": true,
    \"sub_mode\": \"user_username\",
    \"property_mappings\": [\"${SCOPE_OPENID}\",\"${SCOPE_PROFILE}\",\"${SCOPE_EMAIL}\",\"${GROUPS_SCOPE_ID}\"],
    \"redirect_uris\": [{\"matching_mode\": \"regex\", \"url\": \"https://.*\\\\.local\\\\.narwhal\\\\.io/apisix/callback\"}]
  }")
echo "  -> apisix provider (ID: ${PROVIDER_APISIX_ID})"

# Retrieve final client_secret (existing provider reuse 시 올바른 값)
APISIX_CLIENT_SECRET_FINAL=$(ak_get "providers/oauth2/?name=apisix" \
  | jq -r '.results[0].client_secret')

#=========================================
# Applications (slug → OIDC issuer URL 결정)
#=========================================
echo "=== Creating applications ==="

ak_get_or_create "core/applications" "slug" "kubernetes" \
  "{\"name\":\"kubernetes\",\"slug\":\"kubernetes\",\"provider\":${PROVIDER_KUBERNETES_ID},\"meta_description\":\"Kubernetes API Server OIDC\"}" > /dev/null
echo "  -> kubernetes application (issuer: https://authentik.${DOMAIN}/application/o/kubernetes/)"

ak_get_or_create "core/applications" "slug" "apisix" \
  "{\"name\":\"apisix\",\"slug\":\"apisix\",\"provider\":${PROVIDER_APISIX_ID},\"meta_description\":\"APISIX API Gateway SSO\"}" > /dev/null
echo "  -> apisix application (issuer: https://authentik.${DOMAIN}/application/o/apisix/)"

#=========================================
# K8s Secrets 저장
#=========================================
echo "=== Storing OIDC credentials in K8s secrets ==="

APISIX_SESSION_SECRET=$(openssl rand -hex 32)

kubectl create namespace platform-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic apisix-oidc-config \
  --namespace platform-system \
  --from-literal=client_id=apisix \
  --from-literal=client_secret="${APISIX_CLIENT_SECRET_FINAL}" \
  --from-literal=session_secret="${APISIX_SESSION_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "APISIX OIDC config stored (apisix-oidc-config in platform-system)"

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic grafana-oauth-secret -n monitoring \
  --from-literal=client_secret="${APISIX_CLIENT_SECRET_FINAL}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Grafana OAuth secret updated (grafana-oauth-secret in monitoring)"

kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic headlamp-oidc-secret -n devtools \
  --from-literal=clientID=apisix \
  --from-literal=clientSecret="${APISIX_CLIENT_SECRET_FINAL}" \
  --from-literal=issuerURL="https://authentik.${DOMAIN}/application/o/apisix/" \
  --from-literal=scopes=openid,profile,email,groups \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Headlamp OIDC secret updated (headlamp-oidc-secret in devtools)"

kubectl create namespace storage --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic velero-ui-oauth -n storage \
  --from-literal=client_secret="${APISIX_CLIENT_SECRET_FINAL}" \
  --from-literal=passphrase="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Velero UI OAuth secret created (velero-ui-oauth in storage)"

echo ""
echo "=== [11-2-authentik-config.sh] 완료 ==="
echo ""
echo "=========================================="
echo "Authentik Configuration Summary"
echo "=========================================="
echo "Admin UI:    https://authentik.${DOMAIN}"
echo "Admin email: admin@local.narwhal.internal"
echo "Admin pass:  kubectl get secret authentik-bootstrap-secret -n iam -o jsonpath='{.data.bootstrap_password}' | base64 -d"
echo ""
echo "OIDC Issuers:"
echo "  K8s API:  https://authentik.${DOMAIN}/application/o/kubernetes/"
echo "  APISIX:   https://authentik.${DOMAIN}/application/o/apisix/"
echo ""
echo "K8s Secrets updated:"
echo "  platform-system/apisix-oidc-config"
echo "  monitoring/grafana-oauth-secret"
echo "  devtools/headlamp-oidc-secret"
echo "  iam/authentik-user-passwords"
