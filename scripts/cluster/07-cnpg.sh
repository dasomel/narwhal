#!/bin/bash
set -euo pipefail
# shellcheck source=scripts/common/lib.sh
source /home/vagrant/scripts/common/lib.sh

CNPG_CHART_VERSION="${CNPG_CHART_VERSION:-0.28.3}"  # app: v1.29.1
POSTGRES_VERSION="${POSTGRES_VERSION:-18}"

echo "=== Installing CloudNative-PG v1.29.1 (chart: ${CNPG_CHART_VERSION}) ==="

# Retry wrapper for transient DNS/network failures (mirrors 03-k8s-install.sh pattern)
retry() {
  local n=1 max=5
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after ${max} attempts: $*" >&2
      return 1
    fi
    echo "  attempt ${n}/${max} failed, retrying in 15s..." >&2
    n=$((n + 1))
    sleep 15
  done
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

# Add CNPG Helm repo (retry for transient DNS timeouts, e.g. cloudnative-pg.github.io lookup failure)
retry helm repo add cnpg https://cloudnative-pg.github.io/charts
retry helm repo update

# Install CNPG Operator (no --wait: avoids atomic rollback on timeout)
retry helm upgrade --install cnpg cnpg/cloudnative-pg \
  --force-conflicts \
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
# D-authmig: narwhal-db-credentials bootstrap user changed from 'authentik' to 'narwhal'
# (Authentik SSO removed; 'narwhal' is the neutral CNPG bootstrap owner)
if ! kubectl get secret narwhal-db-credentials -n database &>/dev/null; then
  NARWHAL_DB_PASS=$(generate_password)
  HARBOR_DB_PASS=$(generate_password)
  GITEA_DB_PASS=$(generate_password)
  KEYCLOAK_DB_PASS=$(generate_password)
  kubectl create secret generic narwhal-db-credentials \
    --from-literal=username=narwhal \
    --from-literal=password="${NARWHAL_DB_PASS}" \
    --from-literal=harbor-password="${HARBOR_DB_PASS}" \
    --from-literal=gitea-password="${GITEA_DB_PASS}" \
    --from-literal=keycloak-password="${KEYCLOAK_DB_PASS}" \
    -n database
  echo "DB credentials secret created with generated passwords"
else
  HARBOR_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
    -o jsonpath='{.data.harbor-password}' | base64 -d)
  GITEA_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
    -o jsonpath='{.data.gitea-password}' | base64 -d)
  # keycloak-password may be absent on clusters provisioned before this fix; add it if missing
  if kubectl get secret narwhal-db-credentials -n database \
      -o jsonpath='{.data.keycloak-password}' 2>/dev/null | grep -q .; then
    KEYCLOAK_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
      -o jsonpath='{.data.keycloak-password}' | base64 -d)
  else
    KEYCLOAK_DB_PASS=$(generate_password)
    kubectl patch secret narwhal-db-credentials -n database \
      --type='json' \
      -p="[{\"op\":\"add\",\"path\":\"/data/keycloak-password\",\"value\":\"$(echo -n "${KEYCLOAK_DB_PASS}" | base64 -w0)\"}]"
    echo "Added missing keycloak-password to existing narwhal-db-credentials secret"
  fi
  echo "DB credentials secret already exists, reusing existing passwords"
fi

# CNPG bootstrap uses narwhal-db-credentials directly (username/password fields)
# S3 credentials - reuse SeaweedFS creds (aligned with Velero pattern in 08-4-storage.sh)
S3_ACCESS_KEY="${S3_ACCESS_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d || echo "admin")}"
S3_SECRET_KEY="${S3_SECRET_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d || echo "")}"

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: cnpg-s3-credentials
  namespace: database
type: Opaque
stringData:
  ACCESS_KEY_ID: ${S3_ACCESS_KEY}
  ACCESS_SECRET_KEY: ${S3_SECRET_KEY}
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
      database: narwhal
      owner: narwhal
      dataChecksums: true
      secret:
        name: narwhal-db-credentials
      postInitSQL:
        # Create additional users
        - CREATE USER harbor WITH PASSWORD '${HARBOR_DB_PASS}'
        - CREATE USER gitea WITH PASSWORD '${GITEA_DB_PASS}'
        - CREATE USER keycloak WITH PASSWORD '${KEYCLOAK_DB_PASS}'
        # Create additional databases
        - CREATE DATABASE harbor OWNER harbor
        - CREATE DATABASE gitea OWNER gitea
        - CREATE DATABASE keycloak OWNER keycloak
        # Grant privileges
        - GRANT ALL PRIVILEGES ON DATABASE harbor TO harbor
        - GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea
        - GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak

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
    enablePodMonitor: true

  backup:
    barmanObjectStore:
      destinationPath: s3://cnpg-backup/
      endpointURL: http://seaweedfs-s3.storage.svc.cluster.local:8333
      s3Credentials:
        accessKeyId:
          name: cnpg-s3-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: cnpg-s3-credentials
          key: ACCESS_SECRET_KEY
      wal:
        compression: gzip
      data:
        compression: gzip
        immediateCheckpoint: false
    retentionPolicy: "30d"

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

# Wait for PostgreSQL cluster to be ready (poll readyInstances == spec.instances with 10m timeout)
echo "Waiting for PostgreSQL cluster to be ready..."
CLUSTER_READY=false
for attempt in {1..60}; do
  SPEC_INSTANCES=$(kubectl get cluster narwhal-db -n database \
    -o jsonpath='{.spec.instances}' 2>/dev/null || echo "0")
  READY_INSTANCES=$(kubectl get cluster narwhal-db -n database \
    -o jsonpath='{.status.readyInstances}' 2>/dev/null || echo "0")
  # jsonpath prints an empty string when the field is absent, and `||` never fires
  # because kubectl exited 0 — so the default is not applied and the numeric test
  # below aborts the loop body with "[: : integer expression expected" every tick.
  # status.readyInstances is absent for the whole "Setting up primary" phase, which
  # is exactly when this loop is supposed to be waiting.
  [ -n "${SPEC_INSTANCES//[!0-9]/}" ] || SPEC_INSTANCES=0
  [ -n "${READY_INSTANCES//[!0-9]/}" ] || READY_INSTANCES=0
  if [ "${SPEC_INSTANCES}" -gt 0 ] && [ "${READY_INSTANCES}" -ge "${SPEC_INSTANCES}" ]; then
    echo "PostgreSQL cluster ready: ${READY_INSTANCES}/${SPEC_INSTANCES} instances"
    CLUSTER_READY=true
    break
  fi
  echo "  Cluster not ready yet (${READY_INSTANCES:-0}/${SPEC_INSTANCES:-?}), waiting... (${attempt}/60)"
  sleep 10
done

if [ "${CLUSTER_READY}" != "true" ]; then
  echo "ERROR: PostgreSQL cluster did not become ready after 10 minutes"
  kubectl get cluster narwhal-db -n database || true
  kubectl get pods -n database || true
  exit 1
fi

# Wait for primary pod to be Ready before exec-ing into it
kubectl wait --for=condition=Ready pod -l "cnpg.io/cluster=narwhal-db,role=primary" \
  -n database --timeout=120s || true

#=========================================
# Ensure all required databases exist (idempotent post-bootstrap guarantee)
# CNPG postInitSQL runs ONCE at cluster creation; if the primary restarts mid-bootstrap
# those DBs are never created and re-applying the CR won't re-run bootstrap.
# This step is safe to run on every script execution: each statement is guarded by an
# existence check and is a no-op when the DB/role already exists.
#=========================================
echo "=== Ensuring required databases exist (idempotent) ==="

# Find the primary pod
PRIMARY_POD=$(kubectl get pod -n database \
  -l "cnpg.io/cluster=narwhal-db,role=primary" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "${PRIMARY_POD}" ]; then
  echo "WARN: Could not find primary pod for narwhal-db; skipping ensure-databases step"
else
  echo "Primary pod: ${PRIMARY_POD}"

  # Build idempotent SQL: create user+db only when absent, set password unconditionally
  # (password update is safe because the secret already holds the correct value)
  ENSURE_SQL=$(cat <<ENDSQL
DO \$\$
BEGIN
  -- harbor
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'harbor') THEN
    EXECUTE format('CREATE USER harbor WITH PASSWORD %L', '${HARBOR_DB_PASS}');
    RAISE NOTICE 'Created user harbor';
  ELSE
    EXECUTE format('ALTER USER harbor WITH PASSWORD %L', '${HARBOR_DB_PASS}');
  END IF;
  -- gitea
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gitea') THEN
    EXECUTE format('CREATE USER gitea WITH PASSWORD %L', '${GITEA_DB_PASS}');
    RAISE NOTICE 'Created user gitea';
  ELSE
    EXECUTE format('ALTER USER gitea WITH PASSWORD %L', '${GITEA_DB_PASS}');
  END IF;
  -- keycloak
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keycloak') THEN
    EXECUTE format('CREATE USER keycloak WITH PASSWORD %L', '${KEYCLOAK_DB_PASS}');
    RAISE NOTICE 'Created user keycloak';
  ELSE
    EXECUTE format('ALTER USER keycloak WITH PASSWORD %L', '${KEYCLOAK_DB_PASS}');
  END IF;
END
\$\$;
SELECT format('CREATE DATABASE %I OWNER %I', 'harbor', 'harbor') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'harbor')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'gitea', 'gitea') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'gitea')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'keycloak', 'keycloak') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'keycloak')\gexec
GRANT ALL PRIVILEGES ON DATABASE harbor TO harbor;
GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea;
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
ENDSQL
)

  # Pipe SQL to psql via stdin (kubectl exec -i). The CNPG postgres container
  # has a READ-ONLY root filesystem, so we must NOT copy a file into the pod
  # (kubectl cp / tar fails with "Read-only file system").
  if printf '%s\n' "${ENSURE_SQL}" | kubectl exec -i -n database "${PRIMARY_POD}" -- \
      psql -U postgres -v ON_ERROR_STOP=1; then
    echo "Ensure-databases step completed successfully"
  else
    echo "WARN: ensure-databases step failed; databases may need manual creation"
  fi

  kubectl exec -n database "${PRIMARY_POD}" -- \
    psql -U postgres -c "\l" 2>/dev/null | grep -E "harbor|gitea|keycloak" || true
fi

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
# Create ScheduledBackup
#=========================================
echo "=== Creating ScheduledBackup (daily at 02:00) ==="

cat <<EOF | kubectl apply -f - || echo "WARN: ScheduledBackup creation failed, continuing..."
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: narwhal-db-backup
  namespace: database
spec:
  schedule: "0 2 * * *"
  backupOwnerReference: self
  cluster:
    name: narwhal-db
EOF

#=========================================
# Create cross-namespace service aliases
#=========================================
echo "=== Creating cross-namespace service aliases ==="

# D-authmig: authentik-db-rw ExternalName service removed (Authentik SSO removed)
# ExternalName services so apps can use short names within their namespaces
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
echo "  narwhal   - owner: narwhal   (bootstrap owner; password in secret: narwhal-db-credentials/password)"
echo "  harbor    - owner: harbor    (password in secret: narwhal-db-credentials/harbor-password)"
echo "  gitea     - owner: gitea     (password in secret: narwhal-db-credentials/gitea-password)"
echo "  keycloak  - owner: keycloak  (password in secret: narwhal-db-credentials/keycloak-password)"
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
echo "  devtools ns: harbor-db-rw:5432   -> narwhal-db-rw.database.svc.cluster.local"
echo "  devtools ns: gitea-db-rw:5432    -> narwhal-db-rw.database.svc.cluster.local"
echo ""
kubectl get cluster -n database
kubectl get pooler -n database
# Informational listing — must not decide the script's exit status.
kubectl get pods -n database || true
