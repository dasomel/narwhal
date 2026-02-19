#!/bin/bash
set -uo pipefail

#=========================================
# SSO Integration Test Suite v2
#=========================================
# Validates the FULL SSO chain including:
# - Keycloak realm, scopes, mappers, clients
# - Token flows with groups claim per client
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

KEYCLOAK_URL="https://keycloak.local.narwhal.io"
DOMAIN="local.narwhal.io"
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  PASS  $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL  $1"; }
warn() { WARN_COUNT=$((WARN_COUNT + 1)); echo "  WARN  $1"; }

should_run() {
  [ -z "${SECTION_FILTER}" ] || [ "${SECTION_FILTER}" = "$1" ]
}

# Decode JWT payload (base64url → base64 with proper padding)
decode_jwt_payload() {
  local token="$1"
  local payload
  payload=$(echo "${token}" | cut -d. -f2)
  # base64url → base64: replace - with +, _ with /
  payload="${payload//-/+}"
  payload="${payload//_//}"
  # Add padding
  local mod=$((${#payload} % 4))
  if [ $mod -eq 2 ]; then payload="${payload}=="; fi
  if [ $mod -eq 3 ]; then payload="${payload}="; fi
  echo "${payload}" | base64 -d 2>/dev/null
}

# Get Keycloak admin token
get_admin_token() {
  local user pass
  user=$(kubectl get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.username}' 2>/dev/null | base64 -d)
  pass=$(kubectl get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.password}' 2>/dev/null | base64 -d)
  curl -sk -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "username=${user}" \
    -d "password=${pass}" \
    -d "grant_type=password" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null
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
  echo "--- [1/7] Keycloak Infrastructure ---"

  KC_PODS=$(kubectl get pods -n keycloak -l app=keycloak --no-headers 2>/dev/null | { grep -c "Running" || true; })
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
    "${KEYCLOAK_URL}/realms/kubernetes/.well-known/openid-configuration" 2>/dev/null || echo "000")
  if [ "${OIDC_CODE}" = "200" ]; then
    pass "OIDC Discovery: HTTP ${OIDC_CODE}"
  else
    fail "OIDC Discovery: HTTP ${OIDC_CODE}"
  fi

  ADMIN_TOKEN=$(get_admin_token)
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
  echo "--- [2/7] Client Scopes & Mappers ---"

  ADMIN_TOKEN=$(get_admin_token)
  if [ -z "${ADMIN_TOKEN}" ]; then
    fail "Cannot check scopes: admin auth failed"
  else
    # Check 'groups' client scope exists
    GROUPS_SCOPE=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/kubernetes/client-scopes" 2>/dev/null \
      | python3 -c "
import sys,json
scopes = json.load(sys.stdin)
matches = [s for s in scopes if s['name'] == 'groups']
if matches:
    print(f'EXISTS|{matches[0][\"id\"]}')
else:
    print('MISSING')
" 2>/dev/null)

    if echo "${GROUPS_SCOPE}" | grep -q "^EXISTS"; then
      GROUPS_SCOPE_ID=$(echo "${GROUPS_SCOPE}" | cut -d'|' -f2)
      pass "'groups' client scope: exists (${GROUPS_SCOPE_ID})"

      # Check groups mapper in scope
      MAPPER_EXISTS=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/kubernetes/client-scopes/${GROUPS_SCOPE_ID}/protocol-mappers/models" 2>/dev/null \
        | python3 -c "
import sys,json
mappers = json.load(sys.stdin)
gm = [m for m in mappers if m.get('protocolMapper') == 'oidc-group-membership-mapper']
if gm:
    claim = gm[0].get('config', {}).get('claim.name', '?')
    print(f'EXISTS|claim={claim}')
else:
    print('MISSING')
" 2>/dev/null)

      if echo "${MAPPER_EXISTS}" | grep -q "^EXISTS"; then
        pass "'groups' mapper: $(echo "${MAPPER_EXISTS}" | cut -d'|' -f2)"
      else
        fail "'groups' mapper: MISSING in groups scope"
      fi

      # Check groups scope assigned to each client
      CLIENTS_JSON=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/kubernetes/clients" 2>/dev/null)

      for client_name in kubernetes argocd grafana gitea harbor headlamp oauth2-proxy; do
        CLIENT_UUID=$(echo "${CLIENTS_JSON}" | python3 -c "
import sys,json
clients = json.load(sys.stdin)
matches = [c['id'] for c in clients if c['clientId'] == '${client_name}']
print(matches[0] if matches else '')
" 2>/dev/null)

        if [ -n "${CLIENT_UUID}" ]; then
          HAS_GROUPS=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            "${KEYCLOAK_URL}/admin/realms/kubernetes/clients/${CLIENT_UUID}/default-client-scopes" 2>/dev/null \
            | python3 -c "
import sys,json
scopes = json.load(sys.stdin)
print('YES' if any(s['name'] == 'groups' for s in scopes) else 'NO')
" 2>/dev/null)
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
  echo "--- [3/7] Clients & Redirect URIs ---"

  ADMIN_TOKEN=$(get_admin_token)
  if [ -z "${ADMIN_TOKEN}" ]; then
    fail "Cannot check clients: admin auth failed"
  else
    REALM_CHECK=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/kubernetes" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("realm",""))' 2>/dev/null)
    if [ "${REALM_CHECK}" = "kubernetes" ]; then
      pass "Realm 'kubernetes': exists"
    else
      fail "Realm 'kubernetes': not found"
    fi

    CLIENTS_JSON=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/kubernetes/clients" 2>/dev/null)

    for client in argocd grafana gitea harbor headlamp oauth2-proxy; do
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
" 2>/dev/null)

      if echo "${CLIENT_INFO}" | grep -q "^EXISTS"; then
        if echo "${CLIENT_INFO}" | grep -q "https=True"; then
          pass "Client '${client}': HTTPS redirect URIs OK"
        else
          REDIRECTS=$(echo "${CLIENT_INFO}" | sed 's/^EXISTS|[^|]*|//')
          fail "Client '${client}': no HTTPS redirect for ${DOMAIN} — ${REDIRECTS}"
        fi
      else
        fail "Client '${client}': MISSING"
      fi
    done

    # Users & Groups
    USER_COUNT=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/kubernetes/users" 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "0")
    if [ "${USER_COUNT}" -ge 2 ]; then
      pass "Users: ${USER_COUNT} (k8s-admin, developer)"
    else
      fail "Users: ${USER_COUNT} (expected >= 2)"
    fi

    GROUP_COUNT=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/kubernetes/groups" 2>/dev/null \
      | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo "0")
    if [ "${GROUP_COUNT}" -ge 2 ]; then
      pass "Groups: ${GROUP_COUNT} (cluster-admins, developers, viewers)"
    else
      fail "Groups: ${GROUP_COUNT} (expected >= 2)"
    fi
  fi
  echo ""
fi

#=========================================
# 4. TOKEN FLOWS (with groups scope)
#=========================================
if should_run "token"; then
  echo "--- [4/7] Token Flows (Password Grant) ---"

  # Test with kubernetes client (public)
  TOKEN_RESP=$(curl -sk -X POST "${KEYCLOAK_URL}/realms/kubernetes/protocol/openid-connect/token" \
    -d "client_id=kubernetes" \
    -d "username=k8s-admin" \
    -d "password=k8s-admin" \
    -d "grant_type=password" 2>/dev/null)

  ACCESS_TOKEN=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)

  if [ -n "${ACCESS_TOKEN}" ]; then
    pass "kubernetes client: token obtained"
    CLAIMS=$(decode_jwt_payload "${ACCESS_TOKEN}" \
      | python3 -c '
import sys,json
d = json.load(sys.stdin)
groups = d.get("groups", [])
print(f"groups={groups}")
' 2>/dev/null)
    if echo "${CLAIMS}" | grep -q "cluster-admins"; then
      pass "kubernetes client: groups claim includes cluster-admins"
    else
      fail "kubernetes client: groups claim missing — ${CLAIMS}"
    fi
  else
    ERROR=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("error_description",d.get("error","unknown")))' 2>/dev/null)
    fail "kubernetes client: ${ERROR}"
  fi

  # Test with argocd client (confidential, with explicit groups scope)
  ARGOCD_RESP=$(curl -sk -X POST "${KEYCLOAK_URL}/realms/kubernetes/protocol/openid-connect/token" \
    -d "client_id=argocd" \
    -d "client_secret=argocd-secret" \
    -d "username=k8s-admin" \
    -d "password=k8s-admin" \
    -d "grant_type=password" \
    -d "scope=openid profile email groups" 2>/dev/null)

  ARGOCD_TOKEN=$(echo "${ARGOCD_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)

  if [ -n "${ARGOCD_TOKEN}" ]; then
    SCOPE_IN_TOKEN=$(decode_jwt_payload "${ARGOCD_TOKEN}" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("scope",""))' 2>/dev/null)
    if echo "${SCOPE_IN_TOKEN}" | grep -q "groups"; then
      pass "argocd client: groups scope in token (${SCOPE_IN_TOKEN})"
    else
      fail "argocd client: groups scope missing from token (${SCOPE_IN_TOKEN})"
    fi
  else
    ERROR=$(echo "${ARGOCD_RESP}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("error_description",d.get("error","unknown")))' 2>/dev/null)
    fail "argocd client: ${ERROR}"
  fi

  # Test developer user
  DEV_TOKEN=$(curl -sk -X POST "${KEYCLOAK_URL}/realms/kubernetes/protocol/openid-connect/token" \
    -d "client_id=kubernetes" \
    -d "username=developer" \
    -d "password=developer" \
    -d "grant_type=password" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)

  if [ -n "${DEV_TOKEN}" ]; then
    DEV_GROUPS=$(decode_jwt_payload "${DEV_TOKEN}" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("groups",[]))' 2>/dev/null)
    if echo "${DEV_GROUPS}" | grep -q "developers"; then
      pass "developer: groups=[developers]"
    else
      fail "developer: groups missing developers — ${DEV_GROUPS}"
    fi
  else
    fail "developer: token failed"
  fi
  echo ""
fi

#=========================================
# 5. TLS SKIP-VERIFY (self-signed certs)
#=========================================
if should_run "tls"; then
  echo "--- [5/7] TLS Skip-Verify Settings ---"

  # ArgoCD: oidc.tls.insecure.skip.verify in argocd-cm
  ARGOCD_TLS=$(kubectl get configmap argocd-cm -n argocd -o jsonpath='{.data.oidc\.tls\.insecure\.skip\.verify}' 2>/dev/null)
  if [ "${ARGOCD_TLS}" = "true" ]; then
    pass "ArgoCD: oidc.tls.insecure.skip.verify=true"
  else
    fail "ArgoCD: oidc.tls.insecure.skip.verify='${ARGOCD_TLS}' (self-signed cert → need true)"
  fi

  # OAuth2-Proxy: ssl_insecure_skip_verify in config
  O2P_CFG=$(kubectl get configmap oauth2-proxy -n oauth2-proxy -o jsonpath='{.data.oauth2_proxy\.cfg}' 2>/dev/null)
  if echo "${O2P_CFG}" | grep -q "ssl_insecure_skip_verify = true"; then
    pass "OAuth2-Proxy: ssl_insecure_skip_verify=true"
  else
    fail "OAuth2-Proxy: ssl_insecure_skip_verify not set"
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
' 2>/dev/null)
      if [ "${GRAFANA_SECRET}" = "FOUND" ]; then
        pass "Grafana: tls_skip_verify_insecure in secret"
      else
        warn "Grafana: tls_skip_verify_insecure not found (may need sync)"
      fi
    else
      warn "Grafana: cannot verify TLS settings"
    fi
  fi

  # Headlamp: CA cert mounted (v0.40.0 has no -oidc-skip-issuer-tls-verify flag)
  HEADLAMP_CA_MOUNT=$(kubectl get deploy headlamp -n headlamp -o jsonpath='{.spec.template.spec.containers[0].volumeMounts[?(@.mountPath=="/etc/ssl/certs/narwhal-ca.crt")].name}' 2>/dev/null)
  if [ -n "${HEADLAMP_CA_MOUNT}" ]; then
    pass "Headlamp: CA cert mounted at /etc/ssl/certs/narwhal-ca.crt"
  else
    fail "Headlamp: CA cert not mounted (self-signed cert → SSO will fail)"
  fi

  # Harbor: oidc_verify_cert + HTTPS endpoint
  # NOTE: Use external HTTPS endpoint instead of pod IP — Istio ambient mesh
  # blocks direct pod IP access from the host (connection reset by peer)
  HARBOR_CORE_READY=$(kubectl get pods -n harbor -l app=harbor -l component=core -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
  if [ "${HARBOR_CORE_READY}" = "Running" ]; then
    HARBOR_CFG=$(curl -sk -u admin:Harbor12345 "https://harbor.local.narwhal.io/api/v2.0/configurations" 2>/dev/null)
    HARBOR_VERIFY=$(echo "${HARBOR_CFG}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("oidc_verify_cert",{}).get("value","?"))' 2>/dev/null)
    HARBOR_ENDPOINT=$(echo "${HARBOR_CFG}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("oidc_endpoint",{}).get("value",""))' 2>/dev/null)

    if [ "${HARBOR_VERIFY}" = "False" ]; then
      pass "Harbor: oidc_verify_cert=false"
    else
      fail "Harbor: oidc_verify_cert=${HARBOR_VERIFY} (need false for self-signed)"
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
  GITEA_POD=$(kubectl get pod -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -n "${GITEA_POD}" ]; then
    GITEA_AUTH=$(kubectl exec -n gitea "${GITEA_POD}" -c gitea -- gitea admin auth list 2>/dev/null | { grep -ci "keycloak\|openid" || true; })
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
  echo "--- [6/7] App OIDC Configuration ---"

  # ArgoCD: server.insecure + OIDC config
  INSECURE=$(kubectl get configmap argocd-cmd-params-cm -n argocd -o jsonpath='{.data.server\.insecure}' 2>/dev/null)
  if [ "${INSECURE}" = "true" ]; then
    pass "ArgoCD: server.insecure=true (cmd-params-cm)"
  else
    fail "ArgoCD: server.insecure='${INSECURE}' (should be true)"
  fi

  OIDC_CFG=$(kubectl get configmap argocd-cm -n argocd -o jsonpath='{.data.oidc\.config}' 2>/dev/null)
  if echo "${OIDC_CFG}" | grep -q "https://keycloak"; then
    pass "ArgoCD: OIDC issuer HTTPS"
  elif echo "${OIDC_CFG}" | grep -q "keycloak"; then
    fail "ArgoCD: OIDC issuer HTTP (should be HTTPS)"
  else
    fail "ArgoCD: OIDC not configured"
  fi

  ARGOCD_URL=$(kubectl get configmap argocd-cm -n argocd -o jsonpath='{.data.url}' 2>/dev/null)
  if echo "${ARGOCD_URL}" | grep -q "^https://"; then
    pass "ArgoCD: URL=${ARGOCD_URL}"
  else
    fail "ArgoCD: URL='${ARGOCD_URL}' (should be https://)"
  fi

  # OAuth2-Proxy: OIDC config
  O2P_CFG=$(kubectl get configmap oauth2-proxy -n oauth2-proxy -o jsonpath='{.data.oauth2_proxy\.cfg}' 2>/dev/null)
  if echo "${O2P_CFG}" | grep -q "oidc_issuer_url.*https://keycloak"; then
    pass "OAuth2-Proxy: OIDC issuer HTTPS"
  else
    fail "OAuth2-Proxy: OIDC issuer not HTTPS"
  fi

  if echo "${O2P_CFG}" | grep -q 'redirect_url.*https://'; then
    pass "OAuth2-Proxy: redirect_url HTTPS"
  else
    fail "OAuth2-Proxy: redirect_url not HTTPS"
  fi

  # Headlamp: OIDC issuer
  HEADLAMP_ISSUER=$(kubectl get secret oidc -n headlamp -o jsonpath='{.data.issuerURL}' 2>/dev/null | base64 -d 2>/dev/null)
  if echo "${HEADLAMP_ISSUER}" | grep -q "^https://"; then
    pass "Headlamp: issuerURL=${HEADLAMP_ISSUER}"
  else
    fail "Headlamp: issuerURL='${HEADLAMP_ISSUER}' (should be https://)"
  fi

  # K8s API Server OIDC
  if sudo grep -q "oidc-issuer-url" /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null; then
    OIDC_URL=$(sudo grep "oidc-issuer-url" /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null | head -1)
    if echo "${OIDC_URL}" | grep -q "https://"; then
      pass "K8s API: OIDC HTTPS configured"
    else
      fail "K8s API: OIDC HTTP (must be HTTPS for K8s 1.35+)"
    fi
  else
    warn "K8s API: OIDC not configured"
  fi
  echo ""
fi

#=========================================
# 7. HTTPS ENDPOINTS & SSO REDIRECTS
#=========================================
if should_run "endpoints"; then
  echo "--- [7/7] HTTPS Endpoints & SSO Redirects ---"

  for app in argocd grafana gitea keycloak headlamp harbor oauth2-proxy; do
    if [ "${app}" = "oauth2-proxy" ]; then
      CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://${app}.${DOMAIN}/ping" 2>/dev/null || echo "000")
    else
      CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://${app}.${DOMAIN}" 2>/dev/null || echo "000")
    fi
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

  # OAuth2-Proxy SSO redirect test
  O2P_REDIRECT=$(curl -sk -D- -o /dev/null \
    "https://oauth2-proxy.${DOMAIN}/oauth2/start?rd=https://headlamp.${DOMAIN}" 2>/dev/null \
    | grep -i "location:" | head -1 || echo "")
  if echo "${O2P_REDIRECT}" | grep -q "keycloak.${DOMAIN}"; then
    pass "OAuth2-Proxy SSO redirect: → Keycloak"
  else
    fail "OAuth2-Proxy SSO redirect: unexpected — ${O2P_REDIRECT}"
  fi
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
