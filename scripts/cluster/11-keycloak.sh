#!/bin/bash
set -euo pipefail

KEYCLOAK_VERSION="${KEYCLOAK_VERSION:-26.5.3}"
KEYCLOAK_ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}"
KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

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
kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloaks.k8s.keycloak.org-v1.yml
kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/keycloakrealmimports.k8s.keycloak.org-v1.yml
kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${KEYCLOAK_VERSION}/kubernetes/kubernetes.yml -n keycloak

# Wait for operator
echo "Waiting for Keycloak Operator..."
kubectl wait --for=condition=Available deployment/keycloak-operator -n keycloak --timeout=300s || sleep 30

#=========================================
# Create Keycloak Instance
#=========================================
echo "Creating Keycloak instance..."

# Create admin credentials secret
kubectl create secret generic keycloak-admin-secret -n keycloak \
  --from-literal=username="${KEYCLOAK_ADMIN_USER}" \
  --from-literal=password="${KEYCLOAK_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Create database credentials secret (pointing to unified narwhal-db)
kubectl create secret generic keycloak-db-secret -n keycloak \
  --from-literal=username=keycloak \
  --from-literal=password=keycloak-db-password \
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy Keycloak CR
# Note: Initial admin credentials are auto-generated and stored in {cr-name}-initial-admin secret
# DB host uses ExternalName service keycloak-db-rw → narwhal-db-rw.database.svc.cluster.local
cat <<EOF | kubectl apply -f -
apiVersion: k8s.keycloak.org/v2alpha1
kind: Keycloak
metadata:
  name: keycloak
  namespace: keycloak
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
    strict: false
  proxy:
    headers: xforwarded
  additionalOptions:
    - name: hostname-url
      # K8s 1.35+ requires HTTPS for OIDC issuer URL
      value: "https://keycloak.local.narwhal.io"
EOF

# Wait for Keycloak pods
echo "Waiting for Keycloak pods..."
sleep 30
kubectl wait --for=condition=Ready pod -l app=keycloak -n keycloak --timeout=600s || true

#=========================================
# Configure Keycloak for Kubernetes OIDC
#=========================================
echo "=== Configuring Keycloak for Kubernetes OIDC ==="

# Wait for Keycloak to be fully ready
sleep 30

# Get Keycloak pod
KEYCLOAK_POD=$(kubectl get pod -n keycloak -l app=keycloak -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$KEYCLOAK_POD" ]; then
  echo "Warning: Keycloak pod not found. Skipping OIDC configuration."
else
  # Get auto-generated admin credentials from secret
  ADMIN_USER=$(kubectl get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.username}' | base64 -d)
  ADMIN_PASS=$(kubectl get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.password}' | base64 -d)

  echo "Admin credentials: ${ADMIN_USER}"

  # Configure kcadm.sh credentials
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 \
    --realm master \
    --user "${ADMIN_USER}" \
    --password "${ADMIN_PASS}" || true

  # Create kubernetes realm
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create realms \
    -s realm=kubernetes \
    -s enabled=true \
    -s sslRequired=none || true

  # Create realm roles
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
    -s name=cluster-admin -s description="Kubernetes Cluster Admin" || true
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
    -s name=developer -s description="Kubernetes Developer" || true
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create roles -r kubernetes \
    -s name=viewer -s description="Kubernetes Viewer" || true

  # Create groups
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=cluster-admins || true
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=developers || true
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create groups -r kubernetes \
    -s name=viewers || true

  # Create users
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
    -s username=k8s-admin -s email=k8s-admin@local -s enabled=true \
    -s firstName=Kubernetes -s lastName=Admin || true
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
    --username k8s-admin --new-password k8s-admin || true

  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create users -r kubernetes \
    -s username=developer -s email=developer@local -s enabled=true \
    -s firstName=Dev -s lastName=User || true
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh set-password -r kubernetes \
    --username developer --new-password developer || true

  # Assign users to groups
  echo "Assigning users to groups..."

  # Get user IDs
  K8S_ADMIN_ID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
    -q username=k8s-admin --fields id --format csv --noquotes 2>/dev/null | tail -1)
  DEVELOPER_ID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get users -r kubernetes \
    -q username=developer --fields id --format csv --noquotes 2>/dev/null | tail -1)

  # Get group IDs
  CLUSTER_ADMINS_ID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
    -q search=cluster-admins --fields id --format csv --noquotes 2>/dev/null | tail -1)
  DEVELOPERS_ID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get groups -r kubernetes \
    -q search=developers --fields id --format csv --noquotes 2>/dev/null | tail -1)

  # Add k8s-admin to cluster-admins group
  if [ -n "$K8S_ADMIN_ID" ] && [ -n "$CLUSTER_ADMINS_ID" ]; then
    kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh update users/${K8S_ADMIN_ID}/groups/${CLUSTER_ADMINS_ID} \
      -r kubernetes -s realm=kubernetes -s userId=${K8S_ADMIN_ID} -s groupId=${CLUSTER_ADMINS_ID} -n || true
    echo "  -> k8s-admin added to cluster-admins"
  fi

  # Add developer to developers group
  if [ -n "$DEVELOPER_ID" ] && [ -n "$DEVELOPERS_ID" ]; then
    kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh update users/${DEVELOPER_ID}/groups/${DEVELOPERS_ID} \
      -r kubernetes -s realm=kubernetes -s userId=${DEVELOPER_ID} -s groupId=${DEVELOPERS_ID} -n || true
    echo "  -> developer added to developers"
  fi

  #=========================================
  # Create 'groups' client scope (realm-level)
  #=========================================
  echo "Creating 'groups' client scope..."

  # Create the groups client scope
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create client-scopes -r kubernetes \
    -s name=groups \
    -s protocol=openid-connect \
    -s 'attributes."include.in.token.scope"=true' \
    -s 'attributes."display.on.consent.screen"=true' || true

  # Get the groups scope ID
  GROUPS_SCOPE_ID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get client-scopes -r kubernetes \
    -q name=groups --fields id --format csv --noquotes 2>/dev/null | tail -1)

  if [ -n "${GROUPS_SCOPE_ID}" ]; then
    # Add group membership mapper to the groups scope
    kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create client-scopes/${GROUPS_SCOPE_ID}/protocol-mappers/models \
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
  else
    echo "WARN: Could not find groups client scope ID"
  fi

  # Create OIDC clients
  echo "Creating OIDC clients..."

  # kubernetes client (public)
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=kubernetes -s enabled=true -s publicClient=true \
    -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["*"]' -s 'webOrigins=["*"]' || true

  # argocd client
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=argocd -s enabled=true -s publicClient=false \
    -s secret=argocd-secret -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["https://argocd.local.narwhal.io/*","http://localhost:8443/*"]' -s 'webOrigins=["*"]' || true

  # grafana client
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=grafana -s enabled=true -s publicClient=false \
    -s secret=grafana-secret -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["https://grafana.local.narwhal.io/*","http://localhost:3000/*"]' -s 'webOrigins=["*"]' || true

  # gitea client
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=gitea -s enabled=true -s publicClient=false \
    -s secret=gitea-secret -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["https://gitea.local.narwhal.io/*","http://localhost:3000/*"]' -s 'webOrigins=["*"]' || true

  # harbor client
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=harbor -s enabled=true -s publicClient=false \
    -s secret=harbor-secret -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["https://harbor.local.narwhal.io/*","http://localhost:8080/*"]' -s 'webOrigins=["*"]' || true

  # headlamp client
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=headlamp -s enabled=true -s publicClient=false \
    -s secret=headlamp-secret -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["https://headlamp.local.narwhal.io/*","http://localhost:8080/*"]' -s 'webOrigins=["*"]' || true

  # oauth2-proxy client (for Gateway API authentication)
  kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh create clients -r kubernetes \
    -s clientId=oauth2-proxy -s enabled=true -s publicClient=false \
    -s secret=oauth2-proxy-secret -s directAccessGrantsEnabled=true -s standardFlowEnabled=true \
    -s 'redirectUris=["https://*.local.narwhal.io/*","https://oauth2-proxy.local.narwhal.io/*"]' \
    -s 'webOrigins=["*"]' || true

  echo "OIDC clients created."

  #=========================================
  # Assign 'groups' scope to ALL clients
  #=========================================
  echo "Assigning 'groups' scope to all clients..."

  GROUPS_SCOPE_ID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get client-scopes -r kubernetes \
    -q name=groups --fields id --format csv --noquotes 2>/dev/null | tail -1)

  if [ -n "${GROUPS_SCOPE_ID}" ]; then
    for client_name in kubernetes argocd grafana gitea harbor headlamp oauth2-proxy; do
      CLIENT_UUID=$(kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh get clients -r kubernetes \
        -q clientId=${client_name} --fields id --format csv --noquotes 2>/dev/null | tail -1)
      if [ -n "${CLIENT_UUID}" ]; then
        kubectl exec -n keycloak ${KEYCLOAK_POD} -- /opt/keycloak/bin/kcadm.sh update clients/${CLIENT_UUID}/default-client-scopes/${GROUPS_SCOPE_ID} \
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

# ClusterRoleBinding for cluster-admins group
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-cluster-admins
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:cluster-admins
EOF

# ClusterRoleBinding for developers group
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-developers
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: edit
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:developers
EOF

# ClusterRoleBinding for viewers group
cat <<EOF | kubectl apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: oidc-viewers
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
subjects:
- apiGroup: rbac.authorization.k8s.io
  kind: Group
  name: oidc:viewers
EOF

#=========================================
# Create NodePort Service for API Server OIDC
#=========================================
echo "=== Creating Keycloak NodePort Service ==="

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: keycloak-nodeport
  namespace: keycloak
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
# Configure API Server for OIDC
#=========================================
echo "=== Configuring API Server for OIDC ==="

# K8s 1.35+ requires HTTPS for --oidc-issuer-url; HTTP causes API server crash
OIDC_ISSUER_URL="https://keycloak.local.narwhal.io/realms/kubernetes"
APISERVER_MANIFEST="/etc/kubernetes/manifests/kube-apiserver.yaml"

# Verify HTTPS OIDC endpoint is reachable before activating
# K8s 1.35+ will crash API server if the HTTPS URL is unreachable
echo "Verifying OIDC issuer HTTPS endpoint..."
OIDC_REACHABLE=false
for attempt in {1..10}; do
  HTTP_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "${OIDC_ISSUER_URL}/.well-known/openid-configuration" 2>/dev/null || echo "000")
  if [ "${HTTP_CODE}" = "200" ]; then
    OIDC_REACHABLE=true
    echo "OIDC endpoint reachable (HTTP ${HTTP_CODE})"
    break
  fi
  echo "OIDC endpoint not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/10..."
  sleep 10
done

if [ "${OIDC_REACHABLE}" = "false" ]; then
  echo "WARN: OIDC HTTPS endpoint not reachable. Skipping API server OIDC activation."
  echo "  Possible causes:"
  echo "    - cert-manager TLS certificate not issued yet"
  echo "    - Traefik Gateway not routing keycloak.local.narwhal.io"
  echo "    - DNS not resolving keycloak.local.narwhal.io"
  echo "  Run scripts in order: 08-platform-apps.sh → 10-dnsmasq.sh → 11-keycloak.sh"
else
  # Check if OIDC flags already exist
  if ! grep -q "oidc-issuer-url" ${APISERVER_MANIFEST} 2>/dev/null; then
    # Use yq to safely add OIDC flags to the command array
    sudo yq -i '.spec.containers[0].command += [
      "--oidc-issuer-url='"${OIDC_ISSUER_URL}"'",
      "--oidc-client-id=kubernetes",
      "--oidc-username-claim=preferred_username",
      "--oidc-groups-claim=groups",
      "--oidc-username-prefix=oidc:",
      "--oidc-groups-prefix=oidc:"
    ]' ${APISERVER_MANIFEST}

    echo "OIDC flags added to API server. Waiting for restart..."

    # Wait for API server to restart
    sleep 10
    for i in {1..30}; do
      if kubectl get nodes &>/dev/null; then
        echo "API server is ready"
        break
      fi
      echo "Waiting for API server... ($i/30)"
      sleep 5
    done
  else
    echo "OIDC already configured in API server"
  fi
fi

echo "=== Keycloak Installation Done ==="

# Print access info
echo ""
echo "=========================================="
echo "Keycloak is ready!"
echo "=========================================="
echo "Namespace: keycloak"
echo ""
echo "Admin Console:"
echo "  kubectl port-forward svc/keycloak-service -n keycloak 8080:8080"
echo "  URL: http://localhost:8080"
echo "  User: ${KEYCLOAK_ADMIN_USER} / ${KEYCLOAK_ADMIN_PASSWORD}"
echo ""
echo "Kubernetes OIDC Users:"
echo "  k8s-admin / k8s-admin (cluster-admin)"
echo "  developer / developer (edit)"
echo ""
echo "OIDC Configuration:"
echo "  Issuer: https://keycloak.local.narwhal.io/realms/kubernetes"
echo "  Client ID: kubernetes"
echo ""
echo "Test OIDC:"
echo "  TOKEN=\$(curl -s -X POST 'https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token' \\"
echo "    -d 'grant_type=password&client_id=kubernetes&username=k8s-admin&password=k8s-admin' | jq -r '.access_token')"
echo "  kubectl --token=\$TOKEN get nodes"
echo ""
kubectl get pods -n keycloak
kubectl get svc -n keycloak
