#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 11-1-keycloak-admin.sh
# Phase: Keycloak master realm 영구 관리자 전환 (#573)
# - Keycloak 26 operator는 master realm에 임시(temporary) 부트스트랩 관리자 temp-admin을
#   만들고 Secret iam/keycloak-initial-admin 에 담는다. 콘솔은 "임시 관리자로 로그인" 경고를
#   띄우고 임시 계정 삭제를 권한다.
# - 이 스크립트는 영구 관리자 (platform-admin, realm role admin) 를 만들고 자격증명을
#   Secret iam/keycloak-admin (username, password) 에 보관한 뒤, 새 자격증명으로 로그인과
#   master realm 관리 API 호출이 실제로 되는 것을 확인하고서야 temp-admin을 삭제한다.
# - kcadm 소비자(11-2, 11-3, 13-2, scripts/test/*)는 keycloak-admin 이 있으면 그것을,
#   없으면 keycloak-initial-admin 을 쓴다 (lib.sh keycloak_admin_secret).
# Depends on: 11-keycloak.sh (Keycloak pod ready)
#
# D-KC-ADMIN-573 (owner 2026-11-08 "교체"):
#   결정: 사람 콘솔 로그인과 kcadm 자동화가 같은 영구 사용자 1개를 쓴다. 권한은 temp-admin 과
#         같은 master realm role `admin` 뿐이라 확대되지 않는다.
#   이유: 모든 소비자가 kcadm 비밀번호 로그인(config credentials --user/--password)이라 코드
#         변경이 가장 작다. client-credentials 서비스 계정으로 자동화를 분리하려면 11-2/11-3/
#         13-2/test 전부의 로그인 경로와 client secret 순환을 새로 만들어야 한다.
#   비용: 사람과 자동화가 한 자격증명을 공유한다 (감사 로그에서 구분 불가). Secret 은
#         cluster-admin 만 읽는다.
#   탈출구: 자동화만 서비스 계정(client credentials)으로 옮기려면 kc_login 만 바꾸면 된다.
#         keycloak-initial-admin Secret 은 operator 소유라 지우지 않는다 (지우면 operator 가
#         다시 만든다). 무해: KC 는 master realm 에 관리자가 있으면 부트스트랩 계정을 만들지
#         않는다.
#
# NOTE: 재실행 안전. 영구 관리자가 이미 동작하고 temp-admin 이 없으면 아무것도 바꾸지 않는다.
#       Secret 값은 출력하지 않는다.

PERM_USER="platform-admin"
PERM_SECRET="keycloak-admin"
BOOT_SECRET="keycloak-initial-admin"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=========================================="
echo "Keycloak master realm permanent admin (#573)"
echo "=========================================="

kc_exec() {
  kubectl exec -n iam keycloak-0 -c keycloak -- \
    /opt/keycloak/bin/kcadm.sh "$@"
}

# kc_login <user> <password>: kcadm master-realm 로그인 (한 번 시도, 출력 없음)
kc_login() {
  kc_exec config credentials --server http://localhost:8080 --realm master \
    --user "$1" --password "$2" >/dev/null 2>&1
}

# kc_can_administer: 현재 kcadm 세션이 master realm 관리 API 를 실제로 호출할 수 있는지
kc_can_administer() {
  kc_exec get realms/master --fields realm 2>/dev/null | jq -e '.realm == "master"' >/dev/null 2>&1
}

# kc_user_id <username>: master realm 사용자 id, 없으면 빈 문자열
kc_user_id() {
  kc_exec get users -r master -q "username=$1" -q exact=true --fields id,username 2>/dev/null \
    | jq -r --arg u "$1" '.[] | select(.username==$u) | .id' | head -n 1 || true
}

#=========================================
# 1. 사용할 수 있는 자격증명 찾기 (영구 > 부트스트랩)
#=========================================
echo "=== Locating a working master-realm admin credential ==="

PERM_PASS=""
if secret_exists "${PERM_SECRET}" iam; then
  PERM_PASS=$(read_secret_field "${PERM_SECRET}" iam password)
fi
BOOT_USER=$(read_secret_field "${BOOT_SECRET}" iam username)
BOOT_PASS=$(read_secret_field "${BOOT_SECRET}" iam password)
# operator 가 항상 temp-admin 으로 만들지만, Secret 이 이미 없는 재실행에서도 이름을 안다.
BOOT_USER="${BOOT_USER:-temp-admin}"

SESSION=""
for attempt in $(seq 1 18); do
  if [ -n "${PERM_PASS}" ] && kc_login "${PERM_USER}" "${PERM_PASS}" && kc_can_administer; then
    SESSION="permanent"
    break
  fi
  if [ -n "${BOOT_PASS}" ] && kc_login "${BOOT_USER}" "${BOOT_PASS}" && kc_can_administer; then
    SESSION="bootstrap"
    break
  fi
  echo "  Login attempt ${attempt}/18 failed, waiting 10s..."
  sleep 10
done

if [ -z "${SESSION}" ]; then
  echo "ERROR: neither ${PERM_SECRET} nor ${BOOT_SECRET} logs in to the master realm." >&2
  echo "  Recovery: docs/runbooks/keycloak-permanent-admin.md (Recovery)." >&2
  exit 1
fi
echo "  -> logged in with the ${SESSION} admin"

#=========================================
# 2. 영구 관리자 Secret (없을 때만 생성, 덮어쓰지 않음)
#=========================================
if ! secret_exists "${PERM_SECRET}" iam; then
  kubectl create secret generic "${PERM_SECRET}" -n iam \
    --from-literal=username="${PERM_USER}" \
    --from-literal=password="$(generate_password)" >/dev/null
  echo "  -> secret iam/${PERM_SECRET} created"
fi
PERM_PASS=$(read_secret_field "${PERM_SECRET}" iam password)
if [ -z "${PERM_PASS}" ]; then
  echo "ERROR: secret iam/${PERM_SECRET} has no password." >&2
  exit 1
fi

#=========================================
# 3. 영구 관리자 사용자를 Secret 값에 맞춘다 (부트스트랩 세션일 때만 필요)
#=========================================
if [ "${SESSION}" = "bootstrap" ]; then
  echo "=== Reconciling user '${PERM_USER}' in master realm ==="
  PERM_ID=$(kc_user_id "${PERM_USER}")
  if [ -z "${PERM_ID}" ]; then
    # firstName/lastName/email 이 없으면 KC 26 user profile 이 password grant 를
    # "Account is not fully set up" 으로 거절할 수 있다.
    kc_exec create users -r master \
      -s "username=${PERM_USER}" \
      -s "firstName=Platform" \
      -s "lastName=Admin" \
      -s "email=${PERM_USER}@${DOMAIN:-local.narwhal.internal}" \
      -s "emailVerified=true" \
      -s "enabled=true" >/dev/null
    PERM_ID=$(kc_user_id "${PERM_USER}")
    echo "  -> user '${PERM_USER}' created"
  else
    echo "  -> user '${PERM_USER}' already exists"
  fi
  if [ -z "${PERM_ID}" ]; then
    echo "ERROR: user '${PERM_USER}' not found after create." >&2
    exit 1
  fi
  kc_exec set-password -r master --userid "${PERM_ID}" --new-password "${PERM_PASS}" >/dev/null
  kc_exec update "users/${PERM_ID}" -r master -s enabled=true >/dev/null
  kc_exec add-roles -r master --uusername "${PERM_USER}" --rolename admin >/dev/null
  echo "  -> password synced from secret, realm role 'admin' granted"

  # 검증 게이트: 새 자격증명으로 로그인하고 master realm 관리 API 를 호출할 수 있어야 한다.
  VERIFIED=false
  for attempt in $(seq 1 6); do
    if kc_login "${PERM_USER}" "${PERM_PASS}" && kc_can_administer; then
      VERIFIED=true
      break
    fi
    sleep 5
  done
  if [ "${VERIFIED}" != "true" ]; then
    echo "ERROR: '${PERM_USER}' cannot administer the master realm; keeping ${BOOT_USER}." >&2
    exit 1
  fi
  echo "  -> verified: '${PERM_USER}' logs in and administers the master realm"
fi

#=========================================
# 4. 임시 관리자 삭제 (영구 관리자 검증 후에만, 이미 없으면 건너뜀)
#=========================================
echo "=== Removing temporary bootstrap admin '${BOOT_USER}' ==="
if [ "${BOOT_USER}" = "${PERM_USER}" ]; then
  echo "ERROR: bootstrap and permanent admin share the name '${PERM_USER}'; refusing to delete." >&2
  exit 1
fi
# 현재 세션은 영구 관리자다 (SESSION=permanent 이거나 3단계 검증에서 다시 로그인했다).
BOOT_ID=$(kc_user_id "${BOOT_USER}")
if [ -n "${BOOT_ID}" ]; then
  kc_exec delete "users/${BOOT_ID}" -r master >/dev/null
  if [ -n "$(kc_user_id "${BOOT_USER}")" ]; then
    echo "ERROR: '${BOOT_USER}' still exists after delete." >&2
    exit 1
  fi
  echo "  -> '${BOOT_USER}' deleted"
else
  echo "  -> '${BOOT_USER}' not present, nothing to delete"
fi

echo ""
echo "  Admin user:     ${PERM_USER}"
echo "  Admin password: kubectl get secret ${PERM_SECRET} -n iam -o jsonpath='{.data.password}' | base64 -d"
echo ""
echo "=== [11-1-keycloak-admin.sh] 완료 ==="
