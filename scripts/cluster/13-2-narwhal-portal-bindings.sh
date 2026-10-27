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
#   OpenBao: secret/ KV-v2 mount + narwhal-portal 정책 + k8s auth role (Workload Identity)
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
  local web_origins_json
  web_origins_json=$(echo "${redirect_uris_json}" | python3 -c '
import json
import re
import sys

origins = {
    match.group("origin")
    for uri in json.load(sys.stdin)
    if (match := re.match(r"^(?P<origin>https?://[^/*]+)", uri))
}
print(json.dumps(sorted(origins), separators=(",", ":")))
')

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
      -s "webOrigins=${web_origins_json}" >&2

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
    kc_exec update "clients/${existing_id}" -r "${REALM}" \
      -s "redirectUris=${redirect_uris_json}" \
      -s "webOrigins=${web_origins_json}" \
      -s "directAccessGrantsEnabled=false" >&2
    echo "  -> '${client_id}' 이미 존재 (ID: ${existing_id}), origins synced" >&2
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
# `portal.${DOMAIN}/*` 와일드카드는 장식이 아니다 — narwhal-portal의
# federated-logout 라우트(isAllowedRedirectUrl)가 portal 호스트 아래 임의 경로를
# 로그아웃 후 리다이렉트로 허용하므로(/login 외에도 /dashboard 등), 두 개 명시
# URL만 등록하면 그 외 경로 요청이 Keycloak에서 "Invalid redirect uri"로 거부된다
# (seam-drift S08 정리 시 제거했다가 Codex 리뷰로 회귀 발견, 원복).
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

# Keycloak client security preflight validation (#148, #149)
echo "Keycloak 클라이언트 보안 검증..."
VALIDATE_SCRIPT=""
if [ -f "/home/vagrant/scripts/cluster/validate-keycloak-clients.sh" ]; then
  VALIDATE_SCRIPT="/home/vagrant/scripts/cluster/validate-keycloak-clients.sh"
elif [ -f "$(dirname "$0")/validate-keycloak-clients.sh" ]; then
  VALIDATE_SCRIPT="$(dirname "$0")/validate-keycloak-clients.sh"
fi

if [ -n "${VALIDATE_SCRIPT}" ]; then
  bash "${VALIDATE_SCRIPT}" "${REALM}"
fi

# ──────────────────────────────────────────────
# STEP 2: OpenBao — KV mount + 정책 + Kubernetes Workload Identity
# ──────────────────────────────────────────────
echo ""
echo "=== [2/4] OpenBao 설정 ==="

OPENBAO_ROOT_TOKEN=$(kubectl get secret openbao-init -n storage \
  -o jsonpath='{.data.root_token}' | base64 -d)
OPENBAO_POD=$(kubectl get pod -n storage \
  -l app.kubernetes.io/name=openbao \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

OPENBAO_K8S_AUTH_AUDIENCE="${OPENBAO_K8S_AUTH_AUDIENCE:-vault}"
OPENBAO_K8S_ROLE="narwhal-portal"
OPENBAO_K8S_AUTH_MOUNT="kubernetes"
OPENBAO_K8S_TOKEN_PATH="/var/run/secrets/openbao/token"
ENABLE_LEGACY_OPENBAO_TOKEN="${ENABLE_LEGACY_OPENBAO_TOKEN:-false}"
OPENBAO_PORTAL_TOKEN=""

if [ -z "${OPENBAO_POD}" ]; then
  echo "WARN: OpenBao pod 없음 — OpenBao Kubernetes auth role 설정 생략"
else
  # KV v2 mount 활성화 (멱등)
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao secrets enable -version=2 -path=secret kv 2>/dev/null \
    || echo "  secret/ mount 이미 존재 (정상)"

  # 정책 작성: Least privilege — portal secret inventory uses metadata only.
  # Do not grant secret/data access: the portal must never read secret values.
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    /bin/sh -c '
cat > /tmp/portal.hcl << '"'"'POLICY_EOF'"'"'
# Least privilege: secret inventory uses only KV-v2 metadata list/read.
# Secret values are intentionally inaccessible to the portal identity.
path "secret/metadata/narwhal-portal/*" {
  capabilities = ["read","list"]
}
POLICY_EOF
bao policy write narwhal-portal /tmp/portal.hcl
rm -f /tmp/portal.hcl
echo "  narwhal-portal policy written"
'

  # OpenBao TokenReviewer 권한 보장 (Kubernetes auth method 토큰 검증에 필수)
  kubectl create clusterrolebinding openbao-auth-delegator \
    --clusterrole=system:auth-delegator \
    --serviceaccount=storage:openbao \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 || true

  # Kubernetes auth method 활성화 (멱등)
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao auth enable kubernetes 2>/dev/null \
    || echo "  auth/kubernetes 이미 활성화됨 (정상)"

  # Kubernetes auth config 설정 (클러스터 내부 API 서버 주소 지정)
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao write auth/kubernetes/config \
      kubernetes_host="https://kubernetes.default.svc"

  # narwhal-portal 전용 Kubernetes auth role 생성 (단기 토큰: TTL 1h, Max TTL 4h, audience 검증)
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao write auth/kubernetes/role/narwhal-portal \
      bound_service_account_names="narwhal-portal" \
      bound_service_account_namespaces="devtools" \
      policies="narwhal-portal" \
      audience="${OPENBAO_K8S_AUTH_AUDIENCE}" \
      token_ttl="1h" \
      token_max_ttl="4h"

  # Role 영속화 검증 (실패 시 즉시 fail-closed)
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao read auth/kubernetes/role/narwhal-portal >/dev/null 2>&1 || {
      echo "ERROR: auth/kubernetes/role/narwhal-portal 영속화 확인 실패" >&2
      exit 1
    }
  echo "  OpenBao Kubernetes auth role 'narwhal-portal' 설정 및 검증 완료"

  # OpenBao raft snapshot 백업 (#431, gitops/resources/openbao-backup.yaml): 전용 정책/롤.
  # 정책은 sys/storage/raft/snapshot 의 read 하나뿐 — root 토큰/unseal 키는 CronJob 에 주지 않는다.
  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    /bin/sh -c '
set -eu
cat > /tmp/openbao-backup.hcl << '"'"'POLICY_EOF'"'"'
# Least privilege: take a raft snapshot, nothing else (no restore = no sys/storage/raft/snapshot update).
path "sys/storage/raft/snapshot" {
  capabilities = ["read"]
}
POLICY_EOF
bao policy write openbao-backup /tmp/openbao-backup.hcl
rm -f /tmp/openbao-backup.hcl
# 저장된 정책 내용 검증: snapshot read 한 경로만 있어야 한다 (write 실패/드리프트 fail-closed).
STORED=$(bao policy read openbao-backup)
[ "$(printf "%s\n" "${STORED}" | grep -c "^path ")" = 1 ]
printf "%s\n" "${STORED}" | grep -Fq "path \"sys/storage/raft/snapshot\""
printf "%s\n" "${STORED}" | grep -Fq "capabilities = [\"read\"]"
echo "  openbao-backup policy written and verified"
'

  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
    bao write auth/kubernetes/role/openbao-backup \
      bound_service_account_names="openbao-backup" \
      bound_service_account_namespaces="storage" \
      policies="openbao-backup" \
      audience="${OPENBAO_K8S_AUTH_AUDIENCE}" \
      token_no_default_policy=true \
      token_ttl="10m" \
      token_max_ttl="10m"

  kubectl exec -n storage "${OPENBAO_POD}" -- \
    env BAO_TOKEN="${OPENBAO_ROOT_TOKEN}" \
        BAO_ADDR="https://127.0.0.1:8200" \
        BAO_SKIP_VERIFY=true \
        EXPECT_AUDIENCE="${OPENBAO_K8S_AUTH_AUDIENCE}" \
    /bin/sh -c '
set -eu
R=auth/kubernetes/role/openbao-backup
bao read -format=json "${R}" | grep -Fq "\"token_no_default_policy\": true"
# 저장된 롤이 의도한 값 그대로인지 확인 (정책/바인딩/audience 드리프트 fail-closed). audience 는 한 곳에서 읽는다.
[ "$(bao read -field=policies "${R}")" = "[openbao-backup]" ]
[ "$(bao read -field=bound_service_account_names "${R}")" = "[openbao-backup]" ]
[ "$(bao read -field=bound_service_account_namespaces "${R}")" = "[storage]" ]
[ "$(bao read -field=audience "${R}")" = "${EXPECT_AUDIENCE}" ]
' || {
      echo "ERROR: auth/kubernetes/role/openbao-backup 영속화 확인 실패" >&2
      exit 1
    }
  echo "  OpenBao Kubernetes auth role 'openbao-backup' 설정 및 검증 완료"

  # Narwhal #156: 8760h 장기 bearer 토큰은 기본적으로 발급하지 않음.
  # 레거시 호환이 절대적으로 필요한 경우에만 ENABLE_LEGACY_OPENBAO_TOKEN=true 플래그로 게이트.
  if [ "${ENABLE_LEGACY_OPENBAO_TOKEN}" = "true" ]; then
    echo "WARN: ENABLE_LEGACY_OPENBAO_TOKEN=true 감지 — 8760h 장기 bearer 토큰을 발급합니다 (DEPRECATED: 보안상 비권장)." >&2
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
    echo "  [LEGACY] OPENBAO_TOKEN 발급 완료 (${#OPENBAO_PORTAL_TOKEN} chars)"
  else
    echo "  OpenBao Workload Identity 사용: 8760h 장기 bearer 토큰 발급 생략 (보안 강화)"
  fi

  # 머신 판독 가능 증적 기록 (evidence JSON)
  mkdir -p /home/vagrant/.narwhal
  cat > /home/vagrant/.narwhal/openbao-portal-auth.json <<JSONEOF
{
  "auth_method": "kubernetes",
  "k8s_mount": "${OPENBAO_K8S_AUTH_MOUNT}",
  "role": "${OPENBAO_K8S_ROLE}",
  "bound_service_account_names": ["narwhal-portal"],
  "bound_service_account_namespaces": ["devtools"],
  "audience": "${OPENBAO_K8S_AUTH_AUDIENCE}",
  "token_ttl": "1h",
  "token_max_ttl": "4h",
  "policy": "narwhal-portal",
  "policy_capabilities": {
    "secret/metadata/narwhal-portal/*": ["read", "list"]
  },
  "legacy_token_enabled": ${ENABLE_LEGACY_OPENBAO_TOKEN},
  "configured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSONEOF
  echo "  evidence written to /home/vagrant/.narwhal/openbao-portal-auth.json"
fi

# ──────────────────────────────────────────────
# STEP 3: 나머지 값 수집
# ──────────────────────────────────────────────
echo ""
echo "=== [3/4] 나머지 값 수집 ==="

# 자동 생성 시크릿
AUTH_SECRET=$(openssl rand -base64 32)
LIVE_INGEST_SECRET=$(openssl rand -base64 24)

# narwhal-portal SA (idempotent) — kept unconditional: the RBAC bindings above target
# this SA regardless of legacy-token mode, and (when ENABLE_LEGACY_K8S_SA_TOKEN=true)
# `kubectl create token` below still needs it to exist before ArgoCD's chart apply lands
# the identical SA (this script runs at step 13-2, chart apply is step 13 but ArgoCD sync
# lands after — see original note this replaced).
kubectl create serviceaccount narwhal-portal -n devtools \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 || true

# Narwhal-portal#20: a static 1-year K8S_SA_TOKEN bearer token is legacy/deprecated — the
# portal now reads a kubelet-rotated projected serviceAccountToken volume by default (see
# gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml, k8s-api-token volume +
# K8S_SA_TOKEN_FILE/K8S_TOKEN_AUDIENCE env). Only mint/inject the long-lived token when an
# operator explicitly opts into the legacy path, same gating pattern as
# ENABLE_LEGACY_OPENBAO_TOKEN above.
ENABLE_LEGACY_K8S_SA_TOKEN="${ENABLE_LEGACY_K8S_SA_TOKEN:-false}"
K8S_SA_TOKEN=""
if [ "${ENABLE_LEGACY_K8S_SA_TOKEN}" = "true" ]; then
  echo "WARN: ENABLE_LEGACY_K8S_SA_TOKEN=true 감지 — 8760h 장기 bearer 토큰을 발급합니다 (DEPRECATED: 보안상 비권장, 대신 projected SA 토큰 볼륨 사용)." >&2
  K8S_SA_TOKEN=$(kubectl create token narwhal-portal \
    -n devtools --duration=8760h 2>/dev/null || echo "")
  if [ -z "${K8S_SA_TOKEN}" ]; then
    echo "WARN: narwhal-portal SA 토큰 발급 실패 (K8S_SA_TOKEN 비어 있음)"
  else
    echo "  K8S_SA_TOKEN 발급 완료 (${#K8S_SA_TOKEN} chars)"
  fi
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
  # Wait for the server itself before polling its HTTP endpoint. The retry loop below
  # used to be the only gate, and on a clean install it burned all 12 attempts before
  # argocd-server had finished rolling out — reproduced 2026-08-04, where every attempt
  # failed and the placeholder shipped. Waiting on the Deployment first spends the
  # retry budget on the ingress route rather than on the pod.
  echo "  argocd-server 롤아웃 대기 (최대 300s)..."
  kubectl -n devtools rollout status deploy/argocd-server --timeout=300s || \
    echo "  WARN: argocd-server 롤아웃 확인 실패 — 그래도 발급을 시도한다"

  # 30 × 10s is defensive headroom, NOT the fix — an earlier version of this comment
  # claimed the window had been "measured too short", which was wrong twice over. The
  # real failure was deterministic: `accounts.narwhal-portal` did not exist, because its
  # only declaration lived in a GitOps chart applied at step 14, one step after this
  # script. The token endpoint answered "account 'narwhal-portal' does not exist" and no
  # amount of waiting would have changed that. 13-argocd.sh now creates the account, and
  # this loop is back to covering what a retry loop can actually cover — the ingress
  # route and TLS cert settling after 13-argocd returns.
  for attempt in $(seq 1 30); do
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
        # Prove the token works before trusting it. Issuance returning a string is not
        # the same as that string authenticating — and a token that does not is exactly
        # as useless as the placeholder this used to write.
        tok_code=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
          -H "Authorization: Bearer ${ARGOCD_TOKEN}" \
          "https://argocd.${DOMAIN}/api/v1/applications" || echo "000")
        if [ "${tok_code}" = "200" ]; then
          echo "  ARGOCD_TOKEN 발급·검증 완료 (${#ARGOCD_TOKEN} chars, attempt ${attempt})"
          break
        fi
        echo "  발급된 토큰이 인증되지 않음 (HTTP ${tok_code}) — 재시도"
        ARGOCD_TOKEN=""
      fi
    fi
    echo "  ArgoCD 토큰 발급 대기 (server/ingress 준비 중) attempt ${attempt}/30..."
    sleep 10
  done
fi

# No placeholder. Writing REPLACE_ME__argocd_token here is what shipped a fake
# credential on 2026-08-04: the portal caught the resulting 401, logged
# "Connection failed, returning empty" and rendered an empty widget, so the cluster
# looked healthy and the failure surfaced only when someone asked why the portal was
# blank. An absent key is honest — the portal fails the same way, but the secret no
# longer claims to hold a credential, and this script says so and exits non-zero.
# 13-2 is critical in 06-phase2-start.sh; exit 3 (Secrets applied, only ARGOCD_TOKEN
# missing) is the single non-zero result Phase 2 tolerates, every other failure aborts.
ARGOCD_TOKEN_OK=true
if [ -z "${ARGOCD_TOKEN}" ]; then
  ARGOCD_TOKEN_OK=false
  echo "ERROR: ArgoCD 토큰 발급 실패 — narwhal-portal-secrets 에 ARGOCD_TOKEN 을 넣지 않는다." >&2
  echo "       확인: accounts.narwhal-portal 이 argocd-cm 에 있는지, argocd.${DOMAIN} 이 응답하는지." >&2
  echo "       수동 재발급 후 파드 재시작:" >&2
  echo "         ${BASH_SOURCE[0]}   # 이 스크립트는 멱등이라 그냥 다시 실행하면 된다" >&2
  echo "         kubectl -n devtools rollout restart deploy/narwhal-portal" >&2
fi

# ──────────────────────────────────────────────
# STEP 4: narwhal-portal-secrets 생성 (멱등)
# ──────────────────────────────────────────────
echo ""
echo "=== [4/4] narwhal-portal-secrets 생성 ==="

# ARGOCD_TOKEN is passed only when one was issued AND verified. Omitting the key beats
# writing a placeholder: envFrom simply leaves it unset, the portal fails the same way,
# and nothing in the secret pretends to be a credential.
ARGOCD_TOKEN_ARG=()
if [ "${ARGOCD_TOKEN_OK}" = "true" ]; then
  ARGOCD_TOKEN_ARG=(--from-literal=ARGOCD_TOKEN="${ARGOCD_TOKEN}")
fi

# Narwhal #156: OPENBAO_TOKEN is passed ONLY when legacy token generation was explicitly enabled.
# Workload identity uses OPENBAO_AUTH_METHOD=kubernetes and rotated k8s SA tokens.
OPENBAO_TOKEN_ARG=()
if [ -n "${OPENBAO_PORTAL_TOKEN:-}" ]; then
  OPENBAO_TOKEN_ARG=(--from-literal=OPENBAO_TOKEN="${OPENBAO_PORTAL_TOKEN}")
fi

# Narwhal-portal#20: K8S_SA_TOKEN is passed ONLY when ENABLE_LEGACY_K8S_SA_TOKEN=true
# actually minted one above. Default path uses the projected serviceAccountToken volume
# (k8s-api-token) mounted by the gitops chart instead — no static long-lived credential
# in the secret at all.
K8S_SA_TOKEN_ARG=()
if [ -n "${K8S_SA_TOKEN:-}" ]; then
  K8S_SA_TOKEN_ARG=(--from-literal=K8S_SA_TOKEN="${K8S_SA_TOKEN}")
fi

# Narwhal #156 (review fix): OPENBAO_AUTH_METHOD must track which credential was actually
# minted above — the portal's getOpenBaoToken() (narwhal-portal src/lib/openbao.ts) branches
# on this value and only reads OPENBAO_TOKEN when it is "token". Hardcoding "kubernetes" here
# meant ENABLE_LEGACY_OPENBAO_TOKEN=true minted a token the portal never looked at, silently
# falling through to kubernetes auth despite the operator explicitly requesting legacy mode.
OPENBAO_AUTH_METHOD_VALUE="kubernetes"
if [ "${ENABLE_LEGACY_OPENBAO_TOKEN}" = "true" ]; then
  OPENBAO_AUTH_METHOD_VALUE="token"
fi

umask 077
portal_secret_tmp="$(mktemp -d)"
trap 'rm -rf "${portal_secret_tmp}"' EXIT

# D1: keep the cache password in a dedicated Secret and preserve it across bootstrap reruns
# so the ACL and portal env stay synchronized; cost is one Secret; rotate only as a coordinated
# portal/Valkey rollout if preservation must be escaped.
valkey_auth_dir="${portal_secret_tmp}/valkey-auth"
mkdir -m 700 "${valkey_auth_dir}"
# D6: existence is probed separately from the password. A jsonpath read is empty both when the
# Secret is absent and when it exists without a usable `password`; conflating them would
# regenerate the password and overwrite a Secret that a running Valkey (ACL held in memory)
# still authenticates against. Any kubectl failure aborts; an existing-but-incomplete Secret
# aborts; only a truly absent Secret is generated. Cost: an incomplete Secret needs a manual
# fix; escape: delete that Secret deliberately and roll portal+Valkey together.
valkey_secret_exists="$(kubectl get secret narwhal-portal-valkey-auth -n devtools \
  --ignore-not-found -o name)" || {
  echo "ERROR: could not read narwhal-portal-valkey-auth; refusing to rotate the Valkey password" >&2
  exit 1
}
load_valkey_password() {
  local b64
  b64="$(kubectl get secret narwhal-portal-valkey-auth -n devtools -o jsonpath='{.data.password}')" || {
    echo "ERROR: could not read narwhal-portal-valkey-auth; refusing to rotate the Valkey password" >&2
    exit 1
  }
  if [ -z "${b64}" ]; then
    echo "ERROR: narwhal-portal-valkey-auth exists without a password; refusing to regenerate it (fix or delete the Secret deliberately)" >&2
    exit 1
  fi
  if ! printf '%s' "${b64}" | base64 -d > "${valkey_auth_dir}/password"; then
    echo "ERROR: narwhal-portal-valkey-auth password is not valid base64; refusing to regenerate it" >&2
    exit 1
  fi
}
# D4: the password file becomes Secret key `password` (portal env VALKEY_PASSWORD, used
# untrimmed) AND the users.acl token, so both must be byte-identical: write it without a
# trailing newline (tr, never a bare `> file` from openssl) and accept only 64 lowercase hex
# chars. Real Valkey 8.1.10 answers WRONGPASS to "<hex>\n". Cost: an existing Secret with any
# other shape aborts; escape: delete that Secret deliberately (coordinated portal rollout).
prepare_valkey_auth() {
  if ! LC_ALL=C grep -qxE '[0-9a-f]{64}' "${valkey_auth_dir}/password" \
    || [ "$(wc -c < "${valkey_auth_dir}/password" | tr -d ' ')" != 64 ]; then
    echo "ERROR: Valkey password must be exactly 64 hex chars with no whitespace/newline (narwhal-portal-valkey-auth or generation is malformed)" >&2
    exit 1
  fi
  chmod 600 "${valkey_auth_dir}/password"
  local valkey_password
  valkey_password="$(cat "${valkey_auth_dir}/password")"
# D3: only cache and live-stream commands are granted (derived from every ioredis call in
# narwhal-portal src: valkey.ts get/mget/set/multi/exec/del, idempotency.ts set/get,
# live-stream.ts incr/lpush/ltrim/lrange/publish/subscribe, live-k8s-informer.ts eval lease
# scripts calling get/pexpire/del, cache-invalidation.ts scan/unlink, ready check ping/info),
# with Pub/Sub scoped to its event channel; cost is ACL maintenance when commands change; escape via reviewed per-command ACL
# addition rather than granting a broad command category.
cat > "${valkey_auth_dir}/users.acl" <<EOF
user default off
user portal on >${valkey_password} ~* &live:events:chan +auth +hello +ping +info +select +client|setname +client|setinfo +get +mget +set +del +unlink +scan +incr +multi +exec +eval +pexpire +lpush +ltrim +lrange +publish +subscribe +unsubscribe
EOF
}
apply_valkey_auth() {
  kubectl create secret generic narwhal-portal-valkey-auth \
    --namespace devtools \
    --from-file=password="${valkey_auth_dir}/password" \
    --from-file=users.acl="${valkey_auth_dir}/users.acl" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}
if [ -n "${valkey_secret_exists}" ]; then
  load_valkey_password
  prepare_valkey_auth
  apply_valkey_auth
else
  openssl rand -hex 32 | tr -d '\n' > "${valkey_auth_dir}/password"
  prepare_valkey_auth
  # D7: first creation is atomic (`create`, never apply) so two concurrent first runs cannot
  # overwrite each other's password; the loser re-reads the winner's stored password.
  if ! valkey_create_err="$(kubectl create secret generic narwhal-portal-valkey-auth \
    --namespace devtools \
    --from-file=password="${valkey_auth_dir}/password" \
    --from-file=users.acl="${valkey_auth_dir}/users.acl" 2>&1 >/dev/null)"; then
    case "${valkey_create_err}" in
      *AlreadyExists*)
        load_valkey_password
        prepare_valkey_auth
        apply_valkey_auth
        ;;
      *)
        echo "ERROR: could not create narwhal-portal-valkey-auth: ${valkey_create_err}" >&2
        exit 1
        ;;
    esac
  fi
fi

# D-COST: portal >= 1.1.1 answers /api/cost with 503 unless all eight COST_* keys are
# set (portal/src/lib/cost.ts getCostPricing, "fail closed on unconfigured pricing").
# Kakao uses the public list price of the flavors it runs (csp/kakao-cloud/terraform:
# 3x t1i.large 2vCPU/8GiB 197 KRW/h + 3x t1i.xlarge 4vCPU/16GiB 395 KRW/h = 1776 KRW/h
# for 18 vCPU / 72 GiB, split CPU:MEM 0.65:0.35 per docs/adr-cost-basis.md in portal/;
# Block Storage SSD 0.16 KRW/GiB-h; VAT excluded; kakaocloud.com, read 2026-10-26).
# List prices are per GiB while the portal divides bytes by 1e9, so memory and storage
# are divided by 1.073741824 (GB = 1e9 bytes); keep CPU 64.13.
# Vagrant has no billable cost, so it keeps the ADR's placeholder rates and says so.
# Cost: a flavor or list-price change makes these stale. Escape hatch: every value is
# overridable via the same-named env var.
case "${DOMAIN}" in
  kakao.*)
    : "${COST_CPU_HOURLY:=64.13}"
    : "${COST_MEM_GB_HOURLY:=8.040}"
    : "${COST_STORAGE_GB_HOURLY:=0.1490}"
    : "${COST_CURRENCY:=KRW}"
    : "${COST_PRICING_VERSION:=kakao-cloud-t1i-2026-10-26-gb}"
    : "${COST_PRICING_EFFECTIVE_DATE:=2026-10-26}"
    : "${COST_PRICING_SOURCE:=kakaocloud.com list price: VM t1i.large 197 KRW/h, t1i.xlarge 395 KRW/h; Block Storage SSD 0.16 KRW/GiB-h (VAT excluded, read 2026-10-26)}"
    : "${COST_PRICING_SCOPE:=Kakao Cloud narwhal (3x t1i.large + 3x t1i.xlarge), usage-based showback estimate, CPU:MEM 0.65:0.35, per GB (bytes/1e9, as portal/src/lib/cost.ts counts; GiB list prices divided by 1.073741824), VAT excluded}"
    ;;
  *)
    : "${COST_CPU_HOURLY:=0.04}"
    : "${COST_MEM_GB_HOURLY:=0.005}"
    : "${COST_STORAGE_GB_HOURLY:=0.0001}"
    : "${COST_CURRENCY:=USD}"
    : "${COST_PRICING_VERSION:=placeholder-v0}"
    : "${COST_PRICING_EFFECTIVE_DATE:=2026-06-12}"
    : "${COST_PRICING_SOURCE:=placeholder with no cost basis (portal docs/adr-cost-basis.md section 1)}"
    : "${COST_PRICING_SCOPE:=Vagrant local lab (${DOMAIN}), no billable cost, usage-based showback estimate}"
    ;;
esac

kubectl create secret generic narwhal-portal-secrets \
  --namespace devtools \
  "${ARGOCD_TOKEN_ARG[@]}" \
  "${OPENBAO_TOKEN_ARG[@]}" \
  --from-literal=AUTH_SECRET="${AUTH_SECRET}" \
  --from-literal=AUTH_URL="https://portal.${DOMAIN}" \
  --from-literal=AUTH_TRUST_HOST="true" \
  --from-literal=AUTH_MOCK="false" \
  --from-literal=NEXT_PUBLIC_AUTH_MOCK="false" \
  --from-literal=KEYCLOAK_ISSUER="https://keycloak.${DOMAIN}/realms/${REALM}" \
  --from-literal=KEYCLOAK_CLIENT_ID="narwhal-portal" \
  --from-literal=KEYCLOAK_CLIENT_SECRET="${PORTAL_CLIENT_SECRET}" \
  --from-literal=KEYCLOAK_URL="https://keycloak.${DOMAIN}" \
  --from-literal=KEYCLOAK_REALM="${REALM}" \
  --from-literal=KEYCLOAK_ADMIN_REALM="${REALM}" \
  --from-literal=KEYCLOAK_ADMIN_CLIENT_ID="narwhal-portal-admin" \
  --from-literal=KEYCLOAK_ADMIN_CLIENT_SECRET="${ADMIN_CLIENT_SECRET}" \
  --from-literal=KEYCLOAK_INTERNAL_URL="http://keycloak-service.iam.svc.cluster.local:8080" \
  --from-literal=SLO_CHAIN_START="https://gitea.${DOMAIN}/apisix/logout" \
  --from-literal=K8S_API_SERVER="https://${VIP_ADDRESS:-192.168.56.100}:6443" \
  "${K8S_SA_TOKEN_ARG[@]}" \
  --from-literal=CLUSTER_NAME="narwhal" \
  --from-literal=CLUSTER_BASE_DOMAIN="${DOMAIN}" \
  --from-literal=ARGOCD_URL="http://argocd-server.devtools.svc.cluster.local" \
  --from-literal=ARGOCD_DEVELOPER_PROJECTS="" \
  --from-literal=APISIX_ADMIN_URL="http://apisix-admin.platform-system.svc.cluster.local:9180" \
  --from-literal=APISIX_API_KEY="${APISIX_API_KEY}" \
  --from-literal=APISIX_API_KEY_READONLY="${APISIX_API_KEY_READONLY}" \
  --from-literal=PROMETHEUS_URL="http://prometheus-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090" \
  --from-literal=ALERTMANAGER_URL="http://prometheus-stack-kube-prom-alertmanager.monitoring.svc.cluster.local:9093" \
  --from-literal=ALERT_SILENCE_MAX_HOURS="24" \
  --from-literal=LOKI_URL="http://loki.monitoring.svc.cluster.local:3100" \
  --from-literal=TEMPO_URL="http://tempo.monitoring.svc.cluster.local:3200" \
  --from-literal=HUBBLE_RELAY_ADDR="hubble-relay.kube-system.svc.cluster.local:80" \
  --from-literal=VALKEY_URL="rediss://narwhal-portal-valkey.devtools.svc.cluster.local:6379" \
  --from-literal=VALKEY_TLS="true" \
  --from-literal=OPENBAO_ADDR="https://openbao.storage.svc.cluster.local:8200" \
  --from-literal=OPENBAO_AUTH_METHOD="${OPENBAO_AUTH_METHOD_VALUE}" \
  --from-literal=OPENBAO_K8S_ROLE="${OPENBAO_K8S_ROLE}" \
  --from-literal=OPENBAO_K8S_AUTH_MOUNT="${OPENBAO_K8S_AUTH_MOUNT}" \
  --from-literal=OPENBAO_K8S_TOKEN_PATH="${OPENBAO_K8S_TOKEN_PATH}" \
  --from-literal=OPENBAO_K8S_AUTH_AUDIENCE="${OPENBAO_K8S_AUTH_AUDIENCE}" \
  --from-literal=TUNING_JOB_IMAGE="harbor.${DOMAIN}/library/tuning-job:latest" \
  --from-literal=TUNING_JOB_NAMESPACE="devtools" \
  --from-literal=TUNING_JOB_SERVICE_ACCOUNT="narwhal-tuning-job" \
  --from-literal=TRIVY_DB_REGISTRY="harbor.${DOMAIN}/library/trivy-db" \
  --from-literal=LIVE_INGEST_SECRET="${LIVE_INGEST_SECRET}" \
  --from-literal=LIVE_INGEST_LINK_HOSTS="" \
  --from-literal=LIVE_STREAM_DEGRADED="false" \
  --from-literal=OTEL_ENABLED="false" \
  --from-literal=OTEL_SERVICE_NAME="narwhal-portal" \
  --from-literal=NODE_EXTRA_CA_CERTS="/etc/ssl/narwhal/ca.crt" \
  --from-literal=SCORECARD_CONFIGMAP_NAME="narwhal-scorecard-rules" \
  --from-literal=SCORECARD_CONFIGMAP_NAMESPACE="devtools" \
  --from-literal=SERVICE_GRAPH_SOURCE="istio" \
  --from-literal=COST_CPU_HOURLY="${COST_CPU_HOURLY}" \
  --from-literal=COST_MEM_GB_HOURLY="${COST_MEM_GB_HOURLY}" \
  --from-literal=COST_STORAGE_GB_HOURLY="${COST_STORAGE_GB_HOURLY}" \
  --from-literal=COST_CURRENCY="${COST_CURRENCY}" \
  --from-literal=COST_PRICING_VERSION="${COST_PRICING_VERSION}" \
  --from-literal=COST_PRICING_EFFECTIVE_DATE="${COST_PRICING_EFFECTIVE_DATE}" \
  --from-literal=COST_PRICING_SOURCE="${COST_PRICING_SOURCE}" \
  --from-literal=COST_PRICING_SCOPE="${COST_PRICING_SCOPE}" \
  --dry-run=client -o json > "${portal_secret_tmp}/desired.json"

if kubectl get secret narwhal-portal-secrets -n devtools --ignore-not-found -o json \
  > "${portal_secret_tmp}/existing.json"; then
  if [ -s "${portal_secret_tmp}/existing.json" ]; then
    # D1: keep keys owned by other bootstrap scripts; cost is one helper and temp manifests.
    # Escape hatch: the explicit optional-key list prevents stale 13-2 credentials surviving.
    python3 "$(dirname "$0")/lib-preserve-secret-keys.py" \
      "${portal_secret_tmp}/desired.json" \
      "${portal_secret_tmp}/existing.json" \
      "${portal_secret_tmp}/merged.json" \
      --owned-key ARGOCD_TOKEN \
      --owned-key OPENBAO_TOKEN \
      --owned-key K8S_SA_TOKEN \
      --owned-key VALKEY_PASSWORD \
      --owned-key VALKEY_INSECURE_PRODUCTION
    kubectl apply -f "${portal_secret_tmp}/merged.json"
  else
    kubectl apply -f "${portal_secret_tmp}/desired.json"
  fi
else
  echo "ERROR: could not read existing narwhal-portal-secrets" >&2
  exit 1
fi

# D5: portal 1.1.0 refuses to start its Valkey client when VALKEY_INSECURE_PRODUCTION is
# defined at all (even "false"). `kubectl apply` only prunes it if the last-applied
# annotation lists it, which an older secret created by other means may not, so remove it
# explicitly and verify. Idempotent: guarded by a no-pipe existence read. Escape hatch:
# none needed, the key has no supported use.
stale_key="VALKEY_INSECURE_PRODUCTION"
# Existence, not value: a key present with an EMPTY value is still "defined" to the portal.
secret_has_key() {
  local json
  json="$(kubectl get secret narwhal-portal-secrets -n devtools -o json)" || return 2
  STALE_KEY="$1" SECRET_JSON="${json}" python3 -c '
import json, os
data = json.loads(os.environ["SECRET_JSON"]).get("data") or {}
raise SystemExit(0 if os.environ["STALE_KEY"] in data else 1)
'
}
stale_rc=0
secret_has_key "${stale_key}" || stale_rc=$?
if [ "${stale_rc}" -gt 1 ]; then
  echo "ERROR: could not re-read narwhal-portal-secrets to check ${stale_key}" >&2
  exit 1
fi
if [ "${stale_rc}" -eq 0 ]; then
  patch_rc=0
  kubectl patch secret narwhal-portal-secrets -n devtools --type=json \
    -p "[{\"op\":\"remove\",\"path\":\"/data/${stale_key}\"}]" || patch_rc=$?
  # A concurrent removal makes the patch fail; only fail if the key is still there.
  stale_rc=0
  secret_has_key "${stale_key}" || stale_rc=$?
  if [ "${stale_rc}" -ne 1 ]; then
    echo "ERROR: ${stale_key} still present in narwhal-portal-secrets after removal (patch rc=${patch_rc})" >&2
    exit 1
  fi
fi

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
echo "  OpenBao:          policy=narwhal-portal, k8s-auth role=narwhal-portal (short-lived 1h)"
echo "  K8s SA token:     narwhal-portal@devtools (8760h)"
echo "  ArgoCD token:     account=narwhal-portal"
echo "  Secret:           narwhal-portal-secrets (devtools)"
echo ""
echo "  Valkey: TLS + portal ACL enabled; dedicated auth Secret reconciled without printing its contents."
echo ""
echo "NOTE: TUNING_JOB_IMAGE는 digest pin 없이 :latest 태그."
echo "  프로덕션에서는 @sha256:<64hex> digest로 교체 필요."

# Exit non-zero when the ArgoCD token never landed, so the Phase 2 log carries a
# failure rather than a success. Exit 3 is warned about and Phase 2 continues — the
# portal is simply missing its ArgoCD integration until re-run.
if [ "${ARGOCD_TOKEN_OK}" != "true" ]; then
  echo ""
  echo "RESULT: ARGOCD_TOKEN 없이 완료 — 포털의 ArgoCD 위젯은 동작하지 않는다." >&2
  exit 3
fi
