#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 11-3-keycloak-clients.sh
# Phase: Per-service Keycloak OIDC client 생성 + Native SSO 설정
#
# Group A - Native SSO (서비스 자체 OIDC/OAuth 설정):
#   - ArgoCD   → argocd-cm ConfigMap patch
#   - Grafana  → deployment env var injection (generic_oauth)
#   - Gitea    → gitea admin auth add-oauth
#   - Harbor   → Harbor API OIDC config
#   - Headlamp → headlamp-oidc-secret + restart
#   - OpenBao  → bao OIDC auth method
#
# Group B - APISIX openid-connect (native SSO 미지원 서비스):
#   - Hubble UI   → hubble-oidc-secret (platform-system)
#   - Prometheus  → prometheus-oidc-secret (platform-system, Alertmanager 공유)
#   - Velero UI   → velero-ui-oidc-secret (platform-system)
#
# Depends on: 11-2-keycloak-config.sh (realm/groups/scope/users 존재)

DOMAIN="${DOMAIN:-local.narwhal.io}"
REALM="narwhal"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=========================================="
echo "11-3: Keycloak Per-Service SSO Setup"
echo "=========================================="

#=========================================
# kcadm.sh helper
#=========================================
kc_exec() {
  kubectl exec -n iam keycloak-0 -c keycloak -- \
    /opt/keycloak/bin/kcadm.sh "$@"
}

#=========================================
# kcadm.sh 로그인
#=========================================
echo "=== Logging in to Keycloak admin CLI ==="

KC_ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n iam \
  -o jsonpath='{.data.password}' | base64 -d)
KC_ADMIN_USER=$(kubectl get secret keycloak-initial-admin -n iam \
  -o jsonpath='{.data.username}' | base64 -d)

LOGIN_OK=false
for attempt in $(seq 1 18); do
  if kc_exec config credentials \
    --server http://localhost:8080 \
    --realm master \
    --user "${KC_ADMIN_USER}" \
    --password "${KC_ADMIN_PASS}" 2>/dev/null; then
    LOGIN_OK=true
    echo "Keycloak admin login successful"
    break
  fi
  echo "  Login attempt ${attempt}/18 failed, waiting 10s..."
  sleep 10
done

if [ "${LOGIN_OK}" = "false" ]; then
  echo "ERROR: Cannot log in to Keycloak"
  exit 1
fi

#=========================================
# groups scope ID 조회 (11-2에서 생성됨)
#=========================================
GROUPS_SCOPE_ID=$(kc_exec get client-scopes -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="groups") | .id')

if [ -z "${GROUPS_SCOPE_ID}" ]; then
  echo "ERROR: groups scope not found in realm '${REALM}'. Run 11-2-keycloak-config.sh first."
  exit 1
fi
echo "groups scope ID: ${GROUPS_SCOPE_ID}"

ISSUER_URL="https://keycloak.${DOMAIN}/realms/${REALM}"
DISCOVERY_URL="${ISSUER_URL}/.well-known/openid-configuration"

#=========================================
# Helper: Confidential Keycloak client 생성
# 출력: client_secret (stdout 마지막 줄)
# 재실행 안전 (client 존재 시 secret 재사용)
#=========================================
create_keycloak_client() {
  local client_id="$1"
  local redirect_uris_json="$2"  # JSON array string e.g. '["https://..."]'

  echo "--- Creating Keycloak client: ${client_id} ---" >&2

  local existing_id
  existing_id=$(kc_exec get clients -r "${REALM}" -q "clientId=${client_id}" 2>/dev/null \
    | jq -r ".[] | select(.clientId==\"${client_id}\") | .id" || echo "")

  if [ -z "${existing_id}" ]; then
    kc_exec create clients -r "${REALM}" \
      -s "clientId=${client_id}" \
      -s "name=${client_id}" \
      -s "enabled=true" \
      -s "publicClient=false" \
      -s "standardFlowEnabled=true" \
      -s "directAccessGrantsEnabled=false" \
      -s "protocol=openid-connect" \
      -s "redirectUris=${redirect_uris_json}" \
      -s 'webOrigins=["*"]' >&2
    existing_id=$(kc_exec get clients -r "${REALM}" -q "clientId=${client_id}" 2>/dev/null \
      | jq -r ".[] | select(.clientId==\"${client_id}\") | .id")
    echo "  -> client '${client_id}' created (ID: ${existing_id})" >&2
  else
    echo "  -> client '${client_id}' already exists (ID: ${existing_id})" >&2
  fi

  # groups scope 할당
  kc_exec update "clients/${existing_id}/default-client-scopes/${GROUPS_SCOPE_ID}" \
    -r "${REALM}" 2>/dev/null || true

  # Audience mapper (aud claim에 client_id 포함 — 필수)
  AUD_MAPPER_ID=$(kc_exec get "clients/${existing_id}/protocol-mappers/models" \
    -r "${REALM}" 2>/dev/null \
    | jq -r ".[] | select(.name==\"${client_id}-audience\") | .id" || echo "")
  if [ -z "${AUD_MAPPER_ID}" ]; then
    kc_exec create "clients/${existing_id}/protocol-mappers/models" -r "${REALM}" \
      -s "name=${client_id}-audience" \
      -s "protocol=openid-connect" \
      -s "protocolMapper=oidc-audience-mapper" \
      -s "config={\"included.client.audience\":\"${client_id}\",\"id.token.claim\":\"true\",\"access.token.claim\":\"true\"}" >&2
    echo "  -> audience mapper created for '${client_id}'" >&2
  fi

  # Groups mapper (client-level)
  GRP_MAPPER_ID=$(kc_exec get "clients/${existing_id}/protocol-mappers/models" \
    -r "${REALM}" 2>/dev/null \
    | jq -r ".[] | select(.name==\"${client_id}-groups\") | .id" || echo "")
  if [ -z "${GRP_MAPPER_ID}" ]; then
    kc_exec create "clients/${existing_id}/protocol-mappers/models" -r "${REALM}" \
      -s "name=${client_id}-groups" \
      -s "protocol=openid-connect" \
      -s "protocolMapper=oidc-group-membership-mapper" \
      -s 'config={"full.path":"false","id.token.claim":"true","access.token.claim":"true","claim.name":"groups","userinfo.token.claim":"true"}' >&2
    echo "  -> groups mapper created for '${client_id}'" >&2
  fi

  # Client secret 조회 (stdout으로만 출력 — 호출자가 캡처)
  local client_secret
  client_secret=$(kc_exec get "clients/${existing_id}/client-secret" -r "${REALM}" 2>/dev/null \
    | jq -r '.value // empty' || echo "")
  echo "${client_secret}"
}

# =============================================================================
# Group A: Native SSO Services
# =============================================================================
echo ""
echo "=========================================="
echo "Group A: Native SSO Providers"
echo "=========================================="

# -------------------------------------------------------------------------
# 1. ArgoCD — argocd-cm ConfigMap + argocd-secret
# -------------------------------------------------------------------------
echo ""
echo "=== [1/6] ArgoCD ==="

ARGOCD_SECRET=$(create_keycloak_client "argocd" \
  "[\"https://argocd.${DOMAIN}/auth/callback\"]")

kubectl create secret generic argocd-oidc-secret \
  --namespace devtools \
  --from-literal=client-id=argocd \
  --from-literal=client-secret="${ARGOCD_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

# argocd-cm patch (Keycloak OIDC)
if kubectl get configmap argocd-cm -n devtools &>/dev/null; then
  kubectl patch configmap argocd-cm -n devtools --type merge -p "{
    \"data\": {
      \"url\": \"https://argocd.${DOMAIN}\",
      \"oidc.config\": \"name: Keycloak\nissuer: ${ISSUER_URL}\nclientID: argocd\nclientSecret: \$oidc.keycloak.clientSecret\nrequestedScopes:\n  - openid\n  - profile\n  - email\n  - groups\ninsecureSkipVerify: true\n\"
    }
  }" 2>/dev/null || echo "WARN: argocd-cm patch failed"

  # argocd-secret에 clientSecret 키 저장 (argocd-cm의 $oidc.keycloak.clientSecret 참조)
  ARGOCD_SECRET_CURRENT=$(kubectl get secret argocd-secret -n devtools \
    -o jsonpath='{.data}' 2>/dev/null | jq -r '."oidc.keycloak.clientSecret" // empty' | base64 -d || echo "")
  if [ "${ARGOCD_SECRET_CURRENT}" != "${ARGOCD_SECRET}" ]; then
    kubectl patch secret argocd-secret -n devtools \
      --type='json' \
      -p="[{\"op\":\"add\",\"path\":\"/data/oidc.keycloak.clientSecret\",\"value\":\"$(echo -n "${ARGOCD_SECRET}" | base64)\"}]" \
      2>/dev/null || kubectl create secret generic argocd-secret \
        --namespace devtools \
        --from-literal=oidc.keycloak.clientSecret="${ARGOCD_SECRET}" \
        --dry-run=client -o yaml | kubectl apply -f -
    echo "  argocd-secret updated with OIDC client secret"
  fi

  # ArgoCD server restart to apply config
  kubectl rollout restart deployment argocd-server -n devtools 2>/dev/null || true
  echo "ArgoCD OIDC configured"
else
  echo "WARN: argocd-cm not found, skipping ArgoCD OIDC config"
fi

# -------------------------------------------------------------------------
# 2. Grafana — deployment env var injection
# -------------------------------------------------------------------------
echo ""
echo "=== [2/6] Grafana ==="

GRAFANA_SECRET=$(create_keycloak_client "grafana" \
  "[\"https://grafana.${DOMAIN}/login/generic_oauth\"]")

kubectl create secret generic grafana-oidc-secret \
  --namespace monitoring \
  --from-literal=client_id=grafana \
  --from-literal=client_secret="${GRAFANA_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

if kubectl get deployment prometheus-stack-grafana -n monitoring &>/dev/null; then
  kubectl patch deployment prometheus-stack-grafana -n monitoring \
    --type='json' -p="[
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_ENABLED\",\"value\":\"true\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_NAME\",\"value\":\"Keycloak\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_CLIENT_ID\",\"value\":\"grafana\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET\",\"valueFrom\":{\"secretKeyRef\":{\"name\":\"grafana-oidc-secret\",\"key\":\"client_secret\"}}}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_SCOPES\",\"value\":\"openid profile email groups\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_AUTH_URL\",\"value\":\"${ISSUER_URL}/protocol/openid-connect/auth\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_TOKEN_URL\",\"value\":\"${ISSUER_URL}/protocol/openid-connect/token\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_API_URL\",\"value\":\"${ISSUER_URL}/protocol/openid-connect/userinfo\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_TLS_SKIP_VERIFY_INSECURE\",\"value\":\"true\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_GROUPS_ATTRIBUTE_PATH\",\"value\":\"groups\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH\",\"value\":\"contains(groups[*], 'cluster-admin') && 'Admin' || 'Viewer'\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_AUTH_GENERIC_OAUTH_ALLOW_ASSIGN_GRAFANA_ADMIN\",\"value\":\"true\"}},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env/-\",\"value\":{\"name\":\"GF_SERVER_ROOT_URL\",\"value\":\"https://grafana.${DOMAIN}\"}}
  ]" 2>/dev/null || echo "WARN: Grafana env patch failed (may already be patched)"
  echo "Grafana native SSO configured"
else
  echo "WARN: Grafana deployment not found, skipping"
fi

# -------------------------------------------------------------------------
# 3. Gitea — gitea admin auth add-oauth
# -------------------------------------------------------------------------
echo ""
echo "=== [3/6] Gitea ==="

GITEA_SECRET=$(create_keycloak_client "gitea" \
  "[\"https://gitea.${DOMAIN}/user/oauth2/keycloak/callback\"]")

kubectl create secret generic gitea-oidc-secret \
  --namespace devtools \
  --from-literal=client-id=gitea \
  --from-literal=client-secret="${GITEA_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "${GITEA_POD}" ]; then
  # 기존 Keycloak source 삭제 (재실행 안전)
  kubectl exec -n devtools "${GITEA_POD}" -- \
    /app/gitea/gitea admin auth delete --name keycloak 2>/dev/null || true

  # add-oauth (lowercase name → URL path에서 사용)
  kubectl exec -n devtools "${GITEA_POD}" -- \
    /app/gitea/gitea admin auth add-oauth \
      --name keycloak \
      --provider openidConnect \
      --key gitea \
      --secret "${GITEA_SECRET}" \
      --auto-discover-url "${DISCOVERY_URL}" \
      --skip-local-2fa \
      --scopes "openid profile email groups" \
      --group-claim-name groups \
      --admin-group cluster-admin \
      2>/dev/null && echo "Gitea OAuth source 'keycloak' created" \
      || echo "WARN: Gitea OAuth config failed"
else
  echo "WARN: Gitea pod not found, skipping"
fi

# -------------------------------------------------------------------------
# 4. Harbor — Harbor API OIDC config
# -------------------------------------------------------------------------
echo ""
echo "=== [4/6] Harbor ==="

HARBOR_SECRET=$(create_keycloak_client "harbor" \
  "[\"https://harbor.${DOMAIN}/c/oidc/callback\"]")

kubectl create secret generic harbor-oidc-secret \
  --namespace devtools \
  --from-literal=client-id=harbor \
  --from-literal=client-secret="${HARBOR_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

HARBOR_ADMIN_PASS=$(kubectl get secret harbor-secrets -n devtools \
  -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' 2>/dev/null | base64 -d || echo "")

if [ -n "${HARBOR_ADMIN_PASS}" ]; then
  HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools \
    -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")

  if [ -n "${HARBOR_CORE_IP}" ]; then
    HARBOR_API="http://${HARBOR_CORE_IP}/api/v2.0"
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
          \"oidc_name\": \"Keycloak\",
          \"oidc_endpoint\": \"${ISSUER_URL}\",
          \"oidc_client_id\": \"harbor\",
          \"oidc_client_secret\": \"${HARBOR_SECRET}\",
          \"oidc_scope\": \"openid,profile,email,groups\",
          \"oidc_admin_group\": \"cluster-admin\",
          \"oidc_verify_cert\": false,
          \"oidc_auto_onboard\": true,
          \"oidc_user_claim\": \"preferred_username\"
        }" 2>/dev/null && echo "Harbor OIDC configured" \
        || echo "WARN: Harbor OIDC API call failed"
    else
      echo "WARN: Harbor API not available, skipping OIDC config"
    fi
  else
    echo "WARN: Harbor core service not found"
  fi
else
  echo "WARN: Harbor admin password not found, skipping"
fi

# -------------------------------------------------------------------------
# 5. Headlamp — headlamp-oidc-secret + restart
# -------------------------------------------------------------------------
echo ""
echo "=== [5/6] Headlamp ==="

HEADLAMP_SECRET=$(create_keycloak_client "headlamp" \
  "[\"https://headlamp.${DOMAIN}/oidc-callback\"]")

# Headlamp은 issuerURL/scopes 키도 필요
kubectl create secret generic headlamp-oidc-secret \
  --namespace devtools \
  --from-literal=clientID=headlamp \
  --from-literal=clientSecret="${HEADLAMP_SECRET}" \
  --from-literal=issuerURL="${ISSUER_URL}" \
  --from-literal=scopes="openid,profile,email,groups" \
  --dry-run=client -o yaml | kubectl apply -f -

if kubectl get deployment headlamp -n devtools &>/dev/null; then
  kubectl rollout restart deployment headlamp -n devtools 2>/dev/null || true
  echo "Headlamp restarted with Keycloak OIDC"
else
  echo "WARN: Headlamp deployment not found, skipping"
fi

# -------------------------------------------------------------------------
# 6. OpenBao — bao OIDC auth method
# -------------------------------------------------------------------------
echo ""
echo "=== [6/6] OpenBao ==="

OPENBAO_SECRET=$(create_keycloak_client "openbao" \
  "[\"https://openbao.${DOMAIN}/ui/vault/auth/oidc/oidc/callback\",\"http://localhost:8250/oidc/callback\"]")

kubectl create secret generic openbao-oidc-secret \
  --namespace storage \
  --from-literal=client-id=openbao \
  --from-literal=client-secret="${OPENBAO_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

OPENBAO_POD=$(kubectl get pod -n storage -l app.kubernetes.io/name=openbao \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "${OPENBAO_POD}" ]; then
  OPENBAO_ROOT_TOKEN=$(kubectl get secret openbao-init -n storage \
    -o jsonpath='{.data.root_token}' 2>/dev/null | base64 -d || echo "")

  if [ -n "${OPENBAO_ROOT_TOKEN}" ]; then
    kubectl exec -n storage "${OPENBAO_POD}" -- \
      env VAULT_TOKEN="${OPENBAO_ROOT_TOKEN}" VAULT_ADDR="http://127.0.0.1:8200" \
      /bin/sh -c "
        bao auth enable oidc 2>/dev/null || true
        bao write auth/oidc/config \
          oidc_discovery_url='${ISSUER_URL}' \
          oidc_client_id='openbao' \
          oidc_client_secret='${OPENBAO_SECRET}' \
          oidc_discovery_ca_pem='' \
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
    echo "WARN: OpenBao root token not found, skipping"
  fi
else
  echo "WARN: OpenBao pod not found, skipping"
fi

# =============================================================================
# Group B: APISIX openid-connect services
# 각 서비스마다 dedicated Keycloak client + K8s Secret in platform-system
# apisix-routes.yaml의 $secret://kubernetes/k8s-1/<name>/<key> 참조
# =============================================================================
echo ""
echo "=========================================="
echo "Group B: APISIX openid-connect SSO"
echo "=========================================="

#=========================================
# Helper: APISIX SSO secret 생성
# client_secret + session_secret (32-byte hex)
#=========================================
create_apisix_secret() {
  local client_id="$1"
  local redirect_uri="$2"
  local secret_name="$3"

  local client_secret
  client_secret=$(create_keycloak_client "${client_id}" \
    "[\"${redirect_uri}\"]")

  local session_secret
  # 기존 secret에서 session_secret 재사용 (재실행 안전)
  session_secret=$(kubectl get secret "${secret_name}" -n platform-system \
    -o jsonpath='{.data.session_secret}' 2>/dev/null | base64 -d || echo "")
  if [ -z "${session_secret}" ]; then
    session_secret=$(openssl rand -hex 16)
  fi

  kubectl create secret generic "${secret_name}" \
    --namespace platform-system \
    --from-literal=client_id="${client_id}" \
    --from-literal=client_secret="${client_secret}" \
    --from-literal=session_secret="${session_secret}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "  secret '${secret_name}' created in platform-system"
}

# -------------------------------------------------------------------------
# 7. Hubble UI
# -------------------------------------------------------------------------
echo ""
echo "=== [7/9] Hubble UI ==="
create_apisix_secret "hubble" \
  "https://hubble.${DOMAIN}/apisix/callback" \
  "hubble-oidc-secret"

# -------------------------------------------------------------------------
# 8. Prometheus + Alertmanager (shared client)
# -------------------------------------------------------------------------
echo ""
echo "=== [8/9] Prometheus + Alertmanager (shared) ==="

PROM_SECRET=$(create_keycloak_client "prometheus" \
  "[\"https://prometheus.${DOMAIN}/apisix/callback\",\"https://alertmanager.${DOMAIN}/apisix/callback\"]")

PROM_SESSION=$(kubectl get secret prometheus-oidc-secret -n platform-system \
  -o jsonpath='{.data.session_secret}' 2>/dev/null | base64 -d || echo "")
if [ -z "${PROM_SESSION}" ]; then
  PROM_SESSION=$(openssl rand -hex 16)
fi

kubectl create secret generic prometheus-oidc-secret \
  --namespace platform-system \
  --from-literal=client_id=prometheus \
  --from-literal=client_secret="${PROM_SECRET}" \
  --from-literal=session_secret="${PROM_SESSION}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "  secret 'prometheus-oidc-secret' created in platform-system"

# -------------------------------------------------------------------------
# 9. Velero UI
# -------------------------------------------------------------------------
echo ""
echo "=== [9/9] Velero UI ==="
create_apisix_secret "velero-ui" \
  "https://velero-ui.${DOMAIN}/apisix/callback" \
  "velero-ui-oidc-secret"

# =============================================================================
# APISIX Secret Provider 설정 확인
# apisix-routes.yaml에서 $secret://kubernetes/k8s-1/<name>/<key> 참조
# APISIX가 K8s secrets을 읽으려면 SecretProviderClass 또는 apisix-secret 설정 필요
# =============================================================================
echo ""
echo "=== Verifying APISIX Secret Provider ==="

# APISIX k8s-1 secret provider 확인 (08-1-networking.sh에서 설정됨)
if kubectl get secret apisix-oidc-secrets-provider -n platform-system &>/dev/null 2>&1 || \
   kubectl exec -n platform-system deploy/apisix -- curl -s http://127.0.0.1:9180/apisix/admin/secrets/kubernetes/k8s-1 \
     -H 'X-API-KEY: edd1c9f034335f136f87ad84b625c8f1' &>/dev/null 2>&1; then
  echo "  APISIX k8s-1 secret provider configured"
else
  echo "  Configuring APISIX k8s-1 secret provider (kubernetes namespace=platform-system)..."
  kubectl exec -n platform-system deploy/apisix -- curl -sf -X PUT \
    http://127.0.0.1:9180/apisix/admin/secrets/kubernetes/k8s-1 \
    -H 'X-API-KEY: edd1c9f034335f136f87ad84b625c8f1' \
    -H 'Content-Type: application/json' \
    -d "{\"namespace\":\"platform-system\",\"service_account_token\":\"\"}" \
    2>/dev/null && echo "  APISIX k8s-1 secret provider created" \
    || echo "  WARN: APISIX secret provider config failed (may be pre-configured)"
fi

# =============================================================================
# 요약
# =============================================================================
echo ""
echo "=========================================="
echo "[11-3-keycloak-clients.sh] Complete"
echo "=========================================="
echo ""
echo "Group A - Native SSO clients:"
echo "  [OK] ArgoCD   (argocd)    → argocd-cm OIDC + argocd-secret"
echo "  [OK] Grafana  (grafana)   → GF_AUTH_GENERIC_OAUTH_* env injection"
echo "  [OK] Gitea    (gitea)     → gitea admin auth add-oauth (source: keycloak)"
echo "  [OK] Harbor   (harbor)    → Harbor API OIDC config"
echo "  [OK] Headlamp (headlamp)  → headlamp-oidc-secret + restart"
echo "  [OK] OpenBao  (openbao)   → bao OIDC auth method"
echo ""
echo "Group B - APISIX openid-connect secrets (platform-system):"
echo "  [OK] hubble-oidc-secret        (client: hubble)"
echo "  [OK] prometheus-oidc-secret    (client: prometheus, shared with Alertmanager)"
echo "  [OK] velero-ui-oidc-secret     (client: velero-ui)"
echo ""
echo "Keycloak OIDC discovery: ${DISCOVERY_URL}"
echo ""
echo "WARNING: Grafana/ArgoCD env patches may be reverted by ArgoCD selfHeal."
echo "  Push changes to Gitea for persistence."
echo ""
echo "=== [11-3-keycloak-clients.sh] 완료 ==="
