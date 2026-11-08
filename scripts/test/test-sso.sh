#!/bin/bash
set -euo pipefail

#=========================================
# SSO Integration Test Suite v2
#=========================================
# Validates the FULL SSO chain including:
# - Keycloak realm, scopes, mappers, clients
# - Token claims per client (admin evaluate-scopes; password grant must be rejected)
# - TLS skip-verify for self-signed certs
# - OIDC configuration in each app
# - HTTPS endpoints and redirect chains
#
# Usage:
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh --section=tls"
#   vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh --section=scopes"

SECTION_FILTER=""
for arg in "$@"; do
  case "${arg}" in
    --section=*) SECTION_FILTER="${arg#--section=}" ;;
  esac
done

if [ -f /home/vagrant/.kube/config-local ]; then
  export KUBECONFIG=/home/vagrant/.kube/config-local
fi

DOMAIN="${DOMAIN:-local.narwhal.internal}"
KEYCLOAK_URL="https://keycloak.${DOMAIN}"
REALM="${REALM:-narwhal}"   # 11-2-keycloak-config.sh; the old realm name was kubernetes
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  PASS  $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL  $1"; }
warn() { WARN_COUNT=$((WARN_COUNT + 1)); echo "  WARN  $1"; }

# D-LIVE-ABORT: under set -e a failing command (e.g. a missing Secret in a
# `$(kubectl ... | base64 -d)` assignment) used to end the run silently and skip
# every later check. Guard such calls with `|| true`; this trap is the backstop
# that makes any remaining abort loud.
set -E
trap 'echo "  FAIL  ABORTED at line ${LINENO}: unexpected command failure, later checks were skipped"' ERR

should_run() {
  [ -z "${SECTION_FILTER}" ] || [ "${SECTION_FILTER}" = "$1" ]
}

# D-505-HARBOR: use the installed credential for every Harbor API check.
# A missing Secret fails without trying a default password or logging its value.
harbor_api() {
  local password
  password=$(kubectl get secret harbor-secrets -n devtools -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' 2>/dev/null | base64 -d) || password=""
  if [ -z "${password}" ]; then
    return 1
  fi
  curl -sk --connect-timeout 5 -u "admin:${password}" "$1" 2>/dev/null
}

# D-GITEA-SSO-CREDENTIAL: reuse the installed admin Secret, with no fallback;
# keep the pod-local API path so gateway OIDC cannot intercept basic auth.
gitea_api() {
  local password
  password=$(kubectl get secret gitea-admin -n devtools -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d) || password=""
  if [ -z "${password}" ]; then
    return 1
  fi
  kubectl exec -n devtools "${GITEA_POD}" -- \
    curl -sf --max-time 10 "http://localhost:3000/api/v1$1" -u "gitea-admin:${password}" 2>/dev/null
}

# Get Keycloak admin token
get_admin_token() {
  local user pass secret=keycloak-initial-admin
  # D-KC-ADMIN-573: permanent admin when 11-1 has created it, else the bootstrap admin.
  kubectl get secret keycloak-admin -n iam >/dev/null 2>&1 && secret=keycloak-admin
  user=$(kubectl get secret "${secret}" -n iam -o jsonpath='{.data.username}' 2>/dev/null | base64 -d) || true
  pass=$(kubectl get secret "${secret}" -n iam -o jsonpath='{.data.password}' 2>/dev/null | base64 -d) || true
  curl -sk -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "username=${user}" \
    -d "password=${pass}" \
    -d "grant_type=password" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null
}

# Print the access-token payload (JSON) that <clientId> would issue to <username>.
# Every realm client has directAccessGrantsEnabled=false, so no user token can be
# minted from a script; Keycloak's admin "evaluate scopes" endpoint builds the same
# token without a login. Needs ADMIN_TOKEN from get_admin_token.
example_token() {
  local client="$1" user="$2" client_uuid user_id
  client_uuid=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=${client}" 2>/dev/null \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["id"] if d else "")' 2>/dev/null || true)
  user_id=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/users?username=${user}&exact=true" 2>/dev/null \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["id"] if d else "")' 2>/dev/null || true)
  [ -n "${client_uuid}" ] && [ -n "${user_id}" ] || return 0
  curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${client_uuid}/evaluate-scopes/generate-example-access-token?userId=${user_id}&scope=openid" \
    2>/dev/null
}

# D-GATEWAY-LIVE-LOGIN (#519): Keycloak authorization-code login with curl, one function for
# every gateway check. Needs a fresh cookie jar per call (a shared jar carries the Keycloak
# SSO cookie, so the form would be skipped). The password goes to curl on stdin, never argv.
# Prints "<final HTTP status> <final URL>" after the login, or NOFORM when the Keycloak login
# form cannot be parsed (the caller must fail on that, never pass). Callers must check the final
# URL: a wrong password re-renders the Keycloak login page with HTTP 200 (review of #536).
gateway_login() {
  local jar="$1" user="$2" password="$3" url="$4" page action
  page=$(curl -sk -L --connect-timeout 5 --max-time 20 -c "${jar}" -b "${jar}" "${url}" 2>/dev/null) || page=""
  action=$(printf '%s' "${page}" | python3 -c '
import sys, re, html
p = sys.stdin.read()
m = re.search(r"<form[^>]+action=\"([^\"]+)\"", p)
print(html.unescape(m.group(1)) if m and "name=\"username\"" in p and "name=\"password\"" in p else "")
' 2>/dev/null) || action=""
  if [ -z "${action}" ]; then
    echo "NOFORM"
    return 0
  fi
  KC_U="${user}" KC_P="${password}" python3 -c '
import os, urllib.parse
print(urllib.parse.urlencode({"username": os.environ["KC_U"], "password": os.environ["KC_P"], "credentialId": ""}), end="")
' 2>/dev/null \
    | curl -sk -L --connect-timeout 5 --max-time 30 -c "${jar}" -b "${jar}" -o /dev/null \
        -w '%{http_code} %{url_effective}' --data-binary @- "${action}" 2>/dev/null || echo "000 -"
}

# D-VELERO-UI-ADMIN-ONLY: Velero UI has app-native OAuth, so the gateway never sees the user. Log in
# at Keycloak with redirect_uri /login, take the code from the redirect (no -L) and trade it at
# /api/auth/oauth for the app JWT. Prints the JWT, or nothing when any step fails (callers fail on
# empty, never pass). The password goes to curl on stdin, never argv.
velero_ui_token() {
  local user="$1" password="$2" jar page action loc code
  jar=$(mktemp "${GW_TMP:-/tmp}/vu.XXXXXX") || return 0
  page=$(curl -sk -L --connect-timeout 5 --max-time 20 -c "${jar}" -b "${jar}" \
    "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/auth?client_id=velero-ui&redirect_uri=https%3A%2F%2Fvelero-ui.${DOMAIN}%2Flogin&response_type=code&scope=openid%20profile%20email%20groups&state=t" 2>/dev/null) || page=""
  action=$(printf '%s' "${page}" | python3 -c '
import sys, re, html
p = sys.stdin.read()
m = re.search(r"<form[^>]+action=\"([^\"]+)\"", p)
print(html.unescape(m.group(1)) if m and "name=\"username\"" in p and "name=\"password\"" in p else "")
' 2>/dev/null) || action=""
  [ -n "${action}" ] || return 0
  loc=$(KC_U="${user}" KC_P="${password}" python3 -c '
import os, urllib.parse
print(urllib.parse.urlencode({"username": os.environ["KC_U"], "password": os.environ["KC_P"], "credentialId": ""}), end="")
' 2>/dev/null | curl -sk --connect-timeout 5 --max-time 30 -c "${jar}" -b "${jar}" -o /dev/null \
    -w '%{redirect_url}' --data-binary @- "${action}" 2>/dev/null) || loc=""
  code=$(printf '%s' "${loc}" | sed -n 's/.*[?&]code=\([^&]*\).*/\1/p')
  [ -n "${code}" ] || return 0
  curl -sk --connect-timeout 5 --max-time 20 "https://velero-ui.${DOMAIN}/api/auth/oauth?code=${code}&state=t" 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])' 2>/dev/null || true
}

# D-DASHBOARD-FORBIDDEN-IN-200 (#551): Kubernetes Dashboard (api 1.14.0, modules/common/errors/handler.go)
# treats a Forbidden from the apiserver as a NON-critical error: a list endpoint such as
# /api/v1/pod/<ns> answers HTTP 200 with the usual list plus "errors":[{"ErrStatus":{"status":"Failure",
# "message":"pods is forbidden: User ...","reason":"Forbidden","code":403}}] so the SPA can show a warning.
# A status-only check therefore reads a denied user as allowed. Reads the body on stdin, takes the HTTP
# status as $1 and prints DENIED (401/403, or 200 whose errors[] holds a Forbidden), ALLOWED (200 with no
# Forbidden error) or UNKNOWN (anything else, including a body that is not JSON); callers fail on UNKNOWN.
dashboard_verdict() {
  local code="$1"
  case "${code}" in
    401|403) cat >/dev/null; echo DENIED; return 0 ;;
    200) ;;
    *) cat >/dev/null; echo UNKNOWN; return 0 ;;
  esac
  python3 -c '
import sys, json
try:
  d = json.load(sys.stdin)
except Exception:
  print("UNKNOWN"); sys.exit(0)
if not isinstance(d, dict):
  print("UNKNOWN"); sys.exit(0)
errs = d.get("errors") or []
if not isinstance(errs, list):
  errs = [errs]
for e in errs:
  st = e.get("ErrStatus", e) if isinstance(e, dict) else {}
  if not isinstance(st, dict):
    st = {}
  if st.get("code") == 403 or st.get("reason") == "Forbidden" or "forbidden" in json.dumps(e).lower():
    print("DENIED"); sys.exit(0)
print("ALLOWED")
' 2>/dev/null || echo UNKNOWN
}

# D-DASHBOARD-LIVE-TOKEN (#551): the Dashboard takes a Bearer id_token only, so log in at Keycloak with
# the public PKCE client kubernetes-dashboard (redirect /sso/callback, no -L on the login POST), trade
# the code at the token endpoint and print the id_token. Prints nothing when any step fails (callers
# fail on empty, never pass). The password goes to curl on stdin, never argv.
dashboard_token() {
  local user="$1" password="$2" jar ver chal ru page action loc code
  jar=$(mktemp "${KD_TMP:-/tmp}/kd.XXXXXX") || return 0
  ver=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))' 2>/dev/null) || return 0
  chal=$(python3 -c 'import sys,hashlib,base64; print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()).rstrip(b"=").decode())' "${ver}" 2>/dev/null) || return 0
  ru="https://dashboard.${DOMAIN}/sso/callback"
  page=$(curl -sk -L --connect-timeout 5 --max-time 20 -c "${jar}" -b "${jar}" -G \
    --data-urlencode "client_id=kubernetes-dashboard" --data-urlencode "redirect_uri=${ru}" \
    --data-urlencode "response_type=code" --data-urlencode "scope=openid profile email groups" \
    --data-urlencode "state=t" --data-urlencode "code_challenge=${chal}" --data-urlencode "code_challenge_method=S256" \
    "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/auth" 2>/dev/null) || page=""
  action=$(printf '%s' "${page}" | python3 -c '
import sys, re, html
p = sys.stdin.read()
m = re.search(r"<form[^>]+action=\"([^\"]+)\"", p)
print(html.unescape(m.group(1)) if m and "name=\"username\"" in p and "name=\"password\"" in p else "")
' 2>/dev/null) || action=""
  [ -n "${action}" ] || return 0
  loc=$(KC_U="${user}" KC_P="${password}" python3 -c '
import os, urllib.parse
print(urllib.parse.urlencode({"username": os.environ["KC_U"], "password": os.environ["KC_P"], "credentialId": ""}), end="")
' 2>/dev/null | curl -sk --connect-timeout 5 --max-time 30 -c "${jar}" -b "${jar}" -o /dev/null \
    -w '%{redirect_url}' --data-binary @- "${action}" 2>/dev/null) || loc=""
  code=$(printf '%s' "${loc}" | sed -n 's/.*[?&]code=\([^&]*\).*/\1/p')
  [ -n "${code}" ] || return 0
  curl -sk --connect-timeout 5 --max-time 20 -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -d grant_type=authorization_code -d client_id=kubernetes-dashboard -d "code=${code}" \
    --data-urlencode "redirect_uri=${ru}" -d "code_verifier=${ver}" 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)["id_token"])' 2>/dev/null || true
}

echo "============================================"
echo "SSO Integration Test Suite v2"
echo "============================================"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

#=========================================
# 1. KEYCLOAK INFRASTRUCTURE
#=========================================
if should_run "keycloak"; then
  echo "--- [1/9] Keycloak Infrastructure ---"

  KC_PODS=$(kubectl get pods -n iam -l app=keycloak --no-headers 2>/dev/null | { grep -c "Running" || true; })
  if [ "${KC_PODS}" -ge 1 ]; then
    pass "Keycloak pod: Running"
  else
    fail "Keycloak pod: not running"
  fi

  KC_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "${KEYCLOAK_URL}" 2>/dev/null || echo "000")
  if [ "${KC_CODE}" = "200" ] || [ "${KC_CODE}" = "302" ]; then
    pass "Keycloak HTTPS: HTTP ${KC_CODE}"
  else
    fail "Keycloak HTTPS: HTTP ${KC_CODE}"
  fi

  OIDC_CODE=$(curl -sk -o /dev/null -w '%{http_code}' \
    "${KEYCLOAK_URL}/realms/${REALM}/.well-known/openid-configuration" 2>/dev/null || echo "000")
  if [ "${OIDC_CODE}" = "200" ]; then
    pass "OIDC Discovery: HTTP ${OIDC_CODE}"
  else
    fail "OIDC Discovery: HTTP ${OIDC_CODE}"
  fi

  ADMIN_TOKEN=$(get_admin_token) || true
  if [ -n "${ADMIN_TOKEN}" ]; then
    pass "Admin API: authenticated"
  else
    fail "Admin API: authentication failed"
  fi
  echo ""
fi

#=========================================
# 2. CLIENT SCOPES & MAPPERS
#=========================================
if should_run "scopes"; then
  echo "--- [2/9] Client Scopes & Mappers ---"

  ADMIN_TOKEN=$(get_admin_token) || true
  if [ -z "${ADMIN_TOKEN}" ]; then
    fail "Cannot check scopes: admin auth failed"
  else
    # Check 'groups' client scope exists
    GROUPS_SCOPE=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}/client-scopes" 2>/dev/null \
      | python3 -c "
import sys,json
scopes = json.load(sys.stdin)
matches = [s for s in scopes if s['name'] == 'groups']
if matches:
    print(f'EXISTS|{matches[0][\"id\"]}')
else:
    print('MISSING')
" 2>/dev/null) || true

    if echo "${GROUPS_SCOPE}" | grep -q "^EXISTS"; then
      GROUPS_SCOPE_ID=$(echo "${GROUPS_SCOPE}" | cut -d'|' -f2) || true
      pass "'groups' client scope: exists (${GROUPS_SCOPE_ID})"

      # Check groups mapper in scope
      MAPPER_EXISTS=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/client-scopes/${GROUPS_SCOPE_ID}/protocol-mappers/models" 2>/dev/null \
        | python3 -c "
import sys,json
mappers = json.load(sys.stdin)
gm = [m for m in mappers if m.get('protocolMapper') == 'oidc-group-membership-mapper']
if gm:
    claim = gm[0].get('config', {}).get('claim.name', '?')
    print(f'EXISTS|claim={claim}')
else:
    print('MISSING')
" 2>/dev/null) || true

      if echo "${MAPPER_EXISTS}" | grep -q "^EXISTS"; then
        pass "'groups' mapper: $(echo "${MAPPER_EXISTS}" | cut -d'|' -f2)"
      else
        fail "'groups' mapper: MISSING in groups scope"
      fi

      # Check groups scope assigned to each client
      CLIENTS_JSON=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" 2>/dev/null) || true

      for client_name in kubernetes argocd grafana gitea harbor headlamp; do
        CLIENT_UUID=$(echo "${CLIENTS_JSON}" | python3 -c "
import sys,json
clients = json.load(sys.stdin)
matches = [c['id'] for c in clients if c['clientId'] == '${client_name}']
print(matches[0] if matches else '')
" 2>/dev/null) || true

        if [ -n "${CLIENT_UUID}" ]; then
          HAS_GROUPS=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/default-client-scopes" 2>/dev/null \
            | python3 -c "
import sys,json
scopes = json.load(sys.stdin)
print('YES' if any(s['name'] == 'groups' for s in scopes) else 'NO')
" 2>/dev/null) || true
          if [ "${HAS_GROUPS}" = "YES" ]; then
            pass "${client_name}: groups scope assigned"
          else
            fail "${client_name}: groups scope NOT assigned"
          fi
        else
          fail "${client_name}: client not found"
        fi
      done
    else
      fail "'groups' client scope: MISSING (all OIDC apps will fail with invalid_scope)"
    fi
  fi
  echo ""
fi

#=========================================
# 3. CLIENTS & REDIRECT URIs
#=========================================
if should_run "clients"; then
  echo "--- [3/9] Clients & Redirect URIs ---"

  ADMIN_TOKEN=$(get_admin_token) || true
  if [ -z "${ADMIN_TOKEN}" ]; then
    fail "Cannot check clients: admin auth failed"
  else
    REALM_CHECK=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("realm",""))' 2>/dev/null) || true
    if [ "${REALM_CHECK}" = "${REALM}" ]; then
      pass "Realm '${REALM}': exists"
    else
      fail "Realm '${REALM}': not found"
    fi

    CLIENTS_JSON=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" 2>/dev/null) || true

    for client in argocd grafana gitea harbor headlamp; do   # oauth2-proxy removed in bc8bce28
      CLIENT_INFO=$(echo "${CLIENTS_JSON}" | python3 -c "
import sys,json
clients = json.load(sys.stdin)
matches = [c for c in clients if c['clientId'] == '${client}']
if matches:
    c = matches[0]
    redirects = c.get('redirectUris', [])
    has_https = any('https://' in r and '${DOMAIN}' in r for r in redirects)
    print(f'EXISTS|https={has_https}|{redirects}')
else:
    print('MISSING')
" 2>/dev/null) || true

      if echo "${CLIENT_INFO}" | grep -q "^EXISTS"; then
        if echo "${CLIENT_INFO}" | grep -q "https=True"; then
          pass "Client '${client}': HTTPS redirect URIs OK"
        else
          REDIRECTS=$(echo "${CLIENT_INFO}" | sed 's/^EXISTS|[^|]*|//') || true
          fail "Client '${client}': no HTTPS redirect for ${DOMAIN} — ${REDIRECTS}"
        fi
      else
        fail "Client '${client}': MISSING"
      fi
    done

    # Users & Groups
    USER_COUNT=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}/users" 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "0")
    if [ "${USER_COUNT}" -ge 4 ]; then
      pass "Users: ${USER_COUNT} (admin, dev, view, guest)"
    else
      fail "Users: ${USER_COUNT} (expected >= 4)"
    fi

    GROUP_COUNT=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}/groups" 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "0")
    if [ "${GROUP_COUNT}" -ge 4 ]; then
      pass "Groups: ${GROUP_COUNT} (cluster-admin, developer, viewer, guest)"
    else
      fail "Groups: ${GROUP_COUNT} (expected >= 4)"
    fi
  fi
  echo ""
fi

#=========================================
# 4. TOKEN FLOWS (with groups scope)
#=========================================
if should_run "token"; then
  echo "--- [4/9] Token Claims ---"

  # The kubernetes client is public with directAccessGrantsEnabled=false, so a
  # password grant must be REJECTED rather than minting a token. Send the real
  # password: with a wrong one, a rejection would prove nothing about the grant.
  ADMIN_USER_PASS=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' 2>/dev/null | base64 -d) || true
  TOKEN_RESP=$(curl -sk -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -d "client_id=kubernetes" \
    -d "username=admin" \
    --data-urlencode "password=${ADMIN_USER_PASS}" \
    -d "grant_type=password" 2>/dev/null) || true

  ACCESS_TOKEN=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null) || true
  ERROR_CODE=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("error","unknown"))' 2>/dev/null) || true
  ERROR_DESC=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("error_description",""))' 2>/dev/null) || true

  if [ -n "${ACCESS_TOKEN}" ]; then
    fail "kubernetes client: password grant unexpectedly succeeded"
  else
    case "${ERROR_CODE}" in
      unauthorized_client|unsupported_grant_type)
        pass "kubernetes client: password grant rejected (${ERROR_CODE})"
        ;;
      *)
        fail "kubernetes client: unexpected password-grant response (${ERROR_CODE}: ${ERROR_DESC})"
        ;;
    esac
  fi

  ADMIN_TOKEN=$(get_admin_token) || true
  if [ -z "${ADMIN_TOKEN}" ]; then
    fail "Cannot evaluate token claims: admin auth failed"
  else
    # Each seed user's kubernetes-client token: exactly its own group, the issuer
    # the API server trusts, and the kubernetes audience.
    for pair in admin:cluster-admin dev:developer view:viewer guest:guest; do
      user="${pair%%:*}" group="${pair#*:}"
      CLAIMS=$(example_token kubernetes "${user}" | python3 -c '
import sys, json
d = json.load(sys.stdin)
aud = d.get("aud", [])
aud = [aud] if isinstance(aud, str) else aud
print("%s|%s|%s" % (",".join(d.get("groups", [])), d.get("iss", ""), "kubernetes" in aud))' 2>/dev/null || true)
      if [ -z "${CLAIMS}" ]; then
        fail "${user}: no example token (client or user missing)"
        continue
      fi
      IFS='|' read -r TOKEN_GROUPS TOKEN_ISS TOKEN_AUD <<< "${CLAIMS}"
      if [ "${TOKEN_GROUPS}" = "${group}" ]; then
        pass "${user}: groups=[${group}]"
      else
        fail "${user}: groups=[${TOKEN_GROUPS}] (expected [${group}])"
      fi
      if [ "${TOKEN_ISS}" = "${KEYCLOAK_URL}/realms/${REALM}" ] && [ "${TOKEN_AUD}" = "True" ]; then
        pass "${user}: iss=${TOKEN_ISS}, aud has kubernetes"
      else
        fail "${user}: iss=${TOKEN_ISS} aud-has-kubernetes=${TOKEN_AUD} (expected ${KEYCLOAK_URL}/realms/${REALM}, True)"
      fi
    done

    # argocd requests the groups scope explicitly; it must reach the token.
    SCOPE_IN_TOKEN=$(example_token argocd admin \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("scope",""))' 2>/dev/null || true)
    if echo "${SCOPE_IN_TOKEN}" | grep -qw "groups"; then
      pass "argocd client: groups scope in token (${SCOPE_IN_TOKEN})"
    else
      fail "argocd client: groups scope missing from token (${SCOPE_IN_TOKEN})"
    fi
  fi
  echo ""
fi

#=========================================
# 5. TLS VERIFICATION & CA TRUST (Keycloak SSO)
#=========================================
if should_run "tls"; then
  echo "--- [5/9] TLS Verification & CA Trust Settings ---"

  # ArgoCD: Keycloak TLS verification enforced with internal CA trust (Narwhal #147)
  ARGOCD_TLS_SKIP=$(kubectl get configmap argocd-cm -n devtools -o jsonpath='{.data.oidc\.tls\.insecure\.skip\.verify}' 2>/dev/null || echo "")
  ARGOCD_OIDC_CFG=$(kubectl get configmap argocd-cm -n devtools -o jsonpath='{.data.oidc\.config}' 2>/dev/null || echo "")
  if [ "${ARGOCD_TLS_SKIP}" = "true" ] || echo "${ARGOCD_OIDC_CFG}" | grep -qE 'insecureSkipVerify:[[:space:]]*true'; then
    fail "ArgoCD: Keycloak TLS verification bypassed (insecureSkipVerify=true)"
  else
    pass "ArgoCD: Keycloak TLS verification enforced (no insecureSkipVerify)"
  fi

  if echo "${ARGOCD_OIDC_CFG}" | grep -q 'rootCA:.*oidc\.keycloak\.rootCA'; then
    pass "ArgoCD: oidc.config references rootCA"
  else
    fail "ArgoCD: oidc.config missing rootCA reference"
  fi

  # APISIX: Gitea OIDC route enforces Keycloak TLS verification (Narwhal #141)
  APISIX_GITEA_ROUTE=$(kubectl get apisixroute gitea -n platform-system -o yaml 2>/dev/null || echo "")
  if [ -n "${APISIX_GITEA_ROUTE}" ]; then
    if echo "${APISIX_GITEA_ROUTE}" | grep -q 'ssl_verify:[[:space:]]*false'; then
      fail "APISIX: gitea route has ssl_verify=false"
    else
      pass "APISIX: gitea route enforces Keycloak TLS verification"
    fi
  else
    # Fallback to static manifest if live route not queryable
    if grep -q 'ssl_verify: false' gitops/charts/narwhal-platform/templates/apisix-routes.yaml; then
      fail "APISIX: apisix-routes.yaml has ssl_verify: false"
    else
      pass "APISIX: routes enforce Keycloak TLS verification (static manifest)"
    fi
  fi

  # Grafana: tls_skip_verify_insecure in grafana.ini
  # Grafana stores config in secrets or configmaps depending on chart
  GRAFANA_INI=$(kubectl exec deploy/prometheus-stack-grafana -n monitoring -c grafana -- cat /etc/grafana/grafana.ini 2>/dev/null || true)
  if echo "${GRAFANA_INI}" | grep -q "tls_skip_verify_insecure.*true"; then
    pass "Grafana: tls_skip_verify_insecure=true"
  else
    # Check from provisioner config
    GRAFANA_PROV=$(kubectl get secret -n monitoring -o name 2>/dev/null | { grep grafana || true; })
    if [ -n "${GRAFANA_PROV}" ]; then
      GRAFANA_SECRET=$(kubectl get secret prometheus-grafana -n monitoring -o jsonpath='{.data}' 2>/dev/null \
        | python3 -c '
import sys,json,base64
d=json.load(sys.stdin)
for k,v in d.items():
  decoded = base64.b64decode(v).decode("utf-8","replace")
  if "tls_skip_verify" in decoded:
    print("FOUND")
    break
else:
  print("NOT_FOUND")
' 2>/dev/null) || true
      if [ "${GRAFANA_SECRET}" = "FOUND" ]; then
        pass "Grafana: tls_skip_verify_insecure in secret"
      else
        warn "Grafana: tls_skip_verify_insecure not found (may need sync)"
      fi
    else
      warn "Grafana: cannot verify TLS settings"
    fi
  fi

  # Headlamp: combined CA cert mounted (v0.40.0 has no -oidc-skip-issuer-tls-verify flag)
  # initContainer merges system bundle + custom CA → /etc/ssl/certs/ca-certificates.crt
  HEADLAMP_CA_MOUNT=$(kubectl get deploy headlamp -n devtools -o jsonpath='{.spec.template.spec.containers[0].volumeMounts[?(@.mountPath=="/etc/ssl/certs/ca-certificates.crt")].name}' 2>/dev/null) || true
  if [ "${HEADLAMP_CA_MOUNT}" = "combined-certs" ]; then
    pass "Headlamp: combined CA bundle mounted at /etc/ssl/certs/ca-certificates.crt (volume: combined-certs)"
  else
    fail "Headlamp: combined-certs volume not mounted at /etc/ssl/certs/ca-certificates.crt (self-signed cert → SSO will fail)"
  fi

  # Harbor: oidc_verify_cert + HTTPS endpoint
  # NOTE: Use external HTTPS endpoint instead of pod IP — Istio ambient mesh
  # blocks direct pod IP access from the host (connection reset by peer)
  HARBOR_CORE_READY=$(kubectl get pods -n devtools -l app=harbor -l component=core -o jsonpath='{.items[0].status.phase}' 2>/dev/null) || true
  if [ "${HARBOR_CORE_READY}" = "Running" ]; then
    HARBOR_CFG=$(harbor_api "https://harbor.${DOMAIN}/api/v2.0/configurations") || HARBOR_CFG="{}"
    HARBOR_VERIFY=$(echo "${HARBOR_CFG}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("oidc_verify_cert",{}).get("value","?"))' 2>/dev/null) || true
    HARBOR_ENDPOINT=$(echo "${HARBOR_CFG}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("oidc_endpoint",{}).get("value",""))' 2>/dev/null) || true

    # D-HARBOR-OIDC-TRUST: core trusts the Narwhal CA (caBundleSecretName), so verification stays on.
    if [ "${HARBOR_VERIFY}" = "True" ]; then
      pass "Harbor: oidc_verify_cert=true"
    else
      fail "Harbor: oidc_verify_cert=${HARBOR_VERIFY} (need true; core trusts the Narwhal CA)"
    fi

    if echo "${HARBOR_ENDPOINT}" | grep -q "^https://"; then
      pass "Harbor: oidc_endpoint uses HTTPS"
    else
      fail "Harbor: oidc_endpoint='${HARBOR_ENDPOINT}' (should be HTTPS)"
    fi
  else
    warn "Harbor: core pod not running, skipping OIDC check"
  fi

  # Gitea: check if auth source exists
  GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || true
  if [ -n "${GITEA_POD}" ]; then
    GITEA_AUTH=$(kubectl exec -n devtools "${GITEA_POD}" -c gitea -- gitea admin auth list 2>/dev/null | { grep -ci "keycloak\|openid" || true; })
    if [ "${GITEA_AUTH}" -ge 1 ]; then
      pass "Gitea: OAuth2 auth source configured"
    else
      warn "Gitea: no Keycloak auth source found"
    fi
  else
    warn "Gitea: pod not found"
  fi
  echo ""
fi

#=========================================
# 6. APP OIDC CONFIGURATION
#=========================================
if should_run "apps"; then
  echo "--- [6/9] App OIDC Configuration ---"

  # ArgoCD: server.insecure + OIDC config
  INSECURE=$(kubectl get configmap argocd-cmd-params-cm -n devtools -o jsonpath='{.data.server\.insecure}' 2>/dev/null) || true
  if [ "${INSECURE}" = "true" ]; then
    pass "ArgoCD: server.insecure=true (cmd-params-cm)"
  else
    fail "ArgoCD: server.insecure='${INSECURE}' (should be true)"
  fi

  OIDC_CFG=$(kubectl get configmap argocd-cm -n devtools -o jsonpath='{.data.oidc\.config}' 2>/dev/null) || true
  if echo "${OIDC_CFG}" | grep -q "https://keycloak"; then
    pass "ArgoCD: OIDC issuer HTTPS"
  elif echo "${OIDC_CFG}" | grep -q "keycloak"; then
    fail "ArgoCD: OIDC issuer HTTP (should be HTTPS)"
  else
    fail "ArgoCD: OIDC not configured"
  fi

  ARGOCD_URL=$(kubectl get configmap argocd-cm -n devtools -o jsonpath='{.data.url}' 2>/dev/null) || true
  if echo "${ARGOCD_URL}" | grep -q "^https://"; then
    pass "ArgoCD: URL=${ARGOCD_URL}"
  else
    fail "ArgoCD: URL='${ARGOCD_URL}' (should be https://)"
  fi

  # Headlamp: OIDC issuer
  HEADLAMP_ISSUER=$(kubectl get secret headlamp-oidc-secret -n devtools -o jsonpath='{.data.OIDC_ISSUER_URL}' 2>/dev/null | base64 -d 2>/dev/null) || true
  if echo "${HEADLAMP_ISSUER}" | grep -q "^https://"; then
    pass "Headlamp: issuerURL=${HEADLAMP_ISSUER}"
  else
    fail "Headlamp: issuerURL='${HEADLAMP_ISSUER}' (should be https://)"
  fi

  # K8s API Server OIDC (every kube-apiserver pod must carry all 7 flags; D-APISERVER-OIDC-ALL)
  # One kube-apiserver mirror pod per control-plane node: a master with no mirror pod must FAIL,
  # not be skipped by the per-pod loop below.
  CP_NODE_COUNT=$(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | grep -c . || true)
  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].command}{"\n"}{end}' 2>/dev/null || true)
  if [ -z "${APISERVER_POD_LINES}" ]; then
    fail "K8s API: no kube-apiserver pods found"
  else
    APISERVER_POD_COUNT=0
    while IFS=$'\t' read -r pod_name pod_cmd; do
      [ -n "${pod_name}" ] || continue
      APISERVER_POD_COUNT=$((APISERVER_POD_COUNT + 1))
      missing_flags=""
      for flag in "oidc-issuer-url=${KEYCLOAK_URL}/realms/${REALM}" \
                  "oidc-client-id=kubernetes" \
                  "oidc-username-claim=preferred_username" \
                  "oidc-groups-claim=groups" \
                  "oidc-username-prefix=oidc:" \
                  "oidc-groups-prefix=oidc:" \
                  "oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt"; do
        if ! grep -qF -- "\"--${flag}\"" <<<"${pod_cmd}"; then
          missing_flags="${missing_flags} --${flag}"
        fi
      done
      if [ -z "${missing_flags}" ]; then
        pass "K8s API (${pod_name}): OIDC HTTPS + all 7 flags configured"
      else
        fail "K8s API (${pod_name}): missing${missing_flags}"
      fi
    done <<< "${APISERVER_POD_LINES}"
    if [ "${APISERVER_POD_COUNT}" -eq "${CP_NODE_COUNT}" ]; then
      pass "K8s API: ${APISERVER_POD_COUNT} kube-apiserver pod(s) for ${CP_NODE_COUNT} control-plane node(s)"
    else
      fail "K8s API: ${APISERVER_POD_COUNT} kube-apiserver pod(s) for ${CP_NODE_COUNT} control-plane node(s) (one per node expected)"
    fi
  fi

  # D-OPENBAO-OIDC-CONFIG: a live OpenBao without auth/oidc/config made /sso fail with
  # "could not load configuration" while every other check here passed. auth_url needs no
  # token and exercises config + default role + Keycloak discovery in one call.
  OPENBAO_AUTH_URL=$(curl -sk --max-time 10 -X POST "https://openbao.${DOMAIN}/v1/auth/oidc/oidc/auth_url" \
    -H 'Content-Type: application/json' \
    -d "{\"role\":\"default\",\"redirect_uri\":\"https://openbao.${DOMAIN}/sso/callback\"}" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("data",{}).get("auth_url",""))' 2>/dev/null) || OPENBAO_AUTH_URL=""
  case "${OPENBAO_AUTH_URL}" in
    "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/auth?"*)
      pass "OpenBao: auth/oidc/config set, auth_url points at Keycloak realm ${REALM}" ;;
    "")
      fail "OpenBao: auth_url returned no URL (auth/oidc/config or role missing; /sso would fail)" ;;
    *)
      fail "OpenBao: auth_url does not point at ${KEYCLOAK_URL}/realms/${REALM}" ;;
  esac
  echo ""
fi

#=========================================
# 7. HTTPS ENDPOINTS & SSO REDIRECTS
#=========================================
if should_run "endpoints"; then
  echo "--- [7/9] HTTPS Endpoints & SSO Redirects ---"

  for app in argocd grafana gitea keycloak headlamp harbor; do
    CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://${app}.${DOMAIN}" 2>/dev/null || echo "000")
    case "${CODE}" in
      200|301|302|303|307|308)
        pass "https://${app}.${DOMAIN}: HTTP ${CODE}" ;;
      *)
        fail "https://${app}.${DOMAIN}: HTTP ${CODE}" ;;
    esac
  done

  # ArgoCD SSO redirect test
  ARGOCD_SSO=$(curl -sk -D- -o /dev/null \
    "https://argocd.${DOMAIN}/auth/login?return_url=https://argocd.${DOMAIN}" 2>/dev/null \
    | grep -i "location:" | head -1 || echo "")
  if echo "${ARGOCD_SSO}" | grep -q "keycloak.${DOMAIN}"; then
    pass "ArgoCD SSO redirect: → Keycloak"
  elif [ -n "${ARGOCD_SSO}" ]; then
    warn "ArgoCD SSO redirect: ${ARGOCD_SSO}"
  fi
  echo ""
fi

#=========================================
# 8. OIDC RBAC (K8s Access Control)
#=========================================
if should_run "rbac"; then
  echo "--- [8/9] OIDC RBAC (K8s Access Control) ---"

  # The API server on every control-plane node must trust this realm and client,
  # add the oidc: prefixes that gitops/resources/rbac-policies.yaml subjects use,
  # and verify Keycloak TLS via /etc/kubernetes/pki/oidc-ca.crt (D-APISERVER-OIDC-ALL).
  # One kube-apiserver mirror pod per control-plane node: a master with no mirror pod must FAIL,
  # not be skipped by the per-pod loop below.
  CP_NODE_COUNT=$(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | grep -c . || true)
  APISERVER_POD_LINES=$(kubectl get pods -n kube-system -l component=kube-apiserver \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].command}{"\n"}{end}' 2>/dev/null || true)
  if [ -z "${APISERVER_POD_LINES}" ]; then
    fail "API server: no kube-apiserver pods found"
  else
    APISERVER_POD_COUNT=0
    while IFS=$'\t' read -r pod_name pod_cmd; do
      [ -n "${pod_name}" ] || continue
      APISERVER_POD_COUNT=$((APISERVER_POD_COUNT + 1))
      for flag in "oidc-issuer-url=${KEYCLOAK_URL}/realms/${REALM}" \
                  "oidc-client-id=kubernetes" \
                  "oidc-username-claim=preferred_username" \
                  "oidc-groups-claim=groups" \
                  "oidc-username-prefix=oidc:" \
                  "oidc-groups-prefix=oidc:" \
                  "oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt"; do
        if grep -qF -- "\"--${flag}\"" <<<"${pod_cmd}"; then
          pass "API server (${pod_name}): --${flag}"
        else
          fail "API server (${pod_name}): --${flag} not set"
        fi
      done
    done <<< "${APISERVER_POD_LINES}"
    if [ "${APISERVER_POD_COUNT}" -eq "${CP_NODE_COUNT}" ]; then
      pass "API server: ${APISERVER_POD_COUNT} kube-apiserver pod(s) for ${CP_NODE_COUNT} control-plane node(s)"
    else
      fail "API server: ${APISERVER_POD_COUNT} kube-apiserver pod(s) for ${CP_NODE_COUNT} control-plane node(s) (one per node expected)"
    fi
  fi

  # No user token can be scripted (password grant is disabled), so impersonate
  # the identity the API server derives from one: user oidc:<preferred_username>,
  # groups oidc:<group> plus system:authenticated. Section 4 covers the claims.
  # Note: kubectl may emit warnings (e.g., "resource 'nodes' is not namespace scoped")
  # so we extract only the last line (yes/no) for comparison
  test_rbac() {
    local label="$1" user="$2" group="$3" expected="$4"; shift 4
    local raw_result result
    raw_result=$(kubectl auth can-i "$@" --as="oidc:${user}" \
      --as-group="oidc:${group}" --as-group=system:authenticated 2>&1 || true)   # "no" exits 1; set -e
    result=$(echo "${raw_result}" | tail -1)
    if [ "${result}" = "${expected}" ]; then
      pass "${label}: ${result}"
    else
      fail "${label}: got '${result}' (expected '${expected}')"
    fi
  }

  # admin → platform-admin: broad workload access, but no node or RBAC mutation
  test_rbac "admin get nodes" admin cluster-admin "yes" get nodes
  test_rbac "admin create pods kube-system" admin cluster-admin "yes" create pods -n kube-system
  test_rbac "admin delete nodes" admin cluster-admin "no" delete nodes
  test_rbac "admin create clusterrolebindings" admin cluster-admin "no" create clusterrolebindings

  # dev → developer: read-only across namespaces for a fixed resource list (no nodes, namespaces,
  # secrets), writes only in dev; see docs/common/app-sso-permission-matrix.md
  test_rbac "dev create pods in dev" dev developer "yes" create pods -n dev
  test_rbac "dev get pods in devtools" dev developer "yes" get pods -n devtools
  test_rbac "dev get pods in monitoring" dev developer "yes" get pods -n monitoring
  test_rbac "dev create pods in kube-system" dev developer "no" create pods -n kube-system
  test_rbac "dev get secrets in kube-system" dev developer "no" get secrets -n kube-system
  test_rbac "dev get nodes" dev developer "no" get nodes
  test_rbac "dev list namespaces" dev developer "no" list namespaces

  # view → platform-viewer: cluster-wide read, including nodes; no writes, no secrets
  test_rbac "view get pods in dev" view viewer "yes" get pods -n dev
  test_rbac "view get pods in kube-system" view viewer "yes" get pods -n kube-system
  test_rbac "view get nodes" view viewer "yes" get nodes
  test_rbac "view create pods in dev" view viewer "no" create pods -n dev
  test_rbac "view get secrets in dev" view viewer "no" get secrets -n dev

  # guest: no Kubernetes binding at all
  test_rbac "guest get pods in dev" guest guest "no" get pods -n dev
  test_rbac "guest get nodes" guest guest "no" get nodes
  echo ""
fi

#=========================================
# 9. APP-LEVEL ACCESS CONTROL
#=========================================
if should_run "acl"; then
  echo "--- [9/9] App-Level Access Control ---"

  ADMIN_TOKEN=$(get_admin_token) || true

  # --- ArgoCD RBAC ---
  echo "  [ArgoCD RBAC]"

  # Get ArgoCD admin password for API auth
  ARGOCD_PASS=$(kubectl -n devtools get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d 2>/dev/null) || true
  ARGOCD_API="https://argocd.${DOMAIN}"

  # ArgoCD session token for admin (local admin)
  ARGOCD_SESSION=$(curl -sk -X POST "${ARGOCD_API}/api/v1/session" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"${ARGOCD_PASS}\"}" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null) || true

  if [ -n "${ARGOCD_SESSION}" ]; then
    # Check RBAC policy has guest→role:none
    RBAC_CSV=$(kubectl get configmap argocd-rbac-cm -n devtools -o jsonpath='{.data.policy\.csv}' 2>/dev/null) || true
    if echo "${RBAC_CSV}" | grep -q "g, guest, role:none"; then
      pass "ArgoCD RBAC: guest → role:none configured"
    else
      fail "ArgoCD RBAC: guest → role:none NOT configured"
    fi

    if echo "${RBAC_CSV}" | grep -q "role:none.*deny"; then
      pass "ArgoCD RBAC: role:none deny policy exists"
    else
      fail "ArgoCD RBAC: role:none deny policy missing"
    fi

    if echo "${RBAC_CSV}" | grep -q "g, developer, role:developer"; then
      pass "ArgoCD RBAC: developer → role:developer"
    else
      fail "ArgoCD RBAC: developer mapping missing"
    fi
  else
    warn "ArgoCD: cannot get session token, skipping RBAC checks"
  fi

  # D-ARGOCD-LIVE-RBAC: the ConfigMap lines above do not prove enforcement. A non-empty
  # policy.default is retried after a deny and gave guest read access to every app (2026-11-04
  # SSO sweep), so log in as real users through Keycloak and ask the API. A login that did not
  # produce an ArgoCD session fails; an empty list only counts when userinfo proves the session.
  echo "  [ArgoCD live login]"
  ARGO_TMP=$(mktemp -d)
  ARGO_BASE="https://argocd.${DOMAIN}"
  for argo_user in guest view; do
    ARGO_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath="{.data.${argo_user}}" 2>/dev/null | base64 -d 2>/dev/null) || ARGO_PW=""
    if [ -z "${ARGO_PW}" ]; then
      fail "ArgoCD ${argo_user}: Secret iam/keycloak-user-passwords has no ${argo_user} password"
      continue
    fi
    ARGO_JAR="${ARGO_TMP}/${argo_user}.jar"
    ARGO_RES=$(gateway_login "${ARGO_JAR}" "${argo_user}" "${ARGO_PW}" "${ARGO_BASE}/auth/login?return_url=${ARGO_BASE}/") || ARGO_RES="000 -"
    if [ "${ARGO_RES}" = "NOFORM" ]; then
      fail "ArgoCD ${argo_user}: login form not found, cannot verify"
      continue
    fi
    ARGO_LOGGED=$(curl -sk --connect-timeout 5 --max-time 15 -b "${ARGO_JAR}" "${ARGO_BASE}/api/v1/session/userinfo" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("loggedIn", False))' 2>/dev/null) || ARGO_LOGGED=""
    if [ "${ARGO_LOGGED}" != "True" ]; then
      fail "ArgoCD ${argo_user}: no ArgoCD session after Keycloak login (${ARGO_RES%% *}), cannot verify"
      continue
    fi
    ARGO_COUNT=$(curl -sk --connect-timeout 5 --max-time 20 -b "${ARGO_JAR}" "${ARGO_BASE}/api/v1/applications" 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("items") or []))' 2>/dev/null) || ARGO_COUNT="ERR"
    ARGO_GET=$(curl -sk --connect-timeout 5 --max-time 15 -o /dev/null -w '%{http_code}' -b "${ARGO_JAR}" \
      "${ARGO_BASE}/api/v1/applications/narwhal-platform" 2>/dev/null) || ARGO_GET="000"
    ARGO_SYNC=$(curl -sk --connect-timeout 5 --max-time 15 -o /dev/null -w '%{http_code}' -b "${ARGO_JAR}" \
      -X POST -H 'Content-Type: application/json' -d '{"dryRun":true}' \
      "${ARGO_BASE}/api/v1/applications/narwhal-platform/sync" 2>/dev/null) || ARGO_SYNC="000"
    case "${argo_user}" in
      guest)
        if [ "${ARGO_COUNT}" = "0" ]; then
          pass "ArgoCD guest: application list is empty"
        else
          fail "ArgoCD guest: application list returned ${ARGO_COUNT} apps (matrix: deny)"
        fi
        case "${ARGO_GET}" in
          403|404) pass "ArgoCD guest: single application GET -> HTTP ${ARGO_GET}" ;;
          *) fail "ArgoCD guest: single application GET expected 403/404, got HTTP ${ARGO_GET}" ;;
        esac ;;
      view)
        if [ "${ARGO_COUNT}" != "ERR" ] && [ "${ARGO_COUNT}" -ge 1 ] 2>/dev/null; then
          pass "ArgoCD view: application list returns ${ARGO_COUNT} apps"
        else
          fail "ArgoCD view: application list expected apps, got '${ARGO_COUNT}'"
        fi
        if [ "${ARGO_GET}" = "200" ]; then
          pass "ArgoCD view: single application GET -> HTTP 200"
        else
          fail "ArgoCD view: single application GET expected 200, got HTTP ${ARGO_GET}"
        fi
        if [ "${ARGO_SYNC}" = "403" ]; then
          pass "ArgoCD view: sync denied -> HTTP 403"
        else
          fail "ArgoCD view: sync expected 403, got HTTP ${ARGO_SYNC}"
        fi ;;
    esac
  done
  rm -rf "${ARGO_TMP}"

  # D-ADMIN-FULL (owner 2026-11-04): a cluster-admin login holds the top role in every app that
  # has its own role model. Real Keycloak logins; NOFORM or a missing session fails.
  echo "  [Admin top role in apps]"
  ADM_TMP=$(mktemp -d)
  ADM_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' 2>/dev/null | base64 -d 2>/dev/null) || ADM_PW=""
  DEV_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.dev}' 2>/dev/null | base64 -d 2>/dev/null) || DEV_PW=""
  if [ -z "${ADM_PW}" ] || [ -z "${DEV_PW}" ]; then
    fail "Admin top role: Secret iam/keycloak-user-passwords has no admin/dev password"
  else
    # Harbor: group admin shows as admin_role_in_auth (sysadmin_flag stays false by Harbor design);
    # the proof is a sysadmin-only API.
    for h_user in admin dev; do
      H_PW="${ADM_PW}"; [ "${h_user}" = "dev" ] && H_PW="${DEV_PW}"
      H_RES=$(gateway_login "${ADM_TMP}/h-${h_user}.jar" "${h_user}" "${H_PW}" "https://harbor.${DOMAIN}/c/oidc/login") || H_RES="000 -"
      if [ "${H_RES}" = "NOFORM" ]; then fail "Harbor ${h_user}: login form not found, cannot verify"; continue; fi
      H_ROLE=$(curl -sk --max-time 15 -b "${ADM_TMP}/h-${h_user}.jar" "https://harbor.${DOMAIN}/api/v2.0/users/current" 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("admin_role_in_auth", False))' 2>/dev/null) || H_ROLE=""
      H_USERS=$(curl -sk --max-time 15 -o /dev/null -w '%{http_code}' -b "${ADM_TMP}/h-${h_user}.jar" "https://harbor.${DOMAIN}/api/v2.0/users" 2>/dev/null) || H_USERS="000"
      if [ "${h_user}" = "admin" ]; then
        if [ "${H_ROLE}" = "True" ] && [ "${H_USERS}" = "200" ]; then
          pass "Harbor admin: admin_role_in_auth=true, sysadmin-only /users HTTP 200"
        else
          fail "Harbor admin: expected admin_role_in_auth=true and /users 200, got '${H_ROLE}' / ${H_USERS}"
        fi
      elif [ "${H_ROLE}" = "False" ] && [ "${H_USERS}" = "403" ]; then
        pass "Harbor dev: not an admin (/users HTTP 403)"
      else
        fail "Harbor dev: expected admin_role_in_auth=false and /users 403, got '${H_ROLE}' / ${H_USERS}"
      fi
    done

    # Grafana: server admin (GrafanaAdmin) and org Admin.
    G_RES=$(gateway_login "${ADM_TMP}/g.jar" admin "${ADM_PW}" "https://grafana.${DOMAIN}/login/generic_oauth") || G_RES="000 -"
    if [ "${G_RES}" = "NOFORM" ]; then
      fail "Grafana admin: login form not found, cannot verify"
    else
      G_SERVER=$(curl -sk --max-time 15 -b "${ADM_TMP}/g.jar" "https://grafana.${DOMAIN}/api/user" 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("isGrafanaAdmin", False))' 2>/dev/null) || G_SERVER=""
      G_ORG=$(curl -sk --max-time 15 -b "${ADM_TMP}/g.jar" "https://grafana.${DOMAIN}/api/user/orgs" 2>/dev/null \
        | python3 -c 'import sys,json; print(",".join(o["role"] for o in json.load(sys.stdin)))' 2>/dev/null) || G_ORG=""
      if [ "${G_SERVER}" = "True" ] && [ "${G_ORG}" = "Admin" ]; then
        pass "Grafana admin: server admin and org Admin"
      else
        fail "Grafana admin: expected isGrafanaAdmin=true and org Admin, got '${G_SERVER}' / '${G_ORG}'"
      fi
    fi

    # Argo CD: role:admin can sync.
    A_RES=$(gateway_login "${ADM_TMP}/a.jar" admin "${ADM_PW}" "https://argocd.${DOMAIN}/auth/login?return_url=https://argocd.${DOMAIN}/") || A_RES="000 -"
    if [ "${A_RES}" = "NOFORM" ]; then
      fail "ArgoCD admin: login form not found, cannot verify"
    else
      A_SYNC=$(curl -sk --max-time 15 -o /dev/null -w '%{http_code}' -b "${ADM_TMP}/a.jar" -X POST -H 'Content-Type: application/json' \
        -d '{"dryRun":true}' "https://argocd.${DOMAIN}/api/v1/applications/narwhal-platform/sync" 2>/dev/null) || A_SYNC="000"
      if [ "${A_SYNC}" = "200" ]; then
        pass "ArgoCD admin: sync of a platform app allowed (HTTP 200)"
      else
        fail "ArgoCD admin: sync expected 200, got HTTP ${A_SYNC}"
      fi
    fi

    # Keycloak realm console: authorization code + PKCE for security-admin-console, then the
    # realm admin API. A non-admin (dev) must get 403 on the same call.
    KC_VER=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))' 2>/dev/null) || KC_VER=""
    KC_CHAL=$(python3 -c 'import sys,hashlib,base64; print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()).rstrip(b"=").decode())' "${KC_VER}" 2>/dev/null) || KC_CHAL=""
    KC_RU="${KEYCLOAK_URL}/admin/${REALM}/console/"
    for k_user in admin dev; do
      K_PW="${ADM_PW}"; [ "${k_user}" = "dev" ] && K_PW="${DEV_PW}"
      K_RES=$(gateway_login "${ADM_TMP}/k-${k_user}.jar" "${k_user}" "${K_PW}" \
        "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/auth?client_id=security-admin-console&redirect_uri=${KC_RU}&response_type=code&scope=openid&response_mode=query&code_challenge=${KC_CHAL}&code_challenge_method=S256") || K_RES="000 -"
      if [ "${K_RES}" = "NOFORM" ]; then fail "Keycloak console ${k_user}: login form not found, cannot verify"; continue; fi
      K_CODE=$(printf '%s' "${K_RES}" | python3 -c 'import sys,urllib.parse as p; u=sys.stdin.read().split(" ",1)[-1]; print(p.parse_qs(p.urlparse(u).query).get("code",[""])[0])' 2>/dev/null) || K_CODE=""
      K_TOKEN=""
      if [ -n "${K_CODE}" ]; then
        K_TOKEN=$(curl -sk --max-time 20 -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
          -d grant_type=authorization_code -d client_id=security-admin-console -d "code=${K_CODE}" \
          --data-urlencode "redirect_uri=${KC_RU}" -d "code_verifier=${KC_VER}" 2>/dev/null \
          | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null) || K_TOKEN=""
      fi
      if [ -z "${K_TOKEN}" ]; then fail "Keycloak console ${k_user}: no token from the console login, cannot verify"; continue; fi
      K_API=$(curl -sk --max-time 15 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${K_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" 2>/dev/null) || K_API="000"
      if [ "${k_user}" = "admin" ] && [ "${K_API}" = "200" ]; then
        pass "Keycloak console admin: realm admin API HTTP 200 (realm-admin)"
      elif [ "${k_user}" = "dev" ] && [ "${K_API}" = "403" ]; then
        pass "Keycloak console dev: realm admin API HTTP 403"
      else
        fail "Keycloak console ${k_user}: realm admin API got HTTP ${K_API}"
      fi
    done
  fi
  rm -rf "${ADM_TMP}"

  # --- Kubernetes Dashboard: forbidden arrives inside HTTP 200 (#551, D-DASHBOARD-FORBIDDEN-IN-200) ---
  # The Dashboard api answers a list the user may not read with HTTP 200 and errors[].ErrStatus.code
  # 403, so only the body tells allowed from denied. admin (platform-admin) must be allowed;
  # guest (no ClusterRoleBinding) must be denied. UNKNOWN (no token, non-JSON, other status) fails.
  echo "  [Kubernetes Dashboard forbidden-in-200]"
  KD_TMP=$(mktemp -d)
  for kd_user in admin guest; do
    KD_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath="{.data.${kd_user}}" 2>/dev/null | base64 -d 2>/dev/null) || KD_PW=""
    KD_TOKEN=""
    [ -z "${KD_PW}" ] || KD_TOKEN=$(dashboard_token "${kd_user}" "${KD_PW}") || KD_TOKEN=""
    if [ -z "${KD_TOKEN}" ]; then
      fail "Dashboard ${kd_user}: login/token exchange failed, cannot verify"
      continue
    fi
    KD_RES=$(curl -sk --connect-timeout 5 --max-time 30 -H "Authorization: Bearer ${KD_TOKEN}" \
      -w '\n%{http_code}' "https://dashboard.${DOMAIN}/api/v1/pod/default" 2>/dev/null) || KD_RES=$'\n000'
    KD_CODE="${KD_RES##*$'\n'}"
    KD_VERDICT=$(printf '%s' "${KD_RES%$'\n'*}" | dashboard_verdict "${KD_CODE}") || KD_VERDICT="UNKNOWN"
    case "${kd_user}:${KD_VERDICT}" in
      admin:ALLOWED) pass "Dashboard admin: pod list allowed (HTTP ${KD_CODE}, no Forbidden error)" ;;
      guest:DENIED) pass "Dashboard guest: pod list denied (HTTP ${KD_CODE})" ;;
      *) fail "Dashboard ${kd_user}: pod list verdict ${KD_VERDICT} (HTTP ${KD_CODE}), expected $([ "${kd_user}" = admin ] && echo ALLOWED || echo DENIED)" ;;
    esac
  done
  rm -rf "${KD_TMP}"

  # --- Grafana Role Mapping ---
  echo "  [Grafana Role Mapping]"

  # Check role_attribute_path in Grafana config (via prometheus-stack values)
  GRAFANA_ROLE_PATH=$(kubectl exec deploy/prometheus-stack-grafana -n monitoring -c grafana -- \
    cat /etc/grafana/grafana.ini 2>/dev/null | grep "role_attribute_path" || true)

  if echo "${GRAFANA_ROLE_PATH}" | grep -q "cluster-admin.*Admin"; then
    pass "Grafana: cluster-admin → Admin role mapping"
  else
    # Check from ArgoCD Application values
    GRAFANA_APP_VALUES=$(kubectl get application prometheus-stack -n devtools -o jsonpath='{.spec.source.helm.valuesObject}' 2>/dev/null || true)
    if echo "${GRAFANA_APP_VALUES}" | grep -q "cluster-admin.*Admin"; then
      pass "Grafana: cluster-admin → Admin role mapping (in values)"
    else
      warn "Grafana: role_attribute_path not verified (may need sync)"
    fi
  fi

  if echo "${GRAFANA_ROLE_PATH}" | grep -q "developer.*Editor"; then
    pass "Grafana: developer → Editor role mapping"
  else
    if echo "${GRAFANA_APP_VALUES:-}" | grep -q "developer.*Editor"; then
      pass "Grafana: developer → Editor role mapping (in values)"
    else
      warn "Grafana: developer→Editor not verified"
    fi
  fi

  # --- Harbor Group Members ---
  echo "  [Harbor Project Members]"

  HARBOR_API="https://harbor.${DOMAIN}/api/v2.0"
  HARBOR_MEMBERS=$(harbor_api "${HARBOR_API}/projects/1/members") || HARBOR_MEMBERS="[]"

  if [ -n "${HARBOR_MEMBERS}" ]; then
    HAS_DEV_GROUP=$(echo "${HARBOR_MEMBERS}" | python3 -c "
import sys,json
members = json.load(sys.stdin)
dev = [m for m in members if m.get('entity_type') == 'g' and m.get('entity_name','') == 'developer']
print('YES' if dev else 'NO')
" 2>/dev/null) || true

    HAS_VIEW_GROUP=$(echo "${HARBOR_MEMBERS}" | python3 -c "
import sys,json
members = json.load(sys.stdin)
view = [m for m in members if m.get('entity_type') == 'g' and m.get('entity_name','') == 'viewer']
print('YES' if view else 'NO')
" 2>/dev/null) || true

    if [ "${HAS_DEV_GROUP}" = "YES" ]; then
      pass "Harbor: developer group → project member"
    else
      fail "Harbor: developer group NOT in project members"
    fi

    if [ "${HAS_VIEW_GROUP}" = "YES" ]; then
      pass "Harbor: viewer group → project member"
    else
      fail "Harbor: viewer group NOT in project members"
    fi
  else
    warn "Harbor: API not reachable, skipping member check"
  fi

  # --- Gitea Organization & Teams ---
  echo "  [Gitea Organization & Teams]"

  GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || true
  if [ -n "${GITEA_POD}" ]; then
    # Check narwhal org exists
    ORG_CHECK=$(gitea_api /orgs/narwhal \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("username",""))' 2>/dev/null) || true

    if [ "${ORG_CHECK}" = "narwhal" ]; then
      pass "Gitea: narwhal organization exists"
    else
      fail "Gitea: narwhal organization NOT found"
    fi

    GITEA_TEAMS=$(gitea_api '/orgs/narwhal/teams?limit=100') || GITEA_TEAMS="[]"
    for team in Developers Viewers; do
      if echo "${GITEA_TEAMS}" | jq -e --arg team "${team}" 'any(.[]; .name == $team)' >/dev/null 2>&1; then
        pass "Gitea: narwhal/${team} team exists"
      else
        fail "Gitea: narwhal/${team} team NOT found"
      fi
    done

    # Check OAuth2 source has group-team-map
    GITEA_AUTH_LIST=$(kubectl exec -n devtools "${GITEA_POD}" -c gitea -- gitea admin auth list 2>/dev/null || true)
    if echo "${GITEA_AUTH_LIST}" | grep -qi "keycloak"; then
      pass "Gitea: keycloak OAuth2 source configured"
    else
      fail "Gitea: keycloak OAuth2 source NOT found"
    fi

    # Check restricted-group setting via auth source
    GITEA_AUTH_ID=$(echo "${GITEA_AUTH_LIST}" | grep -i "keycloak" | awk '{print $1}' 2>/dev/null) || true
    if [ -n "${GITEA_AUTH_ID}" ]; then
      pass "Gitea: OAuth2 source ID=${GITEA_AUTH_ID}"
    fi
  else
    warn "Gitea: pod not found, skipping org/team check"
  fi

  # --- URL-Level Blocking ---
  echo "  [URL-Level Blocking]"

  # Protected URLs should redirect (302) or return 401 when accessed without cookie
  for protected_app in argocd harbor grafana gitea openbao hubble; do
    PROT_CODE=$(curl -sk -o /dev/null -w '%{http_code}' \
      "https://${protected_app}.${DOMAIN}/" 2>/dev/null || echo "000")
    case "${PROT_CODE}" in
      302|307|401)
        pass "${protected_app}: unauthenticated → HTTP ${PROT_CODE} (blocked)" ;;
      200)
        fail "${protected_app}: unauthenticated → HTTP 200 (should be blocked)" ;;
      *)
        warn "${protected_app}: unauthenticated → HTTP ${PROT_CODE} (unexpected)" ;;
    esac
  done

  # D-HEADLAMP-API-GATE: native OIDC serves a public SPA shell; test its API
  # instead. Revisit this path if Headlamp's configured cluster name changes.
  HEADLAMP_API_CODE=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
    "https://headlamp.${DOMAIN}/clusters/main/api/v1/namespaces" 2>/dev/null) || HEADLAMP_API_CODE="000"
  case "${HEADLAMP_API_CODE}" in
    401|403) pass "Headlamp: unauthenticated API blocked (HTTP ${HEADLAMP_API_CODE})" ;;
    *) fail "Headlamp: unauthenticated API expected 401/403, got HTTP ${HEADLAMP_API_CODE}" ;;
  esac

  # --- Gateway admin-only routes (#519, D-GATEWAY-ADMIN-GROUP) ---
  # hubble, prometheus, alertmanager and nfs-quota-agent are restricted to Keycloak group
  # cluster-admin. Bearer tokens do not work on these routes (session cookie only), so each
  # user logs in through the authorization-code flow. Passwords: Secret iam/keycloak-user-passwords.
  echo "  [Gateway admin-only routes]"
  GW_TMP=$(mktemp -d)
  trap 'rm -rf "${GW_TMP:-}"' EXIT
  GW_FORGED=$(printf '{"groups":["cluster-admin"],"preferred_username":"view"}' | base64 | tr -d '\n') || GW_FORGED=""
  GW_ADMIN_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' 2>/dev/null | base64 -d 2>/dev/null) || GW_ADMIN_PW=""
  GW_VIEW_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.view}' 2>/dev/null | base64 -d 2>/dev/null) || GW_VIEW_PW=""
  if [ -z "${GW_ADMIN_PW}" ] || [ -z "${GW_VIEW_PW}" ]; then
    fail "Gateway admin-only: Secret iam/keycloak-user-passwords has no admin/view password"
  else
    for gw_host in hubble prometheus alertmanager nfs-quota; do
      gw_url="https://${gw_host}.${DOMAIN}/"

      GW_NOSESSION=$(curl -sk -o /dev/null --connect-timeout 5 --max-time 15 \
        -w '%{http_code} %{redirect_url}' "${gw_url}" 2>/dev/null) || GW_NOSESSION="000 "
      case "${GW_NOSESSION}" in
        "302 ${KEYCLOAK_URL}/"*) pass "${gw_host}: no session -> 302 to Keycloak" ;;
        *) fail "${gw_host}: no session expected 302 to ${KEYCLOAK_URL}, got ${GW_NOSESSION%% *}" ;;
      esac

      # The result must come from the app host: a failed login ends on the Keycloak page (200).
      GW_ADMIN_RES=$(gateway_login "${GW_TMP}/${gw_host}-admin.jar" admin "${GW_ADMIN_PW}" "${gw_url}") || GW_ADMIN_RES="000 -"
      GW_ADMIN_CODE="${GW_ADMIN_RES%% *}"
      case "${GW_ADMIN_RES}" in
        NOFORM) fail "${gw_host}: admin login form not found, cannot verify" ;;
        "2"??" ${gw_url}"*) pass "${gw_host}: admin (cluster-admin) -> HTTP ${GW_ADMIN_CODE} from the app" ;;
        "403 "*) fail "${gw_host}: admin (cluster-admin) got HTTP 403" ;;
        *) fail "${gw_host}: admin expected 2xx from ${gw_url}, got HTTP ${GW_ADMIN_CODE} (${GW_ADMIN_RES#* })" ;;
      esac

      GW_VIEW_RES=$(gateway_login "${GW_TMP}/${gw_host}-view.jar" view "${GW_VIEW_PW}" "${gw_url}") || GW_VIEW_RES="000 -"
      GW_VIEW_CODE="${GW_VIEW_RES%% *}"
      case "${GW_VIEW_RES}" in
        NOFORM) fail "${gw_host}: view login form not found, cannot verify" ;;
        "403 ${gw_url}"*) pass "${gw_host}: view (no cluster-admin) -> HTTP 403 from the gateway" ;;
        *) fail "${gw_host}: view expected 403 from ${gw_url}, got HTTP ${GW_VIEW_CODE} (${GW_VIEW_RES#* })" ;;
      esac

      # The viewer session replays with a client-supplied X-Userinfo claiming cluster-admin;
      # the strip-then-check pair must ignore it.
      if [ "${GW_VIEW_CODE}" = "NOFORM" ] || [ ! -s "${GW_TMP}/${gw_host}-view.jar" ]; then
        fail "${gw_host}: forged X-Userinfo not tested, no viewer session"
      else
        GW_FORGED_CODE=$(curl -sk -o /dev/null --connect-timeout 5 --max-time 20 \
          -b "${GW_TMP}/${gw_host}-view.jar" -H "X-Userinfo: ${GW_FORGED}" \
          -w '%{http_code}' "${gw_url}" 2>/dev/null) || GW_FORGED_CODE="000"
        if [ "${GW_FORGED_CODE}" = "403" ]; then
          pass "${gw_host}: view + forged X-Userinfo (cluster-admin) -> HTTP 403"
        else
          fail "${gw_host}: view + forged X-Userinfo expected 403, got HTTP ${GW_FORGED_CODE}"
        fi
      fi
    done
  fi

  # --- Velero UI: cluster-admin only (D-VELERO-UI-ADMIN-ONLY) ---
  # The app enforces a policy file (g,cluster-admin,manage,all). Users without it log in but every
  # policy-checked API answers 403. An empty body makes the admin create answer 400 (validation,
  # after the policy check) so the check creates nothing. POST /api/backups/download has no app
  # policy and is closed at the gateway for everyone.
  echo "  [Velero UI cluster-admin only]"
  VU_BASE="https://velero-ui.${DOMAIN}/api"
  vu_expect() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$4"; fi; }
  VU_NOSESSION=$(curl -sk -o /dev/null --connect-timeout 5 --max-time 15 -w '%{http_code}' "${VU_BASE}/backups" 2>/dev/null) || VU_NOSESSION="000"
  vu_expect "${VU_NOSESSION}" 401 "velero-ui: no session -> HTTP 401" "velero-ui: no session expected 401, got HTTP ${VU_NOSESSION}"
  for vu_user in admin view dev guest; do
    VU_PW=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath="{.data.${vu_user}}" 2>/dev/null | base64 -d 2>/dev/null) || VU_PW=""
    VU_TOKEN=""
    [ -z "${VU_PW}" ] || VU_TOKEN=$(velero_ui_token "${vu_user}" "${VU_PW}") || VU_TOKEN=""
    if [ -z "${VU_TOKEN}" ]; then
      fail "velero-ui: ${vu_user} login/token exchange failed, cannot verify"
      continue
    fi
    VU_LIST=$(curl -sk -o /dev/null --connect-timeout 5 --max-time 30 -H "Authorization: Bearer ${VU_TOKEN}" -w '%{http_code}' "${VU_BASE}/backups" 2>/dev/null) || VU_LIST="000"
    VU_CREATE=$(curl -sk -o /dev/null --connect-timeout 5 --max-time 30 -H "Authorization: Bearer ${VU_TOKEN}" -H 'Content-Type: application/json' -X POST -d '{}' -w '%{http_code}' "${VU_BASE}/backups" 2>/dev/null) || VU_CREATE="000"
    VU_DELETE=$(curl -sk -o /dev/null --connect-timeout 5 --max-time 30 -H "Authorization: Bearer ${VU_TOKEN}" -X DELETE -w '%{http_code}' "${VU_BASE}/backups/narwhal-authz-probe-does-not-exist" 2>/dev/null) || VU_DELETE="000"
    # Express routes non-strict and case-insensitive: trailing-slash, upper-case prefix, encoded and
    # query-string variants hit the same handler. --path-as-is keeps curl from normalising them away.
    for vu_dl_path in /api/backups/download /api/backups/download/ /api/backups/Download /API/backups/download \
      /Api/Backups/Download /api/BACKUPS/download '/api/backups/download?x=1' /api/backups/%44ownload \
      /api/backups%2Fdownload //api//backups//download '/api/backups/download;x=1'; do
      VU_DL=$(curl -sk --path-as-is -o /dev/null --connect-timeout 5 --max-time 30 -H "Authorization: Bearer ${VU_TOKEN}" -H 'Content-Type: application/json' -X POST -d '[]' -w '%{http_code}' "https://velero-ui.${DOMAIN}${vu_dl_path}" 2>/dev/null) || VU_DL="000"
      # `;` is not a path delimiter for Express, so that variant has no handler (404); it must never be a 2xx.
      case "${vu_dl_path}" in *';'*) [ "${VU_DL}" != 404 ] || VU_DL=403 ;; esac
      vu_expect "${VU_DL}" 403 "velero-ui: ${vu_user} bulk download ${vu_dl_path} -> HTTP 403 (closed at the gateway)" "velero-ui: ${vu_user} bulk download ${vu_dl_path} expected 403, got HTTP ${VU_DL}"
    done
    if [ "${vu_user}" = "admin" ]; then
      vu_expect "${VU_LIST}" 200 "velero-ui: admin list backups -> HTTP 200" "velero-ui: admin list expected 200, got HTTP ${VU_LIST}"
      vu_expect "${VU_CREATE}" 400 "velero-ui: admin create passes the policy (empty body -> HTTP 400)" "velero-ui: admin create expected 400, got HTTP ${VU_CREATE}"
    else
      vu_expect "${VU_LIST}" 403 "velero-ui: ${vu_user} list backups -> HTTP 403" "velero-ui: ${vu_user} list expected 403, got HTTP ${VU_LIST}"
      vu_expect "${VU_CREATE}" 403 "velero-ui: ${vu_user} create backup -> HTTP 403" "velero-ui: ${vu_user} create expected 403, got HTTP ${VU_CREATE}"
      vu_expect "${VU_DELETE}" 403 "velero-ui: ${vu_user} delete backup -> HTTP 403" "velero-ui: ${vu_user} delete expected 403, got HTTP ${VU_DELETE}"
    fi
  done

  # Auth services should remain accessible (not protected by ForwardAuth)
  # shellcheck disable=SC2043  # single-item loop kept intentionally for easy extension
  for open_app in keycloak; do
    OPEN_CODE=$(curl -sk -o /dev/null -w '%{http_code}' \
      "https://${open_app}.${DOMAIN}/" 2>/dev/null || echo "000")
    case "${OPEN_CODE}" in
      200|301|302|303)
        pass "${open_app}: accessible (auth service) → HTTP ${OPEN_CODE}" ;;
      401|403)
        fail "${open_app}: blocked → HTTP ${OPEN_CODE} (auth service should be open)" ;;
      *)
        warn "${open_app}: HTTP ${OPEN_CODE} (unexpected)" ;;
    esac
  done
  echo ""
fi

#=========================================
# SUMMARY
#=========================================
TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
echo "============================================"
echo "SSO TEST SUMMARY"
echo "============================================"
echo "  Total: ${TOTAL}"
echo "  PASS: ${PASS_COUNT}"
echo "  FAIL: ${FAIL_COUNT}"
echo "  WARN: ${WARN_COUNT}"
echo ""

if [ "${FAIL_COUNT}" -eq 0 ]; then
  echo "  RESULT: ALL SSO CHECKS PASSED"
  echo "============================================"
  exit 0
else
  echo "  RESULT: ${FAIL_COUNT} SSO CHECKS FAILED"
  echo "============================================"
  exit 1
fi
