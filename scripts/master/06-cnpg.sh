#!/bin/bash
set -euo pipefail

CNPG_CHART_VERSION="${CNPG_CHART_VERSION:-0.27.1}"  # app: v1.28.1
POSTGRES_VERSION="${POSTGRES_VERSION:-17}"

echo "=== Installing CloudNative-PG v1.28.1 (chart: ${CNPG_CHART_VERSION}) ==="

export KUBECONFIG=/home/vagrant/.kube/config

# Add CNPG Helm repo
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update

# Install CNPG Operator
helm upgrade --install cnpg cnpg/cloudnative-pg \
  --namespace cnpg-system \
  --create-namespace \
  --version "${CNPG_CHART_VERSION}" \
  --wait

# Wait for operator to be ready
echo "Waiting for CNPG operator..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=cloudnative-pg -n cnpg-system --timeout=120s

#=========================================
# Create PostgreSQL HA Cluster for Keycloak
#=========================================
echo "=== Creating PostgreSQL HA Cluster ==="

# Create namespace
kubectl create namespace keycloak --dry-run=client -o yaml | kubectl apply -f -

# Create PostgreSQL cluster
cat <<EOF | kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: keycloak-db
  namespace: keycloak
spec:
  instances: 3
  imageName: ghcr.io/cloudnative-pg/postgresql:${POSTGRES_VERSION}

  bootstrap:
    initdb:
      database: keycloak
      owner: keycloak
      secret:
        name: keycloak-db-credentials

  storage:
    size: 10Gi
    storageClass: nfs-csi

  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "500m"

  postgresql:
    parameters:
      max_connections: "200"
      shared_buffers: "256MB"

  monitoring:
    enablePodMonitor: false

  # Backup disabled during initial setup (SeaweedFS not yet available)
  # Enable after GitOps deployment completes
  # backup:
  #   barmanObjectStore:
  #     destinationPath: s3://cnpg-backup/keycloak
  #     endpointURL: http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333
  #     s3Credentials:
  #       accessKeyId:
  #         name: cnpg-s3-credentials
  #         key: ACCESS_KEY_ID
  #       secretAccessKey:
  #         name: cnpg-s3-credentials
  #         key: ACCESS_SECRET_KEY
  #     wal:
  #       compression: gzip
  #     data:
  #       compression: gzip
  #   retentionPolicy: "14d"
EOF

# Create database credentials secret
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-db-credentials
  namespace: keycloak
type: kubernetes.io/basic-auth
stringData:
  username: keycloak
  password: keycloak-db-password
---
apiVersion: v1
kind: Secret
metadata:
  name: cnpg-s3-credentials
  namespace: keycloak
type: Opaque
stringData:
  ACCESS_KEY_ID: admin
  ACCESS_SECRET_KEY: admin
EOF

# Wait for PostgreSQL cluster to be ready
echo "Waiting for PostgreSQL cluster to be ready..."
kubectl wait --for=condition=Ready cluster/keycloak-db -n keycloak --timeout=300s || true

# Wait for primary pod
sleep 10
kubectl wait --for=condition=Ready pod -l cnpg.io/cluster=keycloak-db -n keycloak --timeout=300s || true

echo "=== CNPG PostgreSQL HA Cluster Ready ==="
kubectl get cluster -n keycloak
kubectl get pods -n keycloak
