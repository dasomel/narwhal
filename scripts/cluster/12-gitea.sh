#!/bin/bash
set -euo pipefail

GITEA_VERSION="${GITEA_VERSION:-v1.25.4}"

echo "=== Installing Gitea ${GITEA_VERSION} ==="

# Helper: generate a random 24-char alphanumeric password
generate_password() {
  openssl rand -base64 16 | tr -d '=/+' | head -c 24
}

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Wait for API server to be reachable (may restart under memory pressure)
echo "Waiting for API server..."
for i in {1..30}; do
  if kubectl get nodes &>/dev/null; then
    break
  fi
  echo "API server not ready, retrying... (${i}/30)"
  sleep 10
done

# Create namespace
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -

# Add Gitea Helm repo
helm repo add gitea-charts https://dl.gitea.com/charts/
helm repo update

# Wait for unified PostgreSQL cluster (narwhal-db) to be ready
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true
kubectl wait --for=condition=Ready pod -l cnpg.io/poolerName=narwhal-db-pooler-rw -n database --timeout=120s || true

# Keycloak OIDC configuration
KEYCLOAK_URL="${KEYCLOAK_URL:-https://keycloak.local.narwhal.io}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-kubernetes}"

# Gitea admin password — create Secret on first run, reuse on re-run
if ! kubectl get secret gitea-admin -n devtools &>/dev/null; then
  GITEA_ADMIN_PASS=$(generate_password)
  kubectl create secret generic gitea-admin \
    --from-literal=admin-password="${GITEA_ADMIN_PASS}" \
    -n devtools
else
  GITEA_ADMIN_PASS=$(kubectl get secret gitea-admin -n devtools \
    -o jsonpath='{.data.admin-password}' | base64 -d)
fi

# DB password — provided by 07-cnpg.sh via narwhal-db-credentials Secret
GITEA_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.gitea-password}' | base64 -d)

# Install Gitea with Keycloak OIDC
GITEA_CHART_VERSION="${GITEA_CHART_VERSION:-12.5.0}"

helm upgrade --install gitea gitea-charts/gitea \
  --namespace devtools \
  --version "${GITEA_CHART_VERSION}" \
  --set image.tag="${GITEA_VERSION#v}" \
  --set gitea.admin.username=gitea-admin \
  --set gitea.admin.password="${GITEA_ADMIN_PASS}" \
  --set gitea.admin.email=admin@local \
  --set postgresql.enabled=false \
  --set postgresql-ha.enabled=false \
  --set gitea.config.database.DB_TYPE=postgres \
  --set gitea.config.database.HOST=gitea-db-rw:5432 \
  --set gitea.config.database.NAME=gitea \
  --set gitea.config.database.USER=gitea \
  --set gitea.config.database.PASSWD="${GITEA_DB_PASS}" \
  --set gitea.config.server.ROOT_URL=https://gitea.local.narwhal.io \
  --set gitea.config.oauth2_client.ENABLE_AUTO_REGISTRATION=true \
  --set gitea.config.oauth2_client.ACCOUNT_LINKING=auto \
  --set gitea.config.oauth2_client.UPDATE_AVATAR=true \
  --set gitea.config.oauth2_client.OPENID_CONNECT_SCOPES="openid profile email groups" \
  --set persistence.enabled=true \
  --set persistence.storageClass=nfs-csi \
  --set persistence.size=10Gi \
  --set redis-cluster.enabled=false \
  --set redis.enabled=false \
  --set valkey-cluster.enabled=false \
  --set valkey.enabled=true \
  --set "extraVolumes[0].name=narwhal-ca" \
  --set "extraVolumes[0].secret.secretName=narwhal-ca-cert" \
  --set "extraContainerVolumeMounts[0].name=narwhal-ca" \
  --set "extraContainerVolumeMounts[0].mountPath=/etc/ssl/certs/narwhal-ca.crt" \
  --set "extraContainerVolumeMounts[0].subPath=ca.crt" \
  --set "extraContainerVolumeMounts[0].readOnly=true" \
  --timeout=600s || echo "WARN: Gitea install timed out, continuing..."

# Opt Gitea out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment gitea -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

# Patch Valkey NetworkPolicy for Istio ambient mesh (HBONE port 15008)
if kubectl get networkpolicy gitea-valkey -n devtools &>/dev/null; then
  echo "Patching gitea-valkey NetworkPolicy for Istio ambient mesh (HBONE port 15008)..."
  kubectl patch networkpolicy gitea-valkey -n devtools --type='json' \
    -p='[{"op": "add", "path": "/spec/ingress/0/ports/-", "value": {"port": 15008, "protocol": "TCP"}}]' || true
fi

# Configure Keycloak OAuth2 provider via API
echo "Configuring Gitea OAuth2 provider..."
sleep 10

# Wait for Gitea to be ready
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea -n devtools --timeout=300s || true

# Create OAuth2 source via Gitea API
GITEA_POD=$(kubectl get pod -n devtools -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "${GITEA_POD}" ]; then
  # Create narwhal organization for group→team mapping
  echo "Creating narwhal organization..."
  kubectl exec -n devtools "${GITEA_POD}" -- \
    curl -sf -X POST "http://localhost:3000/api/v1/orgs" \
      -H "Content-Type: application/json" \
      -u "gitea-admin:${GITEA_ADMIN_PASS}" \
      -d '{"username":"narwhal","full_name":"Narwhal","visibility":"public"}' 2>/dev/null || true

  # Create teams in narwhal org
  for team_data in '{"name":"Developers","permission":"write","units":["repo.code","repo.issues","repo.pulls"]}' \
                   '{"name":"Viewers","permission":"read","units":["repo.code","repo.issues"]}'; do
    kubectl exec -n devtools "${GITEA_POD}" -- \
      curl -sf -X POST "http://localhost:3000/api/v1/orgs/narwhal/teams" \
        -H "Content-Type: application/json" \
        -u "gitea-admin:${GITEA_ADMIN_PASS}" \
        -d "${team_data}" 2>/dev/null || true
  done

  # Configure OAuth2 source with group→team mapping
  kubectl exec -n devtools "${GITEA_POD}" -- gitea admin auth add-oauth \
    --name "keycloak" \
    --provider "openidConnect" \
    --key "gitea" \
    --secret "gitea-secret" \
    --auto-discover-url "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
    --group-claim-name "groups" \
    --admin-group "cluster-admin" \
    --restricted-group "guest" \
    --group-team-map '{"developer":{"narwhal":["Developers"]},"viewer":{"narwhal":["Viewers"]}}' \
    --skip-local-2fa || true
else
  echo "WARN: Gitea pod not found, skipping OAuth2 configuration"
fi

echo "=== Gitea Installation Done ==="

echo ""
echo "=========================================="
echo "Gitea Ready!"
echo "=========================================="
echo ""
echo "Access:"
echo "  kubectl port-forward svc/gitea-http -n devtools 3000:3000"
echo "  URL: http://localhost:3000"
echo "  User: gitea-admin / (see Secret gitea-admin -n devtools)"
echo ""
kubectl get pods -n devtools
