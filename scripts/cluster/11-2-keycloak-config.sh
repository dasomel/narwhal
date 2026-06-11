#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 11-2-keycloak-config.sh
# Phase: Keycloak Operator realm/users/groups/clients 구성
# - Realm: narwhal (displayName: Narwhal IDP)
# - Groups: cluster-admin, developer, viewer, guest
# - Users: admin, dev, view, guest (그룹 할당 포함, emailVerified: true)
# - Custom groups scope + Group Membership mapper
# - microprofile-jwt groups mapper 중복 제거
# - kubernetes client (public, kubectl-oidc-login용) + Audience Mapper
# - K8s Secrets: keycloak-oidc-base (iam ns)
# Depends on: 11-keycloak.sh (Keycloak pod ready)
#
# NOTE: 재실행 안전 (creation-only idempotency).
# 재구성 필요 시 Keycloak UI에서 해당 realm 삭제 후 재실행.

DOMAIN="${DOMAIN:-local.narwhal.io}"
REALM="narwhal"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=========================================="
echo "Configuring Keycloak (realm: ${REALM})..."
echo "=========================================="

#=========================================
# kcadm.sh helper
#=========================================
kc_exec() {
  kubectl exec -n iam keycloak-0 -c keycloak -- \
    /opt/keycloak/bin/kcadm.sh "$@"
}

#=========================================
# 1. kcadm.sh 로그인 (재시도 루프)
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
  echo "ERROR: Cannot log in to Keycloak after 3 minutes"
  exit 1
fi

#=========================================
# 2. Realm 생성: narwhal
#=========================================
echo "=== Creating realm '${REALM}' ==="

EXISTING_REALM=$(kc_exec get realms 2>/dev/null \
  | jq -r ".[] | select(.realm==\"${REALM}\") | .realm" || echo "")

if [ -z "${EXISTING_REALM}" ]; then
  kc_exec create realms \
    -s "realm=${REALM}" \
    -s "displayName=Narwhal IDP" \
    -s "enabled=true" \
    -s "sslRequired=external" \
    -s "registrationAllowed=false"
  echo "  -> realm '${REALM}' created"
else
  echo "  -> realm '${REALM}' already exists, skipping"
fi

echo "=== Updating realm loginTheme to 'narwhal' ==="
kc_exec update "realms/${REALM}" -s "loginTheme=narwhal"
echo "  -> realm '${REALM}' loginTheme updated to 'narwhal'"

#=========================================
# 2-1. User Profile: unmanaged attribute 허용
#   Keycloak 24+는 선언된 프로필 외 사용자 속성을 기본 차단(조용히 무시)한다.
#   harbor_username 등 커스텀 속성(protocol mapper로 토큰에 주입)을 쓰려면 필수.
#=========================================
echo "=== Enabling unmanaged user attributes on realm '${REALM}' ==="
kc_exec get "realms/${REALM}/users/profile" 2>/dev/null \
  | jq '.unmanagedAttributePolicy = "ENABLED"' \
  | kubectl exec -i -n iam keycloak-0 -c keycloak -- /bin/sh -c 'cat > /tmp/user-profile.json'
kc_exec update "realms/${REALM}/users/profile" -f /tmp/user-profile.json
echo "  -> unmanagedAttributePolicy=ENABLED"


#=========================================
# 3. Custom groups scope 생성
#=========================================
echo "=== Creating 'groups' client scope ==="

GROUPS_SCOPE_ID=$(kc_exec get client-scopes -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="groups") | .id' || echo "")

if [ -z "${GROUPS_SCOPE_ID}" ]; then
  kc_exec create client-scopes -r "${REALM}" \
    -s "name=groups" \
    -s "protocol=openid-connect" \
    -s "description=User group membership" \
    -s 'attributes={"include.in.token.scope":"true","display.on.consent.screen":"true"}'
  GROUPS_SCOPE_ID=$(kc_exec get client-scopes -r "${REALM}" 2>/dev/null \
    | jq -r '.[] | select(.name=="groups") | .id')
  echo "  -> groups scope created (ID: ${GROUPS_SCOPE_ID})"
else
  echo "  -> groups scope already exists (ID: ${GROUPS_SCOPE_ID})"
fi

# Group Membership mapper in groups scope
GROUPS_MAPPER_ID=$(kc_exec get "client-scopes/${GROUPS_SCOPE_ID}/protocol-mappers/models" \
  -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="groups") | .id' || echo "")

if [ -z "${GROUPS_MAPPER_ID}" ]; then
  kc_exec create "client-scopes/${GROUPS_SCOPE_ID}/protocol-mappers/models" \
    -r "${REALM}" \
    -s "name=groups" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-group-membership-mapper" \
    -s 'config={"full.path":"false","id.token.claim":"true","access.token.claim":"true","claim.name":"groups","userinfo.token.claim":"true"}'
  echo "  -> groups mapper created in groups scope"
else
  echo "  -> groups mapper already exists in groups scope"
fi

# microprofile-jwt scope에서 groups mapper 삭제 (중복 방지)
MJ_SCOPE_ID=$(kc_exec get client-scopes -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="microprofile-jwt") | .id' || echo "")

if [ -n "${MJ_SCOPE_ID}" ]; then
  MJ_GROUPS_MAPPER_ID=$(kc_exec get "client-scopes/${MJ_SCOPE_ID}/protocol-mappers/models" \
    -r "${REALM}" 2>/dev/null \
    | jq -r '.[] | select(.name=="groups") | .id' || echo "")
  if [ -n "${MJ_GROUPS_MAPPER_ID}" ]; then
    kc_exec delete "client-scopes/${MJ_SCOPE_ID}/protocol-mappers/models/${MJ_GROUPS_MAPPER_ID}" \
      -r "${REALM}" 2>/dev/null || true
    echo "  -> removed duplicate groups mapper from microprofile-jwt scope"
  fi
fi

# Add groups scope as default scope to realm
kc_exec update "realms/${REALM}/default-default-client-scopes/${GROUPS_SCOPE_ID}" \
  -r "${REALM}" 2>/dev/null || true
echo "  -> groups scope added as realm default"

#=========================================
# 4. Groups 생성
#=========================================
echo "=== Creating groups ==="

declare -A GROUP_IDS
for group_name in cluster-admin developer viewer guest; do
  GROUP_ID=$(kc_exec get groups -r "${REALM}" 2>/dev/null \
    | jq -r ".[] | select(.name==\"${group_name}\") | .id" || echo "")
  if [ -z "${GROUP_ID}" ]; then
    kc_exec create groups -r "${REALM}" \
      -s "name=${group_name}"
    GROUP_ID=$(kc_exec get groups -r "${REALM}" 2>/dev/null \
      | jq -r ".[] | select(.name==\"${group_name}\") | .id")
    echo "  -> group '${group_name}' created (ID: ${GROUP_ID})"
  else
    echo "  -> group '${group_name}' already exists (ID: ${GROUP_ID})"
  fi
  GROUP_IDS["${group_name}"]="${GROUP_ID}"
done

#=========================================
# 5. Users 생성 (emailVerified: true 필수)
#=========================================
echo "=== Creating users ==="

# 비밀번호 secret 처리
if ! kubectl get secret keycloak-user-passwords -n iam &>/dev/null; then
  ADMIN_PASS="$(generate_password)"
  DEV_PASS="$(generate_password)"
  VIEW_PASS="$(generate_password)"
  GUEST_PASS="$(generate_password)"
  kubectl create secret generic keycloak-user-passwords -n iam \
    --from-literal=admin="${ADMIN_PASS}" \
    --from-literal=dev="${DEV_PASS}" \
    --from-literal=view="${VIEW_PASS}" \
    --from-literal=guest="${GUEST_PASS}"
  echo "  User passwords secret created (keycloak-user-passwords in iam)"
else
  ADMIN_PASS="$(kubectl get secret keycloak-user-passwords -n iam \
    -o jsonpath='{.data.admin}' | base64 -d)"
  DEV_PASS="$(kubectl get secret keycloak-user-passwords -n iam \
    -o jsonpath='{.data.dev}' | base64 -d)"
  VIEW_PASS="$(kubectl get secret keycloak-user-passwords -n iam \
    -o jsonpath='{.data.view}' | base64 -d)"
  GUEST_PASS="$(kubectl get secret keycloak-user-passwords -n iam \
    -o jsonpath='{.data.guest}' | base64 -d)"
  echo "  User passwords loaded from existing secret"
fi

# 사용자 생성 + 그룹 할당
declare -A USER_PASSWORDS
USER_PASSWORDS=( [admin]="${ADMIN_PASS}" [dev]="${DEV_PASS}" [view]="${VIEW_PASS}" [guest]="${GUEST_PASS}" )

declare -A USER_GROUPS
USER_GROUPS=( [admin]="cluster-admin" [dev]="developer" [view]="viewer" [guest]="guest" )

for username in admin dev view guest; do
  password="${USER_PASSWORDS[${username}]}"
  group_name="${USER_GROUPS[${username}]}"
  group_id="${GROUP_IDS[${group_name}]}"

  USER_ID=$(kc_exec get users -r "${REALM}" -q "username=${username}" 2>/dev/null \
    | jq -r ".[] | select(.username==\"${username}\") | .id" || echo "")

  if [ -z "${USER_ID}" ]; then
    kc_exec create users -r "${REALM}" \
      -s "username=${username}" \
      -s "firstName=${username}" \
      -s "lastName=narwhal" \
      -s "email=${username}@${DOMAIN}" \
      -s "emailVerified=true" \
      -s "enabled=true"
    USER_ID=$(kc_exec get users -r "${REALM}" -q "username=${username}" 2>/dev/null \
      | jq -r ".[] | select(.username==\"${username}\") | .id")
    echo "  -> user '${username}' created (ID: ${USER_ID})"
  else
    echo "  -> user '${username}' already exists (ID: ${USER_ID})"
  fi

  # 비밀번호 설정
  kc_exec set-password -r "${REALM}" \
    --username "${username}" \
    --new-password "${password}" 2>/dev/null || true

  # 그룹 할당
  kc_exec update "users/${USER_ID}/groups/${group_id}" \
    -r "${REALM}" -s "realm=${REALM}" -s "userId=${USER_ID}" -s "groupId=${group_id}" \
    -n 2>/dev/null || true
  echo "  -> user '${username}' assigned to group '${group_name}'"

  # harbor_username 속성: Harbor OIDC 온보딩 username 충돌 회피
  #   Harbor는 내장 로컬 admin을 예약하므로 keycloak 'admin'은 그대로 온보딩 불가
  #   -> admin은 narwhal-admin으로, 나머지는 자기 username 그대로 매핑
  #   (11-3에서 harbor 클라이언트 매퍼 + Harbor oidc_user_claim=harbor_username 설정)
  harbor_username="${username}"
  [ "${username}" = "admin" ] && harbor_username="narwhal-admin"
  kc_exec update "users/${USER_ID}" -r "${REALM}" \
    -s "attributes={\"harbor_username\":[\"${harbor_username}\"]}" 2>/dev/null || true
  echo "  -> user '${username}' harbor_username=${harbor_username}"
done

#=========================================
# 6. kubernetes client 생성 (public)
#=========================================
echo "=== Creating 'kubernetes' client (public) ==="

K8S_CLIENT_ID=$(kc_exec get clients -r "${REALM}" -q "clientId=kubernetes" 2>/dev/null \
  | jq -r '.[] | select(.clientId=="kubernetes") | .id' || echo "")

if [ -z "${K8S_CLIENT_ID}" ]; then
  kc_exec create clients -r "${REALM}" \
    -s "clientId=kubernetes" \
    -s "name=kubernetes" \
    -s "enabled=true" \
    -s "publicClient=true" \
    -s "standardFlowEnabled=true" \
    -s "directAccessGrantsEnabled=true" \
    -s 'redirectUris=["http://localhost:*"]' \
    -s 'webOrigins=["http://localhost"]' \
    -s "protocol=openid-connect"
  K8S_CLIENT_ID=$(kc_exec get clients -r "${REALM}" -q "clientId=kubernetes" 2>/dev/null \
    | jq -r '.[] | select(.clientId=="kubernetes") | .id')
  echo "  -> kubernetes client created (ID: ${K8S_CLIENT_ID})"
else
  echo "  -> kubernetes client already exists (ID: ${K8S_CLIENT_ID})"
fi

# groups scope를 kubernetes client에 할당
kc_exec update "clients/${K8S_CLIENT_ID}/default-client-scopes/${GROUPS_SCOPE_ID}" \
  -r "${REALM}" 2>/dev/null || true
echo "  -> groups scope assigned to kubernetes client"

# Protocol Mappers 추가

# username mapper
USERNAME_MAPPER_ID=$(kc_exec get "clients/${K8S_CLIENT_ID}/protocol-mappers/models" \
  -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="username") | .id' || echo "")

if [ -z "${USERNAME_MAPPER_ID}" ]; then
  kc_exec create "clients/${K8S_CLIENT_ID}/protocol-mappers/models" -r "${REALM}" \
    -s "name=username" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-usermodel-property-mapper" \
    -s 'config={"user.attribute":"username","claim.name":"preferred_username","id.token.claim":"true","access.token.claim":"true","userinfo.token.claim":"true","jsonType.label":"String"}'
  echo "  -> username mapper created"
else
  echo "  -> username mapper already exists"
fi

# groups mapper (client-level)
CLIENT_GROUPS_MAPPER_ID=$(kc_exec get "clients/${K8S_CLIENT_ID}/protocol-mappers/models" \
  -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="client-groups") | .id' || echo "")

if [ -z "${CLIENT_GROUPS_MAPPER_ID}" ]; then
  kc_exec create "clients/${K8S_CLIENT_ID}/protocol-mappers/models" -r "${REALM}" \
    -s "name=client-groups" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-group-membership-mapper" \
    -s 'config={"full.path":"false","id.token.claim":"true","access.token.claim":"true","claim.name":"groups","userinfo.token.claim":"true"}'
  echo "  -> client groups mapper created"
else
  echo "  -> client groups mapper already exists"
fi

# Audience mapper (필수: aud claim에 kubernetes 포함)
AUDIENCE_MAPPER_ID=$(kc_exec get "clients/${K8S_CLIENT_ID}/protocol-mappers/models" \
  -r "${REALM}" 2>/dev/null \
  | jq -r '.[] | select(.name=="kubernetes-audience") | .id' || echo "")

if [ -z "${AUDIENCE_MAPPER_ID}" ]; then
  kc_exec create "clients/${K8S_CLIENT_ID}/protocol-mappers/models" -r "${REALM}" \
    -s "name=kubernetes-audience" \
    -s "protocol=openid-connect" \
    -s "protocolMapper=oidc-audience-mapper" \
    -s 'config={"included.client.audience":"kubernetes","id.token.claim":"true","access.token.claim":"true"}'
  echo "  -> kubernetes audience mapper created"
else
  echo "  -> kubernetes audience mapper already exists"
fi

#=========================================
# 7. K8s Secrets 저장
#=========================================
echo "=== Storing OIDC base config in K8s secrets ==="

ISSUER_URL="https://keycloak.${DOMAIN}/realms/${REALM}"

kubectl create secret generic keycloak-oidc-base \
  --namespace iam \
  --from-literal=issuer_url="${ISSUER_URL}" \
  --from-literal=realm="${REALM}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "  -> keycloak-oidc-base secret created/updated (iam ns)"

#=========================================
# 8. 요약 출력
#=========================================
echo ""
echo "=========================================="
echo "Keycloak Configuration Summary"
echo "=========================================="
echo "Admin UI:    https://keycloak.${DOMAIN}"
echo "Admin user:  admin"
echo "Admin pass:  kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d"
echo ""
echo "Realm:       ${REALM}"
echo "Issuer URL:  ${ISSUER_URL}"
echo ""
echo "Users:       admin (cluster-admin), dev (developer), view (viewer), guest (guest)"
echo "User pass:   kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.<username>}' | base64 -d"
echo ""
echo "Clients:     kubernetes (public)"
echo ""
echo "Next step:   Run 11-3-keycloak-clients.sh for additional OIDC clients (apisix, etc.)"
echo "=========================================="
echo ""
echo "=== [11-2-keycloak-config.sh] 완료 ==="
