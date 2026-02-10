#!/bin/bash
set -euo pipefail

GITEA_VERSION="${GITEA_VERSION:-v1.25.4}"

echo "=== Installing Gitea ${GITEA_VERSION} ==="

export KUBECONFIG=/home/vagrant/.kube/config

# Create namespace
kubectl create namespace gitea --dry-run=client -o yaml | kubectl apply -f -

# Add Gitea Helm repo
helm repo add gitea-charts https://dl.gitea.com/charts/
helm repo update

# Install Gitea with PostgreSQL (using CNPG)
# First create PostgreSQL cluster
cat <<EOF | kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: gitea-db
  namespace: gitea
spec:
  instances: 2
  imageName: ghcr.io/cloudnative-pg/postgresql:17
  bootstrap:
    initdb:
      database: gitea
      owner: gitea
      secret:
        name: gitea-db-credentials
  storage:
    size: 10Gi
    storageClass: nfs-csi
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "500m"
EOF

# Create database credentials
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: gitea-db-credentials
  namespace: gitea
type: kubernetes.io/basic-auth
stringData:
  username: gitea
  password: gitea-db-password
EOF

# Wait for PostgreSQL
echo "Waiting for Gitea PostgreSQL cluster..."
sleep 10
kubectl wait --for=condition=Ready cluster/gitea-db -n gitea --timeout=300s || true

# Keycloak OIDC configuration
KEYCLOAK_URL="${KEYCLOAK_URL:-http://keycloak-service.keycloak}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-kubernetes}"

# Install Gitea with Keycloak OIDC
helm upgrade --install gitea gitea-charts/gitea \
  --namespace gitea \
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
  --set gitea.config.server.ROOT_URL=http://localhost:3000 \
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
  --wait --timeout=600s

# Configure Keycloak OAuth2 provider via API
echo "Configuring Gitea OAuth2 provider..."
sleep 10

# Wait for Gitea to be ready
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea -n gitea --timeout=300s || true

# Create OAuth2 source via Gitea API
GITEA_POD=$(kubectl get pod -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n gitea ${GITEA_POD} -- gitea admin auth add-oauth \
  --name "Keycloak" \
  --provider "openidConnect" \
  --key "gitea" \
  --secret "gitea-secret" \
  --auto-discover-url "${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration" \
  --group-claim-name "groups" \
  --admin-group "cluster-admins" || true

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
