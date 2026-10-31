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

# Get Keycloak admin token
get_admin_token() {
  local user pass
  user=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}' 2>/dev/null | base64 -d)
  pass=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' 2>/dev/null | base64 -d)
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
  echo "--- [2/9] Client Scopes & Mappers ---"

  ADMIN_TOKEN=$(get_admin_token)
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
" 2>/dev/null)

    if echo "${GROUPS_SCOPE}" | grep -q "^EXISTS"; then
      GROUPS_SCOPE_ID=$(echo "${GROUPS_SCOPE}" | cut -d'|' -f2)
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
" 2>/dev/null)

      if echo "${MAPPER_EXISTS}" | grep -q "^EXISTS"; then
        pass "'groups' mapper: $(echo "${MAPPER_EXISTS}" | cut -d'|' -f2)"
      else
        fail "'groups' mapper: MISSING in groups scope"
      fi

      # Check groups scope assigned to each client
      CLIENTS_JSON=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" 2>/dev/null)

      for client_name in kubernetes argocd grafana gitea harbor headlamp; do
        CLIENT_UUID=$(echo "${CLIENTS_JSON}" | python3 -c "
import sys,json
clients = json.load(sys.stdin)
matches = [c['id'] for c in clients if c['clientId'] == '${client_name}']
print(matches[0] if matches else '')
" 2>/dev/null)

        if [ -n "${CLIENT_UUID}" ]; then
          HAS_GROUPS=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/default-client-scopes" 2>/dev/null \
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
  echo "--- [3/9] Clients & Redirect URIs ---"

  ADMIN_TOKEN=$(get_admin_token)
  if [ -z "${ADMIN_TOKEN}" ]; then
    fail "Cannot check clients: admin auth failed"
  else
    REALM_CHECK=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("realm",""))' 2>/dev/null)
    if [ "${REALM_CHECK}" = "${REALM}" ]; then
      pass "Realm '${REALM}': exists"
    else
      fail "Realm '${REALM}': not found"
    fi

    CLIENTS_JSON=$(curl -sk -H "Authorization: Bearer ${ADMIN_TOKEN}" \
      "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" 2>/dev/null)

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
  ADMIN_USER_PASS=$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' 2>/dev/null | base64 -d)
  TOKEN_RESP=$(curl -sk -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
    -d "client_id=kubernetes" \
    -d "username=admin" \
    --data-urlencode "password=${ADMIN_USER_PASS}" \
    -d "grant_type=password" 2>/dev/null)

  ACCESS_TOKEN=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)
  ERROR_CODE=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("error","unknown"))' 2>/dev/null)
  ERROR_DESC=$(echo "${TOKEN_RESP}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("error_description",""))' 2>/dev/null)

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

  ADMIN_TOKEN=$(get_admin_token)
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

  # Headlamp: combined CA cert mounted (v0.40.0 has no -oidc-skip-issuer-tls-verify flag)
  # initContainer merges system bundle + custom CA → /etc/ssl/certs/ca-certificates.crt
  HEADLAMP_CA_MOUNT=$(kubectl get deploy headlamp -n devtools -o jsonpath='{.spec.template.spec.containers[0].volumeMounts[?(@.mountPath=="/etc/ssl/certs/ca-certificates.crt")].name}' 2>/dev/null)
  if [ "${HEADLAMP_CA_MOUNT}" = "combined-certs" ]; then
    pass "Headlamp: combined CA bundle mounted at /etc/ssl/certs/ca-certificates.crt (volume: combined-certs)"
  else
    fail "Headlamp: combined-certs volume not mounted at /etc/ssl/certs/ca-certificates.crt (self-signed cert → SSO will fail)"
  fi

  # Harbor: oidc_verify_cert + HTTPS endpoint
  # NOTE: Use external HTTPS endpoint instead of pod IP — Istio ambient mesh
  # blocks direct pod IP access from the host (connection reset by peer)
  HARBOR_CORE_READY=$(kubectl get pods -n devtools -l app=harbor -l component=core -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
  if [ "${HARBOR_CORE_READY}" = "Running" ]; then
    HARBOR_CFG=$(harbor_api "https://harbor.${DOMAIN}/api/v2.0/configurations") || HARBOR_CFG="{}"
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
  GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
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
  INSECURE=$(kubectl get configmap argocd-cmd-params-cm -n devtools -o jsonpath='{.data.server\.insecure}' 2>/dev/null)
  if [ "${INSECURE}" = "true" ]; then
    pass "ArgoCD: server.insecure=true (cmd-params-cm)"
  else
    fail "ArgoCD: server.insecure='${INSECURE}' (should be true)"
  fi

  OIDC_CFG=$(kubectl get configmap argocd-cm -n devtools -o jsonpath='{.data.oidc\.config}' 2>/dev/null)
  if echo "${OIDC_CFG}" | grep -q "https://keycloak"; then
    pass "ArgoCD: OIDC issuer HTTPS"
  elif echo "${OIDC_CFG}" | grep -q "keycloak"; then
    fail "ArgoCD: OIDC issuer HTTP (should be HTTPS)"
  else
    fail "ArgoCD: OIDC not configured"
  fi

  ARGOCD_URL=$(kubectl get configmap argocd-cm -n devtools -o jsonpath='{.data.url}' 2>/dev/null)
  if echo "${ARGOCD_URL}" | grep -q "^https://"; then
    pass "ArgoCD: URL=${ARGOCD_URL}"
  else
    fail "ArgoCD: URL='${ARGOCD_URL}' (should be https://)"
  fi

  # Headlamp: OIDC issuer
  HEADLAMP_ISSUER=$(kubectl get secret oidc -n devtools -o jsonpath='{.data.issuerURL}' 2>/dev/null | base64 -d 2>/dev/null)
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

  # dev → developer: cluster-wide read (no nodes, no secrets), writes only in dev
  test_rbac "dev create pods in dev" dev developer "yes" create pods -n dev
  test_rbac "dev get pods in devtools" dev developer "yes" get pods -n devtools
  test_rbac "dev get pods in monitoring" dev developer "yes" get pods -n monitoring
  test_rbac "dev create pods in kube-system" dev developer "no" create pods -n kube-system
  test_rbac "dev get secrets in kube-system" dev developer "no" get secrets -n kube-system
  test_rbac "dev get nodes" dev developer "no" get nodes

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

  ADMIN_TOKEN=$(get_admin_token)

  # --- ArgoCD RBAC ---
  echo "  [ArgoCD RBAC]"

  # Get ArgoCD admin password for API auth
  ARGOCD_PASS=$(kubectl -n devtools get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" 2>/dev/null | base64 -d 2>/dev/null)
  ARGOCD_API="https://argocd.${DOMAIN}"

  # ArgoCD session token for admin (local admin)
  ARGOCD_SESSION=$(curl -sk -X POST "${ARGOCD_API}/api/v1/session" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"${ARGOCD_PASS}\"}" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)

  if [ -n "${ARGOCD_SESSION}" ]; then
    # Check RBAC policy has guest→role:none
    RBAC_CSV=$(kubectl get configmap argocd-rbac-cm -n devtools -o jsonpath='{.data.policy\.csv}' 2>/dev/null)
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
" 2>/dev/null)

    HAS_VIEW_GROUP=$(echo "${HARBOR_MEMBERS}" | python3 -c "
import sys,json
members = json.load(sys.stdin)
view = [m for m in members if m.get('entity_type') == 'g' and m.get('entity_name','') == 'viewer']
print('YES' if view else 'NO')
" 2>/dev/null)

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

  GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -n "${GITEA_POD}" ]; then
    # Check narwhal org exists
    ORG_CHECK=$(kubectl exec -n devtools "${GITEA_POD}" -- \
      curl -sf "http://localhost:3000/api/v1/orgs/narwhal" -u "gitea-admin:gitea-admin" 2>/dev/null \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("username",""))' 2>/dev/null)

    if [ "${ORG_CHECK}" = "narwhal" ]; then
      pass "Gitea: narwhal organization exists"
    else
      fail "Gitea: narwhal organization NOT found"
    fi

    # Check OAuth2 source has group-team-map
    GITEA_AUTH_LIST=$(kubectl exec -n devtools "${GITEA_POD}" -c gitea -- gitea admin auth list 2>/dev/null || true)
    if echo "${GITEA_AUTH_LIST}" | grep -qi "keycloak"; then
      pass "Gitea: keycloak OAuth2 source configured"
    else
      fail "Gitea: keycloak OAuth2 source NOT found"
    fi

    # Check restricted-group setting via auth source
    GITEA_AUTH_ID=$(echo "${GITEA_AUTH_LIST}" | grep -i "keycloak" | awk '{print $1}' 2>/dev/null)
    if [ -n "${GITEA_AUTH_ID}" ]; then
      pass "Gitea: OAuth2 source ID=${GITEA_AUTH_ID}"
    fi
  else
    warn "Gitea: pod not found, skipping org/team check"
  fi

  # --- URL-Level Blocking ---
  echo "  [URL-Level Blocking]"

  # Protected URLs should redirect (302) or return 401 when accessed without cookie
  for protected_app in argocd harbor headlamp grafana gitea openbao hubble; do
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
