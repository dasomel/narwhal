#!/bin/bash
# DEPRECATED: This file has been decomposed into 11-1~11-4 scripts.
# This file is kept for reference only. Do not run directly.
# Run: 11-1-keycloak-operator.sh, 11-2-keycloak-realm.sh,
#      11-3-keycloak-clients.sh, 11-4-keycloak-apiserver.sh
set -euo pipefail

#=========================================
# Helper: generate a random password (24 chars, no special chars)
#=========================================
generate_password() {
  openssl rand -base64 16 | tr -d '=/+' | head -c 24
}

KEYCLOAK_VERSION="${KEYCLOAK_VERSION:-26.5.3}"
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-$(generate_password)}"

#=========================================
# User passwords (override via env vars for production)
# Defaults are intentionally simple for local dev convenience.
# Override with env vars for production deployments.
#=========================================
ADMIN_USER_PASSWORD="${ADMIN_USER_PASSWORD:-admin}"
DEV_USER_PASSWORD="${DEV_USER_PASSWORD:-dev}"
VIEW_USER_PASSWORD="${VIEW_USER_PASSWORD:-view}"
GUEST_USER_PASSWORD="${GUEST_USER_PASSWORD:-guest}"

echo "=== Installing Keycloak ${KEYCLOAK_VERSION} with Operator ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Wait for unified PostgreSQL cluster to be ready
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true
kubectl wait --for=condition=Ready pod -l cnpg.io/cluster=narwhal-db -n database --timeout=120s || true

#=========================================
# Install Keycloak Operator
#=========================================
echo "Installing Keycloak Operator..."
kubectl apply -f "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloaks.k8s.keycloak.org-v1.yml"
kubectl apply -f "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloakrealmimports.k8s.keycloak.org-v1.yml"
kubectl apply -f "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/kubernetes.yml" -n iam

# Wait for operator
echo "Waiting for Keycloak Operator..."
kubectl wait --for=condition=Available deployment/keycloak-operator -n iam --timeout=300s || sleep 30

#=========================================
# Create Keycloak Instance
#=========================================
echo "Creating Keycloak instance..."

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

# Create admin credentials secret
kubectl create secret generic keycloak-admin-secret -n iam \
  --from-literal=username="${KEYCLOAK_ADMIN_USER}" \
  --from-literal=password="${KEYCLOAK_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Create database credentials secret (pointing to unified narwhal-db)
# Read actual Keycloak DB password from narwhal-db-credentials (created by 07-cnpg.sh)
KEYCLOAK_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || echo "")
kubectl create secret generic keycloak-db-secret -n iam \
  --from-literal=username=keycloak \
  --from-literal=password="${KEYCLOAK_DB_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy Keycloak CR
# Note: Initial admin credentials are auto-generated and stored in {cr-name}-initial-admin secret
# DB host uses ExternalName service keycloak-db-rw → narwhal-db-rw.database.svc.cluster.local
cat <<EOF | kubectl apply -f -
apiVersion: k8s.keycloak.org/v2alpha1
kind: Keycloak
metadata:
  name: keycloak
  namespace: iam
spec:
  instances: 1
  db:
    vendor: postgres
    host: keycloak-db-rw
    port: 5432
    database: keycloak
    usernameSecret:
      name: keycloak-db-secret
      key: username
    passwordSecret:
      name: keycloak-db-secret
      key: password
  http:
    httpEnabled: true
  hostname:
    hostname: keycloak.local.narwhal.io
    strict: true
  proxy:
    headers: xforwarded
  # Opt-out from Istio ambient mesh — ztunnel HBONE breaks Traefik→Keycloak connectivity
  unsupported:
    podTemplate:
      metadata:
        labels:
          istio.io/dataplane-mode: "none"
  # NOTE: Do NOT use additionalOptions hostname-url (v1 deprecated in Keycloak 26.x)
  # hostname v2 with strict: true + proxy.headers: xforwarded ensures HTTPS issuer
  # when accessed via Traefik (X-Forwarded-Proto: https)
EOF

# Wait for Keycloak pods
echo "Waiting for Keycloak pods..."
sleep 30
kubectl wait --for=condition=Ready pod -l app=keycloak -n iam --timeout=600s || true

#=========================================
# Patch NetworkPolicy for Istio ambient mesh (HBONE port 15008)
#=========================================
# Keycloak Operator manages 'keycloak-network-policy' and may overwrite direct edits.
# Add a separate NetworkPolicy to allow HBONE (port 15008) used by Istio ambient mesh
# for mesh-to-mesh mTLS traffic. Without this, iam-namespace pods cannot reach keycloak.
# See CLAUDE.md Mistakes Log: "Istio ambient HBONE port 15008 NetworkPolicy blocking"
echo "Adding NetworkPolicy for Istio ambient mesh HBONE port 15008..."
cat <<NP_EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: keycloak-allow-hbone
  namespace: iam
spec:
  podSelector:
    matchLabels:
      app: keycloak
      app.kubernetes.io/instance: keycloak
      app.kubernetes.io/managed-by: keycloak-operator
  policyTypes:
  - Ingress
  ingress:
  - ports:
    - port: 15008
      protocol: TCP
NP_EOF

#=========================================
# Configure Keycloak for Kubernetes OIDC
#=========================================
echo "=== Configuring Keycloak for Kubernetes OIDC ==="

# Wait for Keycloak to be fully ready
sleep 30

# Get Keycloak pod
KEYCLOAK_POD=$(kubectl get pod -n iam -l app=keycloak -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$KEYCLOAK_POD" ]; then
  echo "Warning: Keycloak pod not found. Skipping OIDC configuration."
else
  # Get auto-generated admin credentials from secret
  ADMIN_USER=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}' | base64 -d)
  ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d)

  echo "Admin credentials: ${ADMIN_USER}"

  # Configure kcadm.sh credentials
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 \
    --realm master \
    --user "${ADMIN_USER}" \
    --password "${ADMIN_PASS}" || true

  # Create kubernetes realm
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create realms \
    -s realm=kubernetes \
    -s enabled=true \
    -s sslRequired=none || true

  # Create realm roles
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
    -s name=cluster-admin -s description="Kubernetes Cluster Admin" || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
    -s name=developer -s description="Kubernetes Developer" || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
    -s name=viewer -s description="Kubernetes Viewer" || true

  # Create groups
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=cluster-admin || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=developer || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=viewer || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=guest || true

  # Create users
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
    -s username=admin -s email=admin@local -s enabled=true -s emailVerified=true \
    -s firstName=Cluster -s lastName=Admin || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
    --username admin --new-password "${ADMIN_USER_PASSWORD}" || true

  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
    -s username=dev -s email=dev@local -s enabled=true -s emailVerified=true \
    -s firstName=Dev -s lastName=User || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
    --username dev --new-password "${DEV_USER_PASSWORD}" || true

  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
    -s username=view -s email=view@local -s enabled=true -s emailVerified=true \
    -s firstName=View -s lastName=User || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
    --username view --new-password "${VIEW_USER_PASSWORD}" || true

  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
    -s username=guest -s email=guest@local -s enabled=true -s emailVerified=true \
    -s firstName=Guest -s lastName=User || true
  kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
    --username guest --new-password "${GUEST_USER_PASSWORD}" || true

  # Assign users to groups
  echo "Assigning users to groups..."

  # Get user IDs
  ADMIN_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.username=="admin") | .id')
  DEV_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.username=="dev") | .id')
  VIEW_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.username=="view") | .id')
  GUEST_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.username=="guest") | .id')

  # Get group IDs
  CLUSTER_ADMIN_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.name=="cluster-admin") | .id')
  DEVELOPER_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.name=="developer") | .id')
  VIEWER_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.name=="viewer") | .id')
  GUEST_GID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
    2>/dev/null | jq -r '.[] | select(.name=="guest") | .id')

  # Add admin to cluster-admin group
  if [ -n "$ADMIN_ID" ] && [ -n "$CLUSTER_ADMIN_GID" ]; then
    kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${ADMIN_ID}/groups/${CLUSTER_ADMIN_GID}" \
      -r kubernetes -s realm=kubernetes -s userId="${ADMIN_ID}" -s groupId="${CLUSTER_ADMIN_GID}" -n || true
    echo "  -> admin added to cluster-admin"
  fi

  # Add dev to developer group
  if [ -n "$DEV_ID" ] && [ -n "$DEVELOPER_GID" ]; then
    kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${DEV_ID}/groups/${DEVELOPER_GID}" \
      -r kubernetes -s realm=kubernetes -s userId="${DEV_ID}" -s groupId="${DEVELOPER_GID}" -n || true
    echo "  -> dev added to developer"
  fi

  # Add view to viewer group
  if [ -n "$VIEW_ID" ] && [ -n "$VIEWER_GID" ]; then
    kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${VIEW_ID}/groups/${VIEWER_GID}" \
      -r kubernetes -s realm=kubernetes -s userId="${VIEW_ID}" -s groupId="${VIEWER_GID}" -n || true
    echo "  -> view added to viewer"
  fi

  # Add guest to guest group
  if [ -n "$GUEST_ID" ] && [ -n "$GUEST_GID" ]; then
    kubectl exec -n iam "${KEYCLOAK_POD}" -- /opt/keycloak/bin/kcadm.sh update "users/${GUEST_ID}/groups/${GUEST_GID}" \
      -r kubernetes -s realm=kubernetes -s userId="${GUEST_ID}" -s groupId="${GUEST_GID}" -n || true
    echo "  -> guest added to guest"
  fi

  #=========================================
  # Create 'groups' client scope (realm-level)
  #=========================================
  echo "Creating 'groups' client scope..."

  # Create the groups client scope
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

  # Create OIDC clients
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
fi

#=========================================
# Create Kubernetes RBAC for OIDC groups
#=========================================
echo "=== Creating Kubernetes RBAC for OIDC ==="

# Create dev namespace for developer workloads
kubectl create namespace dev --dry-run=client -o yaml | kubectl apply -f -

# ClusterRoleBinding for cluster-admin group → platform-admin ClusterRole
# NOTE: platform-admin ClusterRole is defined in gitops/resources/rbac-policies.yaml
# and deployed by ArgoCD at step 14. Creating CRB here with platform-admin reference
# ensures no roleRef conflict when ArgoCD syncs rbac-policies.yaml.
# roleRef is immutable — delete existing CRB first to handle any prior cluster-admin binding.
kubectl delete clusterrolebinding oidc-cluster-admin --ignore-not-found
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-cluster-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: platform-admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:cluster-admin
EOF

# RoleBinding: developer group gets edit in dev namespace
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-developer-edit
  namespace: dev
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: edit
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developer
EOF

# RoleBinding: developer group gets view in devtools and monitoring
for ns in devtools monitoring; do
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-developer-view
  namespace: ${ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developer
EOF
done

# RoleBinding: viewer group gets view in dev, devtools, and monitoring
for ns in dev devtools monitoring; do
  cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: oidc-viewer-view
  namespace: ${ns}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:viewer
EOF
done

# guest group: no K8s RBAC (web UI OIDC only)

#=========================================
# Create NodePort Service for API Server OIDC
#=========================================
echo "=== Creating Keycloak NodePort Service ==="

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: keycloak-nodeport
  namespace: iam
spec:
  type: NodePort
  selector:
    app: keycloak
  ports:
  - port: 8080
    targetPort: 8080
    nodePort: 30080
EOF

#=========================================
# Create Keycloak HTTPRoute for HTTPS access
#=========================================
# HTTPRoute must exist BEFORE OIDC verification, otherwise curl to
# https://keycloak.local.narwhal.io fails (Traefik has no route to Keycloak).
# This is also deployed via GitOps later, but we need it now for API server OIDC setup.
echo "=== Creating Keycloak HTTPRoute ==="
cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: keycloak
  namespace: iam
spec:
  parentRefs:
    - name: traefik-gateway
      namespace: platform-system
  hostnames:
    - "keycloak.local.narwhal.io"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: keycloak-service
          port: 8080
EOF

# Wait for route to become effective
sleep 5

#=========================================
# Configure API Server for OIDC
#=========================================
echo "=== Configuring API Server for OIDC ==="

DOMAIN="${DOMAIN:-local.narwhal.io}"
# K8s 1.35+ requires HTTPS for --oidc-issuer-url; HTTP causes API server crash
OIDC_ISSUER_URL="https://keycloak.${DOMAIN}/realms/kubernetes"
APISERVER_MANIFEST="/etc/kubernetes/manifests/kube-apiserver.yaml"

# Verify HTTPS OIDC endpoint is reachable before activating
echo "Verifying OIDC issuer HTTPS endpoint..."
OIDC_REACHABLE=false
for attempt in {1..15}; do
  HTTP_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "${OIDC_ISSUER_URL}/.well-known/openid-configuration" 2>/dev/null || echo "000")
  if [ "${HTTP_CODE}" = "200" ]; then
    OIDC_REACHABLE=true
    echo "OIDC endpoint reachable (HTTP ${HTTP_CODE})"
    break
  fi
  echo "OIDC endpoint not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/15..."
  sleep 10
done

if [ "${OIDC_REACHABLE}" = "false" ]; then
  echo "WARN: OIDC HTTPS endpoint not reachable. Skipping API server OIDC activation."
  echo "  Possible causes:"
  echo "    - cert-manager TLS certificate not issued yet"
  echo "    - Traefik Gateway not routing keycloak.${DOMAIN}"
  echo "    - DNS not resolving keycloak.${DOMAIN}"
  echo "  Run scripts in order: 08-platform-apps.sh → 10-dnsmasq.sh → 11-keycloak.sh"
else
  # Check if OIDC flags already exist
  if ! grep -q "oidc-issuer-url" "${APISERVER_MANIFEST}" 2>/dev/null; then
    # Extract Keycloak TLS CA certificate for API server OIDC validation
    # Use narwhal-root-ca-secret (the signing CA) rather than parsing TLS handshake,
    # which may return only the leaf cert depending on openssl version.
    echo "Extracting Keycloak TLS CA certificate from narwhal-root-ca-secret..."
    if kubectl get secret -n platform-system narwhal-root-ca-secret &>/dev/null; then
      kubectl get secret -n platform-system narwhal-root-ca-secret \
        -o jsonpath='{.data.tls\.crt}' | base64 -d > /etc/kubernetes/pki/oidc-ca.crt
      echo "CA cert extracted from narwhal-root-ca-secret"
    else
      # Fallback: extract last cert in chain (root CA) from TLS handshake
      echo "Fallback: extracting CA from TLS handshake..."
      openssl s_client -connect "keycloak.${DOMAIN}:443" -showcerts </dev/null 2>/dev/null \
        | awk '/-----BEGIN CERTIFICATE-----/{c=""} {c=c $0 "\n"} /-----END CERTIFICATE-----/{last=c} END{printf "%s", last}' \
        > /etc/kubernetes/pki/oidc-ca.crt
    fi
    # Verify the extracted CA cert is valid
    openssl x509 -in /etc/kubernetes/pki/oidc-ca.crt -noout -subject 2>/dev/null \
      && echo "CA cert verified OK" || echo "WARN: CA cert may be invalid"

    # Use yq to safely add OIDC flags to the command array
    # NOTE: Do NOT quote the URL value with shell quotes - yq handles YAML escaping
    sudo yq -i ".spec.containers[0].command += [
      \"--oidc-issuer-url=${OIDC_ISSUER_URL}\",
      \"--oidc-client-id=kubernetes\",
      \"--oidc-username-claim=preferred_username\",
      \"--oidc-groups-claim=groups\",
      \"--oidc-username-prefix=oidc:\",
      \"--oidc-groups-prefix=oidc:\",
      \"--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt\"
    ]" "${APISERVER_MANIFEST}"

    echo "OIDC flags added to API server. Waiting for restart..."

    # Wait for API server to restart (static pod manager detects manifest change)
    sleep 15
    for i in {1..30}; do
      if kubectl get nodes &>/dev/null; then
        echo "API server is ready with OIDC"
        break
      fi
      echo "Waiting for API server... ($i/30)"
      sleep 5
    done
  else
    echo "OIDC already configured in API server"
  fi

  # Apply OIDC CA and flags to master-2 and master-3 via SSH
  # Master IPs: master-2=192.168.56.11, master-3=192.168.56.12
  MASTER_IPS="${MASTER_IPS:-192.168.56.11 192.168.56.12}"
  MASTER_COUNT="${MASTER_COUNT:-3}"
  IDX=2
  for MASTER_IP in ${MASTER_IPS}; do
    if [ "${IDX}" -gt "${MASTER_COUNT}" ]; then break; fi
    echo "Applying OIDC CA + flags to master-${IDX} (${MASTER_IP})..."
    scp -o StrictHostKeyChecking=no /etc/kubernetes/pki/oidc-ca.crt \
      "vagrant@${MASTER_IP}:/tmp/oidc-ca.crt" 2>/dev/null || true
    ssh -o StrictHostKeyChecking=no "vagrant@${MASTER_IP}" "
      sudo cp /tmp/oidc-ca.crt /etc/kubernetes/pki/oidc-ca.crt
      sudo openssl x509 -in /etc/kubernetes/pki/oidc-ca.crt -noout -subject 2>/dev/null \
        && echo 'CA cert OK on master-${IDX}' || echo 'WARN: CA cert invalid on master-${IDX}'
      if ! sudo grep -q 'oidc-issuer-url' /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null; then
        sudo yq -i \".spec.containers[0].command += [
          \\\"--oidc-issuer-url=${OIDC_ISSUER_URL}\\\",
          \\\"--oidc-client-id=kubernetes\\\",
          \\\"--oidc-username-claim=preferred_username\\\",
          \\\"--oidc-groups-claim=groups\\\",
          \\\"--oidc-username-prefix=oidc:\\\",
          \\\"--oidc-groups-prefix=oidc:\\\",
          \\\"--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt\\\"
        ]\" /etc/kubernetes/manifests/kube-apiserver.yaml
        echo 'OIDC flags added to master-${IDX}'
      else
        # CA file may have been empty on initial run — restart API server to reload it
        sudo crictl stop \$(sudo crictl ps -q --name kube-apiserver 2>/dev/null) 2>/dev/null || true
        echo 'OIDC already configured on master-${IDX}, restarted to reload CA'
      fi
    " 2>/dev/null || echo "WARN: Could not apply OIDC to master-${IDX} (${MASTER_IP})"
    IDX=$((IDX + 1))
  done
fi

echo "=== Keycloak Installation Done ==="

# Print access info
echo ""
echo "=========================================="
echo "Keycloak is ready!"
echo "=========================================="
echo "Namespace: iam"
echo ""
echo "Admin Console:"
echo "  kubectl port-forward svc/keycloak-service -n iam 8080:8080"
echo "  URL: http://localhost:8080"
echo "  User: ${KEYCLOAK_ADMIN_USER} / ${KEYCLOAK_ADMIN_PASSWORD}"
echo ""
echo "Kubernetes OIDC Users:"
echo "  admin / ${ADMIN_USER_PASSWORD} (cluster-admin)"
echo "  dev   / ${DEV_USER_PASSWORD} (developer)"
echo "  view  / ${VIEW_USER_PASSWORD} (viewer)"
echo "  guest / ${GUEST_USER_PASSWORD} (guest - web UI only)"
echo ""
echo "OIDC Client Secrets (stored in secret/oidc-client-secrets -n iam):"
echo "  argocd      : ${ARGOCD_SECRET}"
echo "  grafana     : ${GRAFANA_SECRET}"
echo "  gitea       : ${GITEA_SECRET}"
echo "  harbor      : ${HARBOR_SECRET}"
echo "  headlamp    : ${HEADLAMP_SECRET}"
echo "  oauth2-proxy: ${OAUTH2_PROXY_SECRET}"
echo ""
echo "OIDC Configuration:"
echo "  Issuer: https://keycloak.local.narwhal.io/realms/kubernetes"
echo "  Client ID: kubernetes"
echo ""
echo "Test OIDC:"
echo "  TOKEN=\$(curl -s -X POST 'https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token' \\"
echo "    -d \"grant_type=password&client_id=kubernetes&username=admin&password=${ADMIN_USER_PASSWORD}\" | jq -r '.access_token')"
echo "  kubectl --token=\$TOKEN get nodes"
echo ""
kubectl get pods -n iam
kubectl get svc -n iam
