#!/bin/bash
# 13-2-narwhal-portal-bindings.sh
# narwhal-portal-secrets 생성 (클린 설치 자동화)
#
# 실행 시점: 06-phase2-start.sh 에서 13-argocd.sh 직후, 14-gitops-bootstrap.sh 전.
# 멱등성: 이미 존재하는 Secret은 --dry-run=client | kubectl apply 로 업데이트.
# 의존 스크립트:
#   11-2-keycloak-config.sh  — narwhal realm / groups scope 존재
#   11-3-keycloak-clients.sh — (선택) 다른 클라이언트들 존재. 이 스크립트는
#                              narwhal-portal / narwhal-portal-admin 클라이언트를
#                              자체 생성하므로 11-3 에 의존하지 않는다.
#   13-argocd.sh             — argocd-initial-admin-secret, accounts.narwhal-portal 존재
#   08-4-storage.sh          — openbao-init Secret 존재
#
# 생성 결과:
#   Secret narwhal-portal-secrets  (namespace: devtools)
#     -- Auth/OIDC, Keycloak admin, K8s SA, ArgoCD, APISIX, observability,
#        Valkey, OpenBao, misc 키 전체 포함
#   OpenBao: secret/ KV-v2 mount + narwhal-portal 정책 + 포털 전용 토큰
#   Keycloak: narwhal-portal + narwhal-portal-admin 클라이언트 (재실행 안전)
set -euo pipefail

DOMAIN="${DOMAIN:-local.narwhal.internal}"
REALM="narwhal"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=========================================="
echo "13-2: narwhal-portal-secrets 생성"
echo "=========================================="

# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
kc_exec() {
  kubectl exec -n iam keycloak-0 -c keycloak -- \
    /opt/keycloak/bin/kcadm.sh "$@"
}

wait_for_keycloak() {
  echo "Keycloak 준비 대기..."
  local KC_ADMIN_PASS KC_ADMIN_USER
  KC_ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n iam \
    -o jsonpath='{.data.password}' | base64 -d)
  KC_ADMIN_USER=$(kubectl get secret keycloak-initial-admin -n iam \
    -o jsonpath='{.data.username}' | base64 -d)
  for attempt in $(seq 1 24); do
    if kc_exec config credentials \
      --server http://localhost:8080 \
      --realm master \
      --user "${KC_ADMIN_USER}" \
      --password "${KC_ADMIN_PASS}" 2>/dev/null; then
      echo "  Keycloak 로그인 성공"
      return 0
    fi
    echo "  시도 ${attempt}/24 실패, 10s 대기..."
    sleep 10
  done
  echo "ERROR: Keycloak 로그인 실패"
  return 1
}

# ──────────────────────────────────────────────
# Keycloak 클라이언트 생성 헬퍼
# 출력(stdout 마지막 줄): client_secret
# ──────────────────────────────────────────────
ensure_keycloak_client() {
  local client_id="$1"
  local redirect_uris_json="$2"     # JSON array 문자열, e.g. '["https://..."]'
  local service_accounts="${3:-false}"

  local existing_id
  existing_id=$(kc_exec get clients -r "${REALM}" \
    -q "clientId=${client_id}" 2>/dev/null \
    | python3 -c "
import sys, json
clients = json.load(sys.stdin)
for c in clients:
    if c.get('clientId') == '${client_id}':
        print(c['id'])
        break
" || echo "")

  if [ -z "${existing_id}" ]; then
    kc_exec create clients -r "${REALM}" \
      -s "clientId=${client_id}" \
      -s "name=${client_id}" \
      -s "enabled=true" \
      -s "publicClient=false" \
      -s "standardFlowEnabled=$([ "${service_accounts}" = "true" ] && echo false || echo true)" \
      -s "directAccessGrantsEnabled=false" \
      -s "serviceAccountsEnabled=${service_accounts}" \
      -s "protocol=openid-connect" \
      -s "redirectUris=${redirect_uris_json}" \
      -s 'webOrigins=["*"]' >&2

    existing_id=$(kc_exec get clients -r "${REALM}" \
      -q "clientId=${client_id}" 2>/dev/null \
      | python3 -c "
import sys, json
clients = json.load(sys.stdin)
for c in clients:
    if c.get('clientId') == '${client_id}':
        print(c['id'])
        break
")
    echo "  -> '${client_id}' 생성 완료 (ID: ${existing_id})" >&2
  else
    echo "  -> '${client_id}' 이미 존재 (ID: ${existing_id})" >&2
  fi

  # groups scope 할당 (OIDC 로그인 클라이언트에만)
  if [ "${service_accounts}" = "false" ]; then
    local groups_scope_id
    groups_scope_id=$(kc_exec get client-scopes -r "${REALM}" 2>/dev/null \
      | python3 -c "
import sys, json
scopes = json.load(sys.stdin)
for s in scopes:
    if s.get('name') == 'groups':
        print(s['id'])
        break
" || echo "")
    if [ -n "${groups_scope_id}" ]; then
      kc_exec update \
        "clients/${existing_id}/default-client-scopes/${groups_scope_id}" \
        -r "${REALM}" 2>/dev/null || true
    fi

    # audience mapper
    local has_aud
    has_aud=$(kc_exec get \
      "clients/${existing_id}/protocol-mappers/models" \
      -r "${REALM}" 2>/dev/null \
      | python3 -c "
import sys, json
mappers = json.load(sys.stdin)
print('yes' if any(m.get('name') == '${client_id}-audience' for m in mappers) else '')
" || echo "")
    if [ -z "${has_aud}" ]; then
      kc_exec create \
        "clients/${existing_id}/protocol-mappers/models" -r "${REALM}" \
        -s "name=${client_id}-audience" \
        -s "protocol=openid-connect" \
        -s "protocolMapper=oidc-audience-mapper" \
        -s "config={\"included.client.audience\":\"${client_id}\",\"id.token.claim\":\"true\",\"access.token.claim\":\"true\"}" >&2
      echo "  -> audience mapper 생성" >&2
    fi
  fi

  # Service Account 역할 할당 (narwhal-portal-admin 전용)
  if [ "${service_accounts}" = "true" ]; then
    local sa_user_id realm_mgmt_id
    sa_user_id=$(kc_exec get \
      "clients/${existing_id}/service-account-user" -r "${REALM}" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" || echo "")
    realm_mgmt_id=$(kc_exec get clients -r "${REALM}" \
      -q "clientId=realm-management" 2>/dev/null \
      | python3 -c "
import sys, json
clients = json.load(sys.stdin)
for c in clients:
    if c.get('clientId') == 'realm-management':
        print(c['id'])
        break
" || echo "")
    if [ -n "${sa_user_id}" ] && [ -n "${realm_mgmt_id}" ]; then
      for role in view-users query-users manage-users view-realm; do
        kc_exec add-roles -r "${REALM}" \
          --uid "${sa_user_id}" \
          --cclientid realm-management \
          --rolename "${role}" 2>/dev/null && echo "  -> SA role assigned: ${role}" >&2 || true
      done
    fi
  fi

  # client secret 반환 (stdout)
  kc_exec get "clients/${existing_id}/client-secret" \
    -r "${REALM}" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('value',''))"
}

# ──────────────────────────────────────────────
# STEP 1: Keycloak 클라이언트 확보
# ──────────────────────────────────────────────
echo ""
echo "=== [1/4] Keycloak 클라이언트 ==="
wait_for_keycloak

echo "--- narwhal-portal (OIDC 로그인) ---"
PORTAL_CLIENT_SECRET=$(ensure_keycloak_client \
  "narwhal-portal" \
  "[\"https://portal.${DOMAIN}/api/auth/callback/keycloak\"]" \
  "false")
echo "narwhal-portal client secret: 획득 완료 (${#PORTAL_CLIENT_SECRET} chars)"

# post_logout_redirect_uri 등록: 포털 로그아웃은 SSO 단일 로그아웃(SLO) 체인을
# 구동한다 (federated-logout 라우트 → Keycloak end_session → SLO_CHAIN_START =
# gitea/apisix/logout → 각 게이트웨이 앱 세션 정리 → 연쇄). Keycloak은
# post_logout_redirect_uri를 클라이언트의 post.logout.redirect.uris와 대조하므로,
# 이게 비어 있으면 로그아웃이 "Invalid redirect uri"로 막힌다(2026-07-13 발생).
# 키에 점이 있어 -s는 반드시 따옴표로 감싼다("...").
PORTAL_CID=$(kc_exec get clients -r "${REALM}" -q clientId=narwhal-portal \
  --fields id --format csv --noquotes 2>/dev/null | head -1)
kc_exec update "clients/${PORTAL_CID}" -r "${REALM}" \
  -s "attributes.\"post.logout.redirect.uris\"=https://gitea.${DOMAIN}/apisix/logout##https://portal.${DOMAIN}/login##https://portal.${DOMAIN}/*" >&2 \
  && echo "narwhal-portal post.logout.redirect.uris 등록 완료" >&2

echo "--- narwhal-portal-admin (Service Account) ---"
ADMIN_CLIENT_SECRET=$(ensure_keycloak_client \
  "narwhal-portal-admin" \
  "[]" \
  "true")
echo "narwhal-portal-admin client secret: 획득 완료 (${#ADMIN_CLIENT_SECRET} chars)"

# ──────────────────────────────────────────────
# STEP 2: OpenBao — KV mount + 정책 + 포털 토큰
# ──────────────────────────────────────────────
echo ""
echo "=== [2/4] OpenBao 설정 ==="

OPENBAO_ROOT_TOKEN=$(kubectl get secret openbao-init -n storage \
  -o jsonpath='{.data.root_token}' | base64 -d)
OPENBAO_POD=$(kubectl get pod -n storage \
  -l app.kubernetes.io/name=openbao \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "${OPENBAO_POD}" ]; then
  echo "WARN: OpenBao pod 없음 — OPENBAO_TOKEN은 placeholder로 설정됨"
  OPENBAO_PORTAL_TOKEN="REPLACE_ME__openbao_token"
else
  # KV v2 mount 활성화 (멱등)
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao secrets enable -version=2 -path=secret kv 2>/dev/null \
    || echo "  secret/ mount 이미 존재 (정상)"

  # 정책 작성
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    /bin/sh -c '
cat > /tmp/portal.hcl << '"'"'POLICY_EOF'"'"'
path "secret/data/narwhal-portal/*" {
  capabilities = ["create","read","update","delete","list"]
}
path "secret/metadata/narwhal-portal/*" {
  capabilities = ["read","list"]
}
POLICY_EOF
bao policy write narwhal-portal /tmp/portal.hcl
rm -f /tmp/portal.hcl
echo "  narwhal-portal policy written"
'

  # 포털 전용 토큰 발급 (1년, renewable)
  OPENBAO_PORTAL_TOKEN=$(kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao token create \
      -policy=narwhal-portal \
      -display-name=narwhal-portal \
      -ttl=8760h \
      -renewable=true 2>/dev/null \
    | awk '/^token /{print $2}')
  echo "  OPENBAO_TOKEN 발급 완료 (${#OPENBAO_PORTAL_TOKEN} chars)"
fi

# ──────────────────────────────────────────────
# STEP 3: 나머지 값 수집
# ──────────────────────────────────────────────
echo ""
echo "=== [3/4] 나머지 값 수집 ==="

# 자동 생성 시크릿
AUTH_SECRET=$(openssl rand -base64 32)
LIVE_INGEST_SECRET=$(openssl rand -base64 24)

# narwhal-portal SA (idempotent) — MUST exist before minting its token. The portal Helm
# chart also defines this SA, but ArgoCD deploys that AFTER this step (13), so at 13-2
# time the SA doesn't exist yet → `kubectl create token` fails ("SA 없음") → empty
# K8S_SA_TOKEN → the portal cluster-infra page can't reach the API server. Create it here;
# ArgoCD adopts the identical SA later.
kubectl create serviceaccount narwhal-portal -n devtools \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 || true

# K8s SA 토큰 (1년)
K8S_SA_TOKEN=$(kubectl create token narwhal-portal \
  -n devtools --duration=8760h 2>/dev/null || echo "")
if [ -z "${K8S_SA_TOKEN}" ]; then
  echo "WARN: narwhal-portal SA 토큰 발급 실패 (K8S_SA_TOKEN 비어 있음)"
else
  echo "  K8S_SA_TOKEN 발급 완료 (${#K8S_SA_TOKEN} chars)"
fi

# APISIX API key
APISIX_API_KEY=$(kubectl get secret apisix-admin-key \
  -n platform-system \
  -o jsonpath='{.data.key}' 2>/dev/null | base64 -d || echo "")
APISIX_API_KEY_READONLY=$(kubectl get secret apisix-admin-key \
  -n platform-system \
  -o jsonpath='{.data.viewer}' 2>/dev/null | base64 -d || echo "")
echo "  APISIX_API_KEY 획득 완료"

# ArgoCD token (accounts.narwhal-portal 이 argocd-cm에 정의돼 있어야 함)
ARGOCD_TOKEN=""
ARGOCD_ADMIN_PASS=$(kubectl get secret argocd-initial-admin-secret \
  -n devtools -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo "")
if [ -n "${ARGOCD_ADMIN_PASS}" ]; then
  # Retry with readiness gate: ArgoCD server + its APISIX ingress route may not be
  # reachable the instant 13-2 runs (right after 13-argocd). A single-shot issuance
  # previously left the REPLACE_ME placeholder in narwhal-portal-secrets → the portal
  # ArgoCD widget got 401 → "0 apps" until the token was manually re-issued. Poll until
  # the session endpoint answers and the token is issued (12 × 10s).
  for attempt in $(seq 1 12); do
    ARGOCD_JWT=$(curl -sk --max-time 10 -X POST \
      "https://argocd.${DOMAIN}/api/v1/session" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"admin\",\"password\":\"${ARGOCD_ADMIN_PASS}\"}" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
    if [ -n "${ARGOCD_JWT}" ]; then
      # 기존 토큰 삭제 후 재발급. Content-Type 은 DELETE 에도 필수다 — 없으면 ArgoCD 가
      # 415 "Invalid content type" 를 돌려주고, `|| true` 가 그것을 삼킨다. 그러면 다음
      # POST 가 "account already has token with id 'portal-main'" 로 500 이 되어 토큰이
      # 한 번 생긴 뒤에는 재발급이 영구 실패하고, secret 에 REPLACE_ME 가 남는다.
      # 토큰 값은 발급 시점에만 반환되므로 삭제 없이는 복구할 수 없다.
      del_code=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' -X DELETE \
        "https://argocd.${DOMAIN}/api/v1/account/narwhal-portal/token/portal-main" \
        -H "Authorization: Bearer ${ARGOCD_JWT}" \
        -H "Content-Type: application/json" || echo "000")
      case "${del_code}" in
        200|404) : ;;  # 삭제됨 / 애초에 없음 — 둘 다 정상
        *) echo "  WARN: 기존 토큰 삭제 실패 (HTTP ${del_code}) — 재발급이 실패할 수 있음" ;;
      esac
      ARGOCD_TOKEN=$(curl -sk --max-time 10 -X POST \
        "https://argocd.${DOMAIN}/api/v1/account/narwhal-portal/token" \
        -H "Authorization: Bearer ${ARGOCD_JWT}" \
        -H "Content-Type: application/json" \
        -d '{"expiresIn":0,"id":"portal-main"}' \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
      if [ -n "${ARGOCD_TOKEN}" ]; then
        echo "  ARGOCD_TOKEN 발급 완료 (${#ARGOCD_TOKEN} chars, attempt ${attempt})"
        break
      fi
    fi
    echo "  ArgoCD 토큰 발급 대기 (server/ingress 준비 중) attempt ${attempt}/12..."
    sleep 10
  done
fi
if [ -z "${ARGOCD_TOKEN}" ]; then
  echo "WARN: ArgoCD 토큰 발급 실패 — accounts.narwhal-portal 이 argocd-cm에 있는지 확인"
  ARGOCD_TOKEN="REPLACE_ME__argocd_token"
fi

# ──────────────────────────────────────────────
# STEP 4: narwhal-portal-secrets 생성 (멱등)
# ──────────────────────────────────────────────
echo ""
echo "=== [4/4] narwhal-portal-secrets 생성 ==="

kubectl create secret generic narwhal-portal-secrets \
  --namespace devtools \
  --from-literal=AUTH_SECRET="${AUTH_SECRET}" \
  --from-literal=AUTH_URL="https://portal.${DOMAIN}" \
  --from-literal=AUTH_TRUST_HOST="true" \
  --from-literal=AUTH_MOCK="false" \
  --from-literal=NEXT_PUBLIC_AUTH_MOCK="false" \
  --from-literal=KEYCLOAK_ISSUER="https://keycloak.${DOMAIN}/realms/${REALM}" \
  --from-literal=KEYCLOAK_CLIENT_ID="narwhal-portal" \
  --from-literal=KEYCLOAK_CLIENT_SECRET="${PORTAL_CLIENT_SECRET}" \
  --from-literal=OIDC_CLIENT_ID="narwhal-portal" \
  --from-literal=KEYCLOAK_URL="https://keycloak.${DOMAIN}" \
  --from-literal=KEYCLOAK_REALM="${REALM}" \
  --from-literal=KEYCLOAK_ADMIN_REALM="${REALM}" \
  --from-literal=KEYCLOAK_ADMIN_CLIENT_ID="narwhal-portal-admin" \
  --from-literal=KEYCLOAK_ADMIN_CLIENT_SECRET="${ADMIN_CLIENT_SECRET}" \
  --from-literal=KEYCLOAK_INTERNAL_URL="http://keycloak-service.iam.svc.cluster.local:8080" \
  --from-literal=K8S_API_SERVER="https://${VIP_ADDRESS:-192.168.56.100}:6443" \
  --from-literal=K8S_SA_TOKEN="${K8S_SA_TOKEN}" \
  --from-literal=CLUSTER_NAME="narwhal" \
  --from-literal=ARGOCD_URL="http://argocd-server.devtools.svc.cluster.local" \
  --from-literal=ARGOCD_TOKEN="${ARGOCD_TOKEN}" \
  --from-literal=ARGOCD_DEVELOPER_PROJECTS="" \
  --from-literal=APISIX_ADMIN_URL="http://apisix-admin.platform-system.svc.cluster.local:9180" \
  --from-literal=APISIX_API_KEY="${APISIX_API_KEY}" \
  --from-literal=APISIX_API_KEY_READONLY="${APISIX_API_KEY_READONLY}" \
  --from-literal=PROMETHEUS_URL="http://prometheus-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090" \
  --from-literal=ALERTMANAGER_URL="http://prometheus-stack-kube-prom-alertmanager.monitoring.svc.cluster.local:9093" \
  --from-literal=ALERT_SILENCE_MAX_HOURS="24" \
  --from-literal=LOKI_URL="http://loki.monitoring.svc.cluster.local:3100" \
  --from-literal=TEMPO_URL="http://tempo.monitoring.svc.cluster.local:3200" \
  --from-literal=VALKEY_URL="redis://narwhal-portal-valkey.devtools.svc.cluster.local:6379" \
  --from-literal=VALKEY_TLS="false" \
  --from-literal=VALKEY_INSECURE_PRODUCTION="true" \
  --from-literal=VALKEY_PASSWORD="" \
  --from-literal=OPENBAO_ADDR="https://openbao.storage.svc.cluster.local:8200" \
  --from-literal=OPENBAO_TOKEN="${OPENBAO_PORTAL_TOKEN}" \
  --from-literal=TUNING_JOB_IMAGE="harbor.${DOMAIN}/library/tuning-job:latest" \
  --from-literal=TUNING_JOB_NAMESPACE="devtools" \
  --from-literal=LIVE_INGEST_SECRET="${LIVE_INGEST_SECRET}" \
  --from-literal=LIVE_INGEST_LINK_HOSTS="" \
  --from-literal=LIVE_STREAM_DEGRADED="false" \
  --from-literal=OTEL_ENABLED="false" \
  --from-literal=OTEL_SERVICE_NAME="narwhal-portal" \
  --from-literal=NODE_EXTRA_CA_CERTS="/etc/ssl/narwhal/ca.crt" \
  --from-literal=SCORECARD_CONFIGMAP_NAME="narwhal-scorecard-rules" \
  --from-literal=SCORECARD_CONFIGMAP_NAMESPACE="devtools" \
  --from-literal=SERVICE_GRAPH_SOURCE="istio" \
  --from-literal=COST_CPU_HOURLY="0.04" \
  --from-literal=COST_MEM_GB_HOURLY="0.005" \
  --from-literal=COST_STORAGE_GB_HOURLY="0.0001" \
  --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "narwhal-portal-secrets 생성 완료"
echo ""
echo "=========================================="
echo "[13-2] 완료"
echo "=========================================="
echo ""
echo "생성/갱신 내역:"
echo "  Keycloak client:  narwhal-portal (OIDC 로그인)"
echo "  Keycloak client:  narwhal-portal-admin (Service Account)"
echo "  OpenBao:          policy=narwhal-portal, token 발급"
echo "  K8s SA token:     narwhal-portal@devtools (8760h)"
echo "  ArgoCD token:     account=narwhal-portal"
echo "  Secret:           narwhal-portal-secrets (devtools)"
echo ""
echo "NOTE: Valkey는 현재 noauth 모드 (--save \"\" --appendonly no)."
echo "  VALKEY_PASSWORD는 빈 값으로 설정됨."
echo "  프로덕션에서는 TLS+AUTH 활성화 필요 (docs/security-clean-install.md §6)."
echo ""
echo "NOTE: TUNING_JOB_IMAGE는 digest pin 없이 :latest 태그."
echo "  프로덕션에서는 @sha256:<64hex> digest로 교체 필요."
