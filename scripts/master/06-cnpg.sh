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
# Create Unified PostgreSQL HA Cluster
#=========================================
echo "=== Creating Unified PostgreSQL HA Cluster (narwhal-db) ==="

# Create database namespace
kubectl create namespace database --dry-run=client -o yaml | kubectl apply -f -

# Create database credentials secrets
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: narwhal-db-credentials
  namespace: database
type: kubernetes.io/basic-auth
stringData:
  username: keycloak
  password: keycloak-db-password
---
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

# Create unified PostgreSQL cluster with all databases
cat <<EOF | kubectl apply -f -
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
        - CREATE USER harbor WITH PASSWORD 'harbor-db-password'
        - CREATE USER gitea WITH PASSWORD 'gitea-db-password'
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
      memory: "1Gi"
      cpu: "500m"
    limits:
      memory: "2Gi"
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

  # Backup disabled during initial setup (SeaweedFS not yet available)
  # Enable after GitOps deployment completes
  # backup:
  #   barmanObjectStore:
  #     destinationPath: s3://cnpg-backup/narwhal-db
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

cat <<EOF | kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Pooler
metadata:
  name: narwhal-db-pooler-rw
  namespace: database
spec:
  cluster:
    name: narwhal-db
  instances: 2
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
kubectl create namespace keycloak --dry-run=client -o yaml | kubectl apply -f -
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: keycloak-db-rw
  namespace: keycloak
spec:
  type: ExternalName
  externalName: narwhal-db-rw.database.svc.cluster.local
  ports:
    - port: 5432
EOF

kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f -
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: harbor-db-rw
  namespace: harbor
spec:
  type: ExternalName
  externalName: narwhal-db-rw.database.svc.cluster.local
  ports:
    - port: 5432
EOF

kubectl create namespace gitea --dry-run=client -o yaml | kubectl apply -f -
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: gitea-db-rw
  namespace: gitea
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
echo "Instances: 2 (HA with streaming replication)"
echo "PgBouncer: 2 pooler pods (transaction mode)"
echo ""
echo "Databases:"
echo "  keycloak - owner: keycloak, password: keycloak-db-password"
echo "  harbor   - owner: harbor, password: harbor-db-password"
echo "  gitea    - owner: gitea, password: gitea-db-password"
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
echo "  keycloak ns: keycloak-db-rw:5432 -> narwhal-db-rw.database.svc.cluster.local"
echo "  harbor ns:   harbor-db-rw:5432   -> narwhal-db-rw.database.svc.cluster.local"
echo "  gitea ns:    gitea-db-rw:5432    -> narwhal-db-rw.database.svc.cluster.local"
echo ""
kubectl get cluster -n database
kubectl get pooler -n database
kubectl get pods -n database
