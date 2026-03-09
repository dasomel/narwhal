#!/bin/bash
set -euo pipefail
# shellcheck source=scripts/common/lib.sh
source /home/vagrant/scripts/common/lib.sh

CNPG_CHART_VERSION="${CNPG_CHART_VERSION:-0.27.1}"  # app: v1.28.1
POSTGRES_VERSION="${POSTGRES_VERSION:-18}"

echo "=== Installing CloudNative-PG v1.28.1 (chart: ${CNPG_CHART_VERSION}) ==="

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

# Add CNPG Helm repo
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm repo update

# Install CNPG Operator (no --wait: avoids atomic rollback on timeout)
helm upgrade --install cnpg cnpg/cloudnative-pg \
  --namespace platform-system \
  --create-namespace \
  --version "${CNPG_CHART_VERSION}" \
  --timeout 5m || echo "WARN: CNPG operator install timed out, waiting manually..."

# Wait for operator pod to be ready (must be running before creating CRs)
echo "Waiting for CNPG operator pod..."
CNPG_READY=false
for attempt in {1..60}; do
  if kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=cloudnative-pg -n platform-system --timeout=10s 2>/dev/null; then
    echo "CNPG operator pod is ready"
    CNPG_READY=true
    break
  fi
  echo "CNPG operator not ready yet... (${attempt}/60)"
  sleep 10
done

if [ "${CNPG_READY}" != "true" ]; then
  echo "ERROR: CNPG operator pod did not become ready after 10 minutes"
  kubectl get pods -n platform-system || true
  kubectl describe pod -l app.kubernetes.io/name=cloudnative-pg -n platform-system 2>/dev/null | tail -20 || true
  exit 1
fi

# Wait extra for webhook to be fully registered
echo "Waiting for CNPG webhook to be ready..."
sleep 15

#=========================================
# Create Unified PostgreSQL HA Cluster
#=========================================
echo "=== Creating Unified PostgreSQL HA Cluster (narwhal-db) ==="

# Create database namespace
kubectl create namespace database --dry-run=client -o yaml | kubectl apply -f -

# Create database credentials secrets (idempotent: reuse existing passwords)
if ! kubectl get secret narwhal-db-credentials -n database &>/dev/null; then
  KEYCLOAK_DB_PASS=$(generate_password)
  HARBOR_DB_PASS=$(generate_password)
  GITEA_DB_PASS=$(generate_password)
  kubectl create secret generic narwhal-db-credentials \
    --from-literal=username=keycloak \
    --from-literal=password="${KEYCLOAK_DB_PASS}" \
    --from-literal=harbor-password="${HARBOR_DB_PASS}" \
    --from-literal=gitea-password="${GITEA_DB_PASS}" \
    -n database
  echo "DB credentials secret created with generated passwords"
else
  KEYCLOAK_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
    -o jsonpath='{.data.password}' | base64 -d)
  HARBOR_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
    -o jsonpath='{.data.harbor-password}' | base64 -d)
  GITEA_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
    -o jsonpath='{.data.gitea-password}' | base64 -d)
  echo "DB credentials secret already exists, reusing existing passwords"
fi

# CNPG bootstrap uses narwhal-db-credentials directly (username/password fields)
# S3 credentials for backup (placeholder, adjust if SeaweedFS/MinIO is used)
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: cnpg-s3-credentials
  namespace: database
type: Opaque
stringData:
  ACCESS_KEY_ID: admin
  ACCESS_SECRET_KEY: admin
EOF

# Write cluster manifest to temp file (avoids heredoc + if + set -e issues)
CLUSTER_MANIFEST=$(mktemp)
cat > "${CLUSTER_MANIFEST}" <<EOF
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: narwhal-db
  namespace: database
spec:
  instances: 2
  imageName: ghcr.io/cloudnative-pg/postgresql:${POSTGRES_VERSION}

  bootstrap:
    initdb:
      database: keycloak
      owner: keycloak
      dataChecksums: true
      secret:
        name: narwhal-db-credentials
      postInitSQL:
        # Create additional users
        - CREATE USER harbor WITH PASSWORD '${HARBOR_DB_PASS}'
        - CREATE USER gitea WITH PASSWORD '${GITEA_DB_PASS}'
        # Create additional databases
        - CREATE DATABASE harbor OWNER harbor
        - CREATE DATABASE gitea OWNER gitea
        # Grant privileges
        - GRANT ALL PRIVILEGES ON DATABASE harbor TO harbor
        - GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea

  storage:
    size: 20Gi
    storageClass: nfs-csi

  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "1"

  # PostgreSQL tuning (optimized for 3-app unified workload)
  postgresql:
    parameters:
      # Memory
      shared_buffers: "256MB"
      effective_cache_size: "768MB"
      work_mem: "8MB"
      maintenance_work_mem: "128MB"
      # Connections
      max_connections: "200"
      # WAL & Checkpoint
      max_wal_size: "1GB"
      min_wal_size: "512MB"
      checkpoint_timeout: "15min"
      archive_timeout: "300s"
      # Query planner
      random_page_cost: "1.1"
      effective_io_concurrency: "200"
      # Logging
      log_min_duration_statement: "1000"
      log_checkpoints: "on"
      log_connections: "on"
      log_disconnections: "on"

  monitoring:
    enablePodMonitor: false

  # Tolerations for disk pressure
  affinity:
    tolerations:
      - key: node.kubernetes.io/disk-pressure
        operator: Exists
        effect: NoSchedule
EOF

# Create unified PostgreSQL cluster (retry for webhook readiness)
CLUSTER_CREATED=false
for attempt in {1..15}; do
  if kubectl apply -f "${CLUSTER_MANIFEST}" 2>/dev/null; then
    echo "narwhal-db Cluster CR created successfully"
    CLUSTER_CREATED=true
    break
  fi
  echo "CNPG webhook not ready, retrying... (${attempt}/15)"
  sleep 15
done
rm -f "${CLUSTER_MANIFEST}"

if [ "${CLUSTER_CREATED}" != "true" ]; then
  echo "ERROR: Failed to create narwhal-db cluster after 15 attempts"
  exit 1
fi

# Wait for PostgreSQL cluster to be ready
echo "Waiting for PostgreSQL cluster to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true

# Wait for primary pod
sleep 10
kubectl wait --for=condition=Ready pod -l cnpg.io/cluster=narwhal-db -n database --timeout=300s || true

#=========================================
# Create PgBouncer Connection Pooler
#=========================================
echo "=== Creating PgBouncer Connection Pooler ==="

cat <<EOF | kubectl apply -f - || echo "WARN: PgBouncer pooler creation failed, continuing..."
apiVersion: postgresql.cnpg.io/v1
kind: Pooler
metadata:
  name: narwhal-db-pooler-rw
  namespace: database
spec:
  cluster:
    name: narwhal-db
  instances: 1
  type: rw
  pgbouncer:
    poolMode: transaction
    parameters:
      max_client_conn: "1000"
      default_pool_size: "25"
      min_pool_size: "5"
      reserve_pool_size: "5"
      reserve_pool_timeout: "3"
      server_idle_timeout: "60"
    # Resource limits for PgBouncer pods
  template:
    spec:
      containers:
        - name: pgbouncer
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "128Mi"
              cpu: "200m"
EOF

# Wait for pooler pods
echo "Waiting for PgBouncer pooler..."
sleep 5
kubectl wait --for=condition=Ready pod -l cnpg.io/poolerName=narwhal-db-pooler-rw -n database --timeout=120s || true

#=========================================
# Create cross-namespace service aliases
#=========================================
echo "=== Creating cross-namespace service aliases ==="

# ExternalName services so apps can use short names within their namespaces
kubectl create namespace iam --dry-run=client -o yaml | kubectl apply -f -
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: keycloak-db-rw
  namespace: iam
spec:
  type: ExternalName
  externalName: narwhal-db-rw.database.svc.cluster.local
  ports:
    - port: 5432
EOF

kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: harbor-db-rw
  namespace: devtools
spec:
  type: ExternalName
  externalName: narwhal-db-rw.database.svc.cluster.local
  ports:
    - port: 5432
EOF

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: gitea-db-rw
  namespace: devtools
spec:
  type: ExternalName
  externalName: narwhal-db-rw.database.svc.cluster.local
  ports:
    - port: 5432
EOF

echo "=== CNPG Unified PostgreSQL HA Cluster Ready ==="
echo ""
echo "=========================================="
echo "PostgreSQL Unified Cluster: narwhal-db"
echo "=========================================="
echo "Namespace: database"
echo "Instances: 2 (HA: 1 primary + 1 replica)"
echo "PgBouncer: 1 pooler pod (transaction mode)"
echo ""
echo "Databases:"
echo "  keycloak - owner: keycloak (password in secret: narwhal-db-credentials/password)"
echo "  harbor   - owner: harbor   (password in secret: narwhal-db-credentials/harbor-password)"
echo "  gitea    - owner: gitea    (password in secret: narwhal-db-credentials/gitea-password)"
echo ""
echo "Connection (via PgBouncer):"
echo "  Host: narwhal-db-pooler-rw.database.svc.cluster.local"
echo "  Port: 5432"
echo ""
echo "Connection (direct, bypassing pooler):"
echo "  Host: narwhal-db-rw.database.svc.cluster.local"
echo "  Port: 5432"
echo ""
echo "Cross-namespace aliases (ExternalName):"
echo "  iam ns:      keycloak-db-rw:5432 -> narwhal-db-rw.database.svc.cluster.local"
echo "  devtools ns: harbor-db-rw:5432   -> narwhal-db-rw.database.svc.cluster.local"
echo "  devtools ns: gitea-db-rw:5432    -> narwhal-db-rw.database.svc.cluster.local"
echo ""
kubectl get cluster -n database
kubectl get pooler -n database
kubectl get pods -n database
