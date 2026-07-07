#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

echo "=== Installing Registry Apps (Harbor) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Harbor (Container Registry) - ARM64 images
#=========================================
echo "=== Installing Harbor ==="
for attempt in 1 2 3 4 5; do
  if helm repo add harbor https://helm.goharbor.io && helm repo update harbor; then
    break
  fi
  echo "Helm repo harbor attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Wait for unified PostgreSQL cluster (narwhal-db) to be ready
echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true

# Create namespace first to apply ExternalName service
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -

# Harbor admin credentials — build harbor-secrets (used by GitOps YAML)
# Key: HARBOR_ADMIN_PASSWORD (required by harbor chart existingSecretAdminPasswordKey)
HARBOR_ADMIN_PASS=$(generate_password)
if ! kubectl get secret harbor-secrets -n devtools &>/dev/null; then
  kubectl create secret generic harbor-secrets \
    --from-literal=HARBOR_ADMIN_PASSWORD="${HARBOR_ADMIN_PASS}" \
    -n devtools
  echo "Harbor admin secret created (harbor-secrets)"
else
  HARBOR_ADMIN_PASS=$(kubectl get secret harbor-secrets -n devtools \
    -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)
  echo "Harbor admin secret already exists, reusing"
fi

# Harbor internal shared secrets (secretKey/core/jobservice) — externalized (no plaintext in git)
# Keys read by harbor chart: secretKey (existingSecretSecretKey), secret (core.existingSecret), JOBSERVICE_SECRET (jobservice.existingSecret)
if ! kubectl get secret harbor-shared-secrets -n devtools &>/dev/null; then
  kubectl create secret generic harbor-shared-secrets \
    --from-literal=secretKey="$(openssl rand -hex 8)" \
    --from-literal=secret="$(openssl rand -hex 8)" \
    --from-literal=JOBSERVICE_SECRET="$(openssl rand -hex 8)" \
    -n devtools
  echo "Harbor shared secrets created (harbor-shared-secrets)"
else
  echo "Harbor shared secrets already exist, reusing"
fi

# Harbor DB password — provided by 07-cnpg.sh via narwhal-db-credentials Secret
HARBOR_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
  -o jsonpath='{.data.harbor-password}' | base64 -d)

# harbor-db-credentials: cross-namespace copy for harbor chart (devtools ns)
# Harbor chart external DB existingSecret requires key 'password' in same namespace
if ! kubectl get secret harbor-db-credentials -n devtools &>/dev/null; then
  kubectl create secret generic harbor-db-credentials \
    --from-literal=password="${HARBOR_DB_PASS}" \
    -n devtools
  echo "Harbor DB credentials secret created (harbor-db-credentials in devtools)"
else
  echo "Harbor DB credentials secret already exists"
fi

# Apply ExternalName service to connect to narwhal-db from devtools namespace
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
      targetPort: 5432
EOF

for attempt in 1 2 3 4 5; do
  if helm upgrade --install harbor harbor/harbor \
    --namespace devtools \
    --version 1.19.1 \
    --set expose.type=clusterIP \
    --set expose.tls.enabled=false \
    --set externalURL=http://harbor.local \
    --set existingSecretAdminPassword=harbor-secrets \
    --set existingSecretAdminPasswordKey=HARBOR_ADMIN_PASSWORD \
    --set existingSecretSecretKey=harbor-shared-secrets \
    --set core.existingSecret=harbor-shared-secrets \
    --set jobservice.existingSecret=harbor-shared-secrets \
    --set database.type=external \
    --set database.external.host=harbor-db-rw \
    --set database.external.port=5432 \
    --set database.external.username=harbor \
    --set database.external.existingSecret=harbor-db-credentials \
    --set database.external.coreDatabase=harbor \
    --set persistence.enabled=true \
    --set persistence.persistentVolumeClaim.registry.storageClass=nfs-csi \
    --set persistence.persistentVolumeClaim.registry.size=20Gi \
    --set persistence.persistentVolumeClaim.jobservice.storageClass=nfs-csi \
    --set persistence.persistentVolumeClaim.trivy.storageClass=nfs-csi \
    --set redis.type=internal \
    --set trivy.enabled=false \
    --set metrics.enabled=false \
    --set core.image.repository=ghcr.io/dasomel/goharbor/harbor-core \
    --set core.image.tag=v2.15.1 \
    --set jobservice.image.repository=ghcr.io/dasomel/goharbor/harbor-jobservice \
    --set jobservice.image.tag=v2.15.1 \
    --set registry.registry.image.repository=ghcr.io/dasomel/goharbor/registry-photon \
    --set registry.registry.image.tag=v2.15.1 \
    --set registry.controller.image.repository=ghcr.io/dasomel/goharbor/harbor-registryctl \
    --set registry.controller.image.tag=v2.15.1 \
    --set portal.image.repository=ghcr.io/dasomel/goharbor/harbor-portal \
    --set portal.image.tag=v2.15.1 \
    --set nginx.image.repository=ghcr.io/dasomel/goharbor/nginx-photon \
    --set nginx.image.tag=v2.15.1 \
    --set redis.internal.image.repository=ghcr.io/dasomel/goharbor/redis-photon \
    --set redis.internal.image.tag=v2.15.1 \
    --set exporter.image.repository=ghcr.io/dasomel/goharbor/harbor-exporter \
    --set exporter.image.tag=v2.15.0-build.32; then
    break
  fi
  echo "Harbor install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Opt Harbor SSO-facing pods out of Istio ambient mesh (cookie handling)
for harbor_deploy in harbor-core harbor-nginx harbor-portal; do
  kubectl patch deployment "${harbor_deploy}" -n devtools --type='json' \
    -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true
done

echo "Harbor installed"

#=========================================
# Harbor: Project Group Members
#=========================================
echo "Configuring Harbor project group members..."

# Wait for Harbor to be ready before API calls
echo "Waiting for Harbor core pod..."
kubectl wait --for=condition=Ready pod -l app=harbor,component=core -n devtools --timeout=300s || true

# Wait for Harbor API to respond via internal ClusterIP
# (harbor.local.narwhal.internal DNS is not yet available — dnsmasq runs at step 10)
HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
HARBOR_HEALTH="000"
for attempt in $(seq 1 15); do
  if [ -z "${HARBOR_CORE_IP}" ]; then
    HARBOR_CORE_IP=$(kubectl get svc harbor-core -n devtools -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
  fi
  HARBOR_HEALTH=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' "http://${HARBOR_CORE_IP}/api/v2.0/systeminfo" 2>/dev/null || echo "000")
  if [ "${HARBOR_HEALTH}" = "200" ]; then
    echo "Harbor API ready (internal ClusterIP)"
    break
  fi
  echo "Harbor API not ready (HTTP ${HARBOR_HEALTH}), attempt ${attempt}/15..."
  sleep 10
done

if [ "${HARBOR_HEALTH}" = "200" ]; then
  HARBOR_API="http://${HARBOR_CORE_IP}/api/v2.0"
  # Add developer group → project Developer role (roleId=2)
  curl -sk -X POST "${HARBOR_API}/projects/1/members" \
    -H "Content-Type: application/json" \
    -u "admin:${HARBOR_ADMIN_PASS}" \
    -d '{"role_id":2,"member_group":{"group_name":"developer","group_type":3}}' 2>/dev/null || true
  echo "Harbor: developer group → project Developer"

  # Add viewer group → project Guest role (roleId=3)
  curl -sk -X POST "${HARBOR_API}/projects/1/members" \
    -H "Content-Type: application/json" \
    -u "admin:${HARBOR_ADMIN_PASS}" \
    -d '{"role_id":3,"member_group":{"group_name":"viewer","group_type":3}}' 2>/dev/null || true
  echo "Harbor: viewer group → project Guest"
else
  echo "WARN: Harbor API not available, skipping project member setup"
fi

echo "=== Registry Apps Installation Complete ==="
