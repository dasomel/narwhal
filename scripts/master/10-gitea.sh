#!/bin/bash
set -euo pipefail

GITEA_VERSION="${GITEA_VERSION:-v1.25.4}"

echo "=== Installing Gitea ${GITEA_VERSION} ==="

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
kubectl create namespace gitea --dry-run=client -o yaml | kubectl apply -f -

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

# Install Gitea with Keycloak OIDC
GITEA_CHART_VERSION="${GITEA_CHART_VERSION:-12.5.0}"

helm upgrade --install gitea gitea-charts/gitea \
  --namespace gitea \
  --version "${GITEA_CHART_VERSION}" \
  --set image.tag="${GITEA_VERSION#v}" \
  --set gitea.admin.username=gitea-admin \
  --set gitea.admin.password=gitea-admin \
  --set gitea.admin.email=admin@local \
  --set postgresql.enabled=false \
  --set postgresql-ha.enabled=false \
  --set gitea.config.database.DB_TYPE=postgres \
  --set gitea.config.database.HOST=gitea-db-rw:5432 \
  --set gitea.config.database.NAME=gitea \
  --set gitea.config.database.USER=gitea \
  --set gitea.config.database.PASSWD=gitea-db-password \
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

# Configure Keycloak OAuth2 provider via API
echo "Configuring Gitea OAuth2 provider..."
sleep 10

# Wait for Gitea to be ready
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea -n gitea --timeout=300s || true

# Create OAuth2 source via Gitea API
GITEA_POD=$(kubectl get pod -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "${GITEA_POD}" ]; then
  kubectl exec -n gitea "${GITEA_POD}" -- gitea admin auth add-oauth \
    --name "Keycloak" \
    --provider "openidConnect" \
    --key "gitea" \
    --secret "gitea-secret" \
    --auto-discover-url "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
    --group-claim-name "groups" \
    --admin-group "cluster-admins" \
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
echo "  kubectl port-forward svc/gitea-http -n gitea 3000:3000"
echo "  URL: http://localhost:3000"
echo "  User: gitea-admin / gitea-admin"
echo ""
kubectl get pods -n gitea
