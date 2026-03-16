#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

# 11c-keycloak-clients.sh
# Phase: OIDC client secrets 생성, 7개 OIDC 클라이언트 생성, audience mappers 추가, groups scope 설정
# Depends on: 11b-keycloak-realm.sh (kubernetes realm must exist)

export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Configuring Keycloak OIDC Clients ==="

# Get Keycloak pod
KEYCLOAK_POD=$(kubectl get pod -n iam -l app=keycloak -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$KEYCLOAK_POD" ]; then
  echo "ERROR: Keycloak pod not found. Ensure 11a-keycloak-operator.sh completed successfully."
  exit 1
fi

echo "Using Keycloak pod: ${KEYCLOAK_POD}"

#=========================================
# OIDC client secrets — create once, reuse on re-runs
#=========================================
if ! kubectl get secret oidc-client-secrets -n iam &>/dev/null; then
  ARGOCD_SECRET="$(generate_password)"
  GRAFANA_SECRET="$(generate_password)"
  GITEA_SECRET="$(generate_password)"
  HARBOR_SECRET="$(generate_password)"
  HEADLAMP_SECRET="$(generate_password)"
  OAUTH2_PROXY_SECRET="$(generate_password)"
  kubectl create secret generic oidc-client-secrets \
    --from-literal=argocd="${ARGOCD_SECRET}" \
    --from-literal=grafana="${GRAFANA_SECRET}" \
    --from-literal=gitea="${GITEA_SECRET}" \
    --from-literal=harbor="${HARBOR_SECRET}" \
    --from-literal=headlamp="${HEADLAMP_SECRET}" \
    --from-literal=oauth2-proxy="${OAUTH2_PROXY_SECRET}" \
    -n iam
  echo "OIDC client secrets created (secret: oidc-client-secrets / ns: iam)"
else
  ARGOCD_SECRET="$(kubectl get secret oidc-client-secrets -n iam -o jsonpath='{.data.argocd}' | base64 -d)"
  GRAFANA_SECRET="$(kubectl get secret oidc-client-secrets -n iam -o jsonpath='{.data.grafana}' | base64 -d)"
  GITEA_SECRET="$(kubectl get secret oidc-client-secrets -n iam -o jsonpath='{.data.gitea}' | base64 -d)"
  HARBOR_SECRET="$(kubectl get secret oidc-client-secrets -n iam -o jsonpath='{.data.harbor}' | base64 -d)"
  HEADLAMP_SECRET="$(kubectl get secret oidc-client-secrets -n iam -o jsonpath='{.data.headlamp}' | base64 -d)"
  OAUTH2_PROXY_SECRET="$(kubectl get secret oidc-client-secrets -n iam -o jsonpath='{.data.oauth2-proxy}' | base64 -d)"
  echo "OIDC client secrets loaded from existing secret (oidc-client-secrets / ns: iam)"
fi

#=========================================
# OAuth2-Proxy cookie secret — create once, reuse on re-runs
#=========================================
if ! kubectl get secret oauth2-proxy-secrets -n iam &>/dev/null; then
  # cookie-secret must be exactly 32 bytes (hex 16 = 32 hex chars)
  COOKIE_SECRET="$(openssl rand -hex 16)"
  kubectl create secret generic oauth2-proxy-secrets \
    --from-literal=client-id="oauth2-proxy" \
    --from-literal=cookie-secret="${COOKIE_SECRET}" \
    --from-literal=client-secret="${OAUTH2_PROXY_SECRET}" \
    -n iam
  echo "OAuth2-Proxy secrets created (secret: oauth2-proxy-secrets / ns: iam)"
fi

#=========================================
# Grafana OAuth secret — cross-namespace copy for GitOps YAML
# grafana-oauth-secret in monitoring ns: key 'client_secret'
# Used by prometheus-stack GitOps via extraSecretMounts + $__file{} provider
#=========================================
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic grafana-oauth-secret -n monitoring \
  --from-literal=client_secret="${GRAFANA_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Grafana OAuth secret created/updated (grafana-oauth-secret in monitoring)"

#=========================================
# Headlamp OIDC secret — used by GitOps YAML via config.oidc.externalSecret
# headlamp-oidc-secret in devtools ns: keys clientID, clientSecret, issuerURL, scopes
#=========================================
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic headlamp-oidc-secret -n devtools \
  --from-literal=clientID=headlamp \
  --from-literal=clientSecret="${HEADLAMP_SECRET}" \
  --from-literal=issuerURL=https://keycloak.local.narwhal.io/realms/kubernetes \
  --from-literal=scopes=openid,profile,email,groups \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Headlamp OIDC secret created/updated (headlamp-oidc-secret in devtools)"

#=========================================
# kcadm login (separate session from 11b)
#=========================================
ADMIN_USER=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}' | base64 -d)
ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d)

if ! kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 \
  --realm master \
  --user "${ADMIN_USER}" \
  --password "${ADMIN_PASS}"; then
  echo "ERROR: Failed to authenticate to Keycloak admin CLI"
  exit 1
fi

#=========================================
# Create 'groups' client scope (realm-level)
#=========================================
echo "Creating 'groups' client scope..."

kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create client-scopes -r kubernetes \
  -s name=groups \
  -s protocol=openid-connect \
  -s 'attributes."include.in.token.scope"=true' \
  -s 'attributes."display.on.consent.screen"=true' || true

# Get the groups scope ID
GROUPS_SCOPE_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get client-scopes -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.name=="groups") | .id')

if [ -n "${GROUPS_SCOPE_ID}" ]; then
  # Add group membership mapper to the groups scope
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create "client-scopes/${GROUPS_SCOPE_ID}/protocol-mappers/models" \
    -r kubernetes \
    -s name=groups \
    -s protocol=openid-connect \
    -s protocolMapper=oidc-group-membership-mapper \
    -s 'config."full.path"=false' \
    -s 'config."id.token.claim"=true' \
    -s 'config."access.token.claim"=true' \
    -s 'config."claim.name"=groups' \
    -s 'config."userinfo.token.claim"=true' || true
  echo "  -> 'groups' client scope created with mapper (ID: ${GROUPS_SCOPE_ID})"

  # Remove conflicting 'groups' claim mappers from other scopes (e.g., microprofile-jwt)
  # These mappers inject realm roles as 'groups' claim, conflicting with our group-membership mapper
  for scope_name in microprofile-jwt basic; do
    OTHER_SCOPE_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get client-scopes -r kubernetes \
      2>/dev/null | jq -r ".[] | select(.name==\"${scope_name}\") | .id")
    if [ -n "${OTHER_SCOPE_ID}" ]; then
      # Find mapper with claim.name=groups in this scope
      MAPPER_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get \
        "client-scopes/${OTHER_SCOPE_ID}/protocol-mappers/models" -r kubernetes 2>/dev/null \
        | jq -r '.[] | select(.config."claim.name"=="groups") | .id')
      if [ -n "${MAPPER_ID}" ]; then
        kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh delete \
          "client-scopes/${OTHER_SCOPE_ID}/protocol-mappers/models/${MAPPER_ID}" -r kubernetes 2>/dev/null || true
        echo "  -> Removed conflicting 'groups' mapper from ${scope_name} scope"
      fi
    fi
  done
else
  echo "WARN: Could not find groups client scope ID"
fi

#=========================================
# Create OIDC clients
#=========================================
echo "Creating OIDC clients..."

# kubernetes client (public)
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=kubernetes -s enabled=true -s publicClient=true \
  -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["*"]' -s 'webOrigins=["*"]' || true

# argocd client
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=argocd -s enabled=true -s publicClient=false \
  -s secret="${ARGOCD_SECRET}" -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["https://argocd.local.narwhal.io/*","http://localhost:8443/*"]' -s 'webOrigins=["*"]' || true

# grafana client
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=grafana -s enabled=true -s publicClient=false \
  -s secret="${GRAFANA_SECRET}" -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["https://grafana.local.narwhal.io/*","http://localhost:3000/*"]' -s 'webOrigins=["*"]' || true

# gitea client
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=gitea -s enabled=true -s publicClient=false \
  -s secret="${GITEA_SECRET}" -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["https://gitea.local.narwhal.io/*","http://localhost:3000/*"]' -s 'webOrigins=["*"]' || true

# harbor client
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=harbor -s enabled=true -s publicClient=false \
  -s secret="${HARBOR_SECRET}" -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["https://harbor.local.narwhal.io/*","http://localhost:8080/*"]' -s 'webOrigins=["*"]' || true

# headlamp client
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=headlamp -s enabled=true -s publicClient=false \
  -s secret="${HEADLAMP_SECRET}" -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["https://headlamp.local.narwhal.io/*","http://localhost:8080/*"]' -s 'webOrigins=["*"]' || true

# oauth2-proxy client (for Gateway API authentication)
kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
  -s clientId=oauth2-proxy -s enabled=true -s publicClient=false \
  -s secret="${OAUTH2_PROXY_SECRET}" -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
  -s 'redirectUris=["https://*.local.narwhal.io/*","https://oauth2-proxy.local.narwhal.io/*"]' \
  -s 'webOrigins=["*"]' || true

echo "OIDC clients created."

#=========================================
# Add audience mapper to ALL clients
#=========================================
# Each client needs an audience mapper so the JWT 'aud' claim includes the client ID.
# Without this, apps that verify the token (K8s API, ArgoCD) reject it with:
#   "expected audience X got [account]"
echo "Adding audience mappers to all clients..."

for client_name in kubernetes argocd grafana gitea harbor headlamp oauth2-proxy; do
  CLIENT_UUID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get clients -r kubernetes \
    2>/dev/null | jq -r ".[] | select(.clientId==\"${client_name}\") | .id")
  if [ -n "${CLIENT_UUID}" ]; then
    kubectl exec -n iam "${KEYCLOAK_POD}" -- bash -c "cat > /tmp/aud-mapper.json << JSONEOF
{
  \"name\": \"audience-mapper\",
  \"protocolMapper\": \"oidc-audience-mapper\",
  \"protocol\": \"openid-connect\",
  \"config\": {
    \"included.client.audience\": \"${client_name}\",
    \"id.token.claim\": \"true\",
    \"access.token.claim\": \"true\"
  }
}
JSONEOF" || true
    kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create \
      "clients/${CLIENT_UUID}/protocol-mappers/models" \
      -r kubernetes -f /tmp/aud-mapper.json 2>/dev/null || true
    echo "  -> audience mapper added to ${client_name} (aud=${client_name})"
  fi
done

#=========================================
# Assign 'groups' scope to ALL clients
#=========================================
echo "Assigning 'groups' scope to all clients..."

GROUPS_SCOPE_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get client-scopes -r kubernetes \
  2>/dev/null | jq -r '.[] | select(.name=="groups") | .id')

if [ -n "${GROUPS_SCOPE_ID}" ]; then
  for client_name in kubernetes argocd grafana gitea harbor headlamp oauth2-proxy; do
    CLIENT_UUID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get clients -r kubernetes \
      2>/dev/null | jq -r ".[] | select(.clientId==\"${client_name}\") | .id")
    if [ -n "${CLIENT_UUID}" ]; then
      kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "clients/${CLIENT_UUID}/default-client-scopes/${GROUPS_SCOPE_ID}" \
        -r kubernetes || true
      echo "  -> groups scope assigned to ${client_name}"
    fi
  done
else
  echo "WARN: groups scope ID not found, skipping assignment"
fi

#=========================================
# APISIX OIDC Client
#=========================================
echo "Creating apisix OIDC client..."

APISIX_CLIENT_SECRET=$(generate_password)

kubectl exec -n iam "${KEYCLOAK_POD}" -- \
  /opt/keycloak/bin/kcadm.sh create clients \
  -r kubernetes \
  -s clientId=apisix \
  -s enabled=true \
  -s protocol=openid-connect \
  -s publicClient=false \
  -s secret="${APISIX_CLIENT_SECRET}" \
  -s 'redirectUris=["https://argocd.local.narwhal.io/apisix/callback","https://grafana.local.narwhal.io/apisix/callback","https://gitea.local.narwhal.io/apisix/callback","https://harbor.local.narwhal.io/apisix/callback","https://headlamp.local.narwhal.io/apisix/callback","https://openbao.local.narwhal.io/apisix/callback","https://hubble.local.narwhal.io/apisix/callback","https://apisix-dashboard.local.narwhal.io/apisix/callback"]' \
  -s 'webOrigins=["https://*.local.narwhal.io"]' \
  -s standardFlowEnabled=true \
  -s directAccessGrantsEnabled=false 2>/dev/null || echo "WARN: apisix client may already exist"

# Get apisix client ID for mapper creation
APISIX_CLIENT_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- \
  /opt/keycloak/bin/kcadm.sh get clients -r kubernetes 2>/dev/null | \
  jq -r '.[] | select(.clientId=="apisix") | .id')

# Add audience mapper (required for OIDC token validation)
if [[ -n "${APISIX_CLIENT_ID}" ]]; then
  kubectl exec -n iam "${KEYCLOAK_POD}" -- \
    /opt/keycloak/bin/kcadm.sh create \
    "clients/${APISIX_CLIENT_ID}/protocol-mappers/models" \
    -r kubernetes \
    -s name=apisix-audience \
    -s protocol=openid-connect \
    -s protocolMapper=oidc-audience-mapper \
    -s 'config={"included.client.audience":"apisix","access.token.claim":"true"}' \
    2>/dev/null || echo "WARN: audience mapper may already exist"
fi

# Store APISIX OIDC credentials + session secret in K8s Secret
APISIX_SESSION_SECRET=$(openssl rand -hex 32)

kubectl create namespace platform-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic apisix-oidc-config \
  --namespace platform-system \
  --from-literal=client_id=apisix \
  --from-literal=client_secret="${APISIX_CLIENT_SECRET}" \
  --from-literal=session_secret="${APISIX_SESSION_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "apisix OIDC client created, secret stored in apisix-oidc-config"

echo "=== [11c-keycloak-clients.sh] 완료 ==="
