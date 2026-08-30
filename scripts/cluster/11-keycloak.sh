#!/bin/bash
set -euo pipefail

# Manifests come from the airgap bundle.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh
source /home/vagrant/scripts/common/lib.sh

# 11-keycloak.sh
# Phase: Keycloak IAM 설치 (Authentik 대체)
# - Keycloak Operator CRD+RBAC 설치 (namespace: iam)
# - CNPG narwhal-db에 keycloak user/db 생성
# - keycloak-db-secret 생성 (iam ns)
# - Keycloak CR 생성 (Operator가 pod 관리)
# - Keycloak HTTPS route is installed by 08-6-tls-routes.sh from the canonical
#   GitOps ApisixUpstream/ApisixRoute template.
# Depends on: 07-cnpg.sh (narwhal-db ready), 08-6-tls-routes.sh (APISIX route ready)

KEYCLOAK_VERSION="${KEYCLOAK_VERSION:-26.5.7}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"
export KUBECONFIG=/home/vagrant/.kube/config-local

echo "=== Installing Keycloak ${KEYCLOAK_VERSION} ==="

# Wait for narwhal-db
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true
kubectl wait --for=condition=Ready pod -l cnpg.io/cluster=narwhal-db -n database --timeout=120s || true

ensure_namespace iam

#=========================================
# Create keycloak user/database in CNPG narwhal-db
# Idempotent: CREATE IF NOT EXISTS pattern
#=========================================
echo "=== Creating keycloak database and user ==="

CNPG_PRIMARY=$(kubectl get pod -n database \
  -l cnpg.io/cluster=narwhal-db,role=primary \
  -o jsonpath='{.items[0].metadata.name}')

# D9: Single-source keycloak credential from narwhal-db-credentials (set by 07-cnpg.sh).
# Root cause of the credential race: 11-keycloak used to generate its OWN password and
# ALTER USER keycloak to match it — but 07-cnpg's ensure-databases step ALSO ALTERs the
# keycloak role (idempotent block, line ~320). Whichever script ran last won; the other
# script's secret became stale. Fix: 11-keycloak is NOT authoritative for the role password.
# It reads narwhal-db-credentials/keycloak-password (written once by 07-cnpg) and keeps
# keycloak-db-secret in sync with it. The DB role is always set by 07-cnpg.
KEYCLOAK_DB_PASS="$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.keycloak-password}' | base64 -d)"

if [ -z "${KEYCLOAK_DB_PASS}" ]; then
  echo "ERROR: narwhal-db-credentials/keycloak-password is empty. Ensure 07-cnpg.sh ran successfully." >&2
  exit 1
fi

# Sync keycloak-db-secret to match the DB role password (idempotent: create or overwrite).
if ! kubectl get secret keycloak-db-secret -n iam &>/dev/null; then
  kubectl create secret generic keycloak-db-secret -n iam \
    --from-literal=username=keycloak \
    --from-literal=password="${KEYCLOAK_DB_PASS}"
  echo "keycloak-db-secret created in iam namespace"
else
  # Overwrite password field unconditionally so it always matches the DB role.
  KEYCLOAK_DB_PASS_B64="$(printf '%s' "${KEYCLOAK_DB_PASS}" | base64 -w0)"
  kubectl patch secret keycloak-db-secret -n iam \
    --type='json' \
    -p="[{\"op\":\"replace\",\"path\":\"/data/password\",\"value\":\"${KEYCLOAK_DB_PASS_B64}\"}]" \
    2>/dev/null || \
  kubectl patch secret keycloak-db-secret -n iam \
    --type='json' \
    -p="[{\"op\":\"add\",\"path\":\"/data/password\",\"value\":\"${KEYCLOAK_DB_PASS_B64}\"}]"
  echo "keycloak-db-secret synced to narwhal-db-credentials/keycloak-password"
fi

# D9: Auth gate — verify the secret password actually authenticates before deploying Keycloak.
# This catches any residual mismatch (e.g. 07-cnpg re-ran and changed the role password again).
echo "Verifying DB auth with synced credentials..."
AUTH_OK=false
for _attempt in $(seq 1 10); do
  if kubectl exec -n database "${CNPG_PRIMARY}" -- \
      env PGPASSWORD="${KEYCLOAK_DB_PASS}" \
      psql -U keycloak -h narwhal-db-rw.database.svc.cluster.local -d keycloak \
      -c "SELECT 1" &>/dev/null; then
    AUTH_OK=true
    echo "DB auth verified OK"
    break
  fi
  echo "DB auth not ready yet (attempt ${_attempt}/10), retrying in 6s..."
  sleep 6
done
if [ "${AUTH_OK}" != "true" ]; then
  echo "ERROR: keycloak role cannot authenticate with the password from narwhal-db-credentials. Check 07-cnpg.sh logs." >&2
  exit 1
fi

#=========================================
# Install Keycloak Operator CRD + RBAC
# kubernetes.yml defaults to 'keycloak' namespace → sed to 'iam'
#=========================================
echo "=== Installing Keycloak Operator ${KEYCLOAK_VERSION} ==="


# 1. CRDs (cluster-scoped, no namespace)
kubectl apply -f "$(manifest keycloak-keycloaks.k8s.keycloak.org-v1.yml)"
kubectl apply -f "$(manifest keycloak-keycloakrealmimports.k8s.keycloak.org-v1.yml)"

# 2. Operator RBAC + Deployment (namespaced to iam)
kubectl apply -n iam -f "$(manifest keycloak-kubernetes.yml)"

echo "Waiting for Keycloak Operator deployment..."
kubectl rollout status deployment/keycloak-operator -n iam --timeout=180s

#=========================================
# Create Keycloak CR
# - hostname v2: hostname.hostname + hostname.strict + proxy.headers
# - Istio ambient: no opt-out label (runs in ambient mesh)
# - KC_DB_URL: full JDBC URL with sslmode=disable via podTemplate env
#   (Istio ambient ztunnel intercepts TCP at L4; operator-set KC_DB_URL_HOST/PORT/DATABASE
#    default to SSL → ztunnel HBONE causes SSL handshake reset. KC_DB_URL overrides all.)
# - Ingress disabled: APISIX handles routing
#=========================================
echo "=== Creating Keycloak CR ==="

kubectl apply -f - <<EOF
apiVersion: k8s.keycloak.org/v2alpha1
kind: Keycloak
metadata:
  name: keycloak
  namespace: iam
spec:
  instances: 1
  db:
    vendor: postgres
    host: narwhal-db-rw.database.svc.cluster.local
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
    hostname: keycloak.${DOMAIN}
    strict: false
  proxy:
    headers: xforwarded
  ingress:
    enabled: false
  # Operator HONORS spec.resources but IGNORES unsupported.podTemplate resources.
  # Without an effective CPU request Keycloak gets starved under node contention ->
  # 1s health probes time out -> liveness SIGKILL crash loop -> cluster-wide SSO 502.
  resources:
    requests:
      cpu: "600m"
      memory: "1Gi"
    limits:
      cpu: "2"
      memory: "2Gi"
  unsupported:
    podTemplate:
      spec:
        containers:
          - name: keycloak
            env:
              - name: KC_DB_URL
                value: "jdbc:postgresql://narwhal-db-rw.database.svc.cluster.local:5432/keycloak?sslmode=disable"
            # Relaxed probes (operator HONORS these) tolerate transient CPU spikes;
            # CPU/memory are set via spec.resources above (podTemplate resources ignored).
            livenessProbe:
              httpGet: { path: /health/live, port: 9000 }
              timeoutSeconds: 5
              periodSeconds: 15
              failureThreshold: 5
            readinessProbe:
              httpGet: { path: /health/ready, port: 9000 }
              timeoutSeconds: 5
              periodSeconds: 10
              failureThreshold: 5
            startupProbe:
              httpGet: { path: /health/started, port: 9000 }
              timeoutSeconds: 5
              periodSeconds: 5
              failureThreshold: 120
EOF

#=========================================
# Keycloak Operator auto-creates NetworkPolicy that blocks HBONE 15008.
# Cannot modify operator-managed policy → create separate allow policy.
#=========================================
echo "=== Creating keycloak-allow-hbone NetworkPolicy ==="

kubectl apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: keycloak-allow-hbone
  namespace: iam
spec:
  podSelector:
    matchLabels:
      app: keycloak
  ingress:
    - ports:
        - protocol: TCP
          port: 15008
        - protocol: TCP
          port: 8080
  policyTypes:
    - Ingress
EOF

#=========================================
# Wait for Keycloak pod to be ready
#=========================================
echo "Waiting for Keycloak pod to be ready..."
KEYCLOAK_READY=false
for attempt in $(seq 1 60); do
  # No `grep -q "Running"` gate here. It used to be one, written as
  #   if kubectl get pod ... | grep -q "Running" || true; then
  # where the `|| true` — added to dodge pipefail — made the condition ALWAYS true, so the
  # test it looked like it was doing never happened. `kubectl wait` is the real gate and
  # needs no precondition: it returns non-zero on its own timeout, which is exactly what
  # the loop wants. Deleting the dead check is the fix; making it a working check would
  # only duplicate what wait already does.
  if kubectl wait --for=condition=Ready pod -l app=keycloak -n iam --timeout=10s 2>/dev/null; then
    KEYCLOAK_READY=true
    echo "Keycloak pod is ready"
    break
  fi
  echo "Keycloak pod not ready, attempt ${attempt}/60..."
  sleep 10
done

if [ "${KEYCLOAK_READY}" = "false" ]; then
  echo "WARN: Keycloak pod did not become ready within timeout."
  echo "  Check: kubectl get pods -n iam"
  echo "  Check: kubectl logs -n iam -l app=keycloak --tail=50"
fi

#=========================================
# Verify HTTPS endpoint
#=========================================
echo "Verifying Keycloak HTTPS endpoint..."
KEYCLOAK_REACHABLE=false
for attempt in $(seq 1 20); do
  HTTP_CODE=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' \
    "https://keycloak.${DOMAIN}/health/ready" 2>/dev/null || echo "000")
  if [ "${HTTP_CODE}" = "200" ]; then
    KEYCLOAK_REACHABLE=true
    echo "Keycloak ready (HTTP ${HTTP_CODE})"
    break
  fi
  echo "Keycloak not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/20..."
  sleep 15
done

if [ "${KEYCLOAK_REACHABLE}" = "false" ]; then
  echo "ERROR: Keycloak HTTPS endpoint not reachable after timeout." >&2
  echo "  Check: kubectl get pods -n iam"
  echo "  Check: kubectl logs -n iam -l app=keycloak --tail=50"
  echo "  Check: canonical route from 08-6-tls-routes.sh was applied successfully" >&2
  exit 1
fi

#=========================================
# Summary
#=========================================
echo ""
echo "============================================="
echo "  Keycloak ${KEYCLOAK_VERSION} Installation Complete"
echo "============================================="
echo "  Admin URL: https://keycloak.${DOMAIN}"
echo "  Admin user: admin"
echo "  Admin password:"
echo "    kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d"
echo "  DB: narwhal-db-rw.database.svc.cluster.local / keycloak"
echo "============================================="
echo ""
echo "=== [11-keycloak.sh] 완료 ==="
