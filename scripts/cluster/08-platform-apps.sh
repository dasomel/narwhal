#!/bin/bash
set -euo pipefail

echo "=== Installing Platform Apps via Helm ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Helper: generate a random 24-char alphanumeric password
generate_password() {
  openssl rand -base64 16 | tr -d '=/+' | head -c 24
}

# Helper: ensure a Secret exists with a given key; create with generated value if absent.
# Usage: ensure_secret_key <secret-name> <namespace> <key>
# Prints the resolved value on stdout.
ensure_secret_key() {
  local secret_name="$1" ns="$2" key="$3"
  if ! kubectl get secret "${secret_name}" -n "${ns}" &>/dev/null; then
    local val
    val=$(generate_password)
    kubectl create secret generic "${secret_name}" \
      --from-literal="${key}=${val}" \
      -n "${ns}"
    echo "${val}"
  else
    kubectl get secret "${secret_name}" -n "${ns}" \
      -o jsonpath="{.data.${key}}" | base64 -d
  fi
}

#=========================================
# MetalLB (LoadBalancer for bare-metal)
#=========================================
echo "=== Installing MetalLB ==="
helm repo add metallb https://metallb.github.io/metallb
helm repo update metallb

helm upgrade --install metallb metallb/metallb \
  --namespace platform-system \
  --create-namespace \
  --version 0.15.3 \
  --set speaker.tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set speaker.tolerations[0].operator=Exists \
  --set speaker.tolerations[0].effect=NoSchedule || echo "WARN: MetalLB install issue, continuing..."

# Wait for MetalLB controller to be ready
echo "Waiting for MetalLB controller..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=controller -n platform-system --timeout=120s || true

# Apply MetalLB configuration (IP pool and L2 advertisement) with retry
echo "Applying MetalLB configuration..."
for attempt in 1 2 3 4 5; do
  if kubectl apply -f /home/vagrant/configs/gitops/resources/metallb-config.yaml 2>&1; then
    echo "MetalLB configuration applied"
    break
  fi
  echo "MetalLB config apply attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

echo "MetalLB installed"

#=========================================
# Traefik (Gateway API Controller)
#=========================================
echo "=== Installing Traefik ==="

# Install Gateway API CRDs (standard + experimental) with server-side apply
# to avoid field manager conflicts when Traefik Helm chart tries to install its own copies
echo "Installing Gateway API experimental CRDs..."
kubectl apply --server-side --force-conflicts -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml 2>&1 | grep -E "created|configured|unchanged|applied" || true

helm repo add traefik https://traefik.github.io/charts
helm repo update traefik

# Extract and apply Traefik CRDs separately with --server-side to avoid conflicts
echo "Applying Traefik CRDs with server-side apply..."
helm pull traefik/traefik --version 39.0.0 --untar --untardir /tmp/traefik-chart
for f in /tmp/traefik-chart/traefik/crds/*.yaml; do
  kubectl apply --server-side --force-conflicts -f "${f}" 2>&1 | tail -1
done
rm -rf /tmp/traefik-chart

helm upgrade --install traefik traefik/traefik \
  --namespace platform-system \
  --create-namespace \
  --version 39.0.0 \
  --skip-crds \
  --set service.type=LoadBalancer \
  --set ports.web.port=8000 \
  --set ports.web.exposedPort=80 \
  --set ports.websecure.port=8443 \
  --set ports.websecure.exposedPort=443 \
  --set ingressRoute.dashboard.enabled=true \
  --set providers.kubernetesGateway.enabled=true \
  --set providers.kubernetesCRD.enabled=true \
  --set providers.kubernetesCRD.allowExternalNameServices=true \
  --set providers.kubernetesIngress.enabled=false \
  --set gateway.enabled=false \
  --set logs.general.level=INFO || echo "WARN: Traefik install issue, continuing..."

echo "Traefik installed"

#=========================================
# cert-manager
#=========================================
echo "=== Installing cert-manager ==="
helm repo add jetstack https://charts.jetstack.io
helm repo update jetstack

helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace platform-system \
  --create-namespace \
  --version v1.19.3 \
  --set crds.enabled=true || echo "WARN: cert-manager install issue, continuing..."

# Create self-signed ClusterIssuer (bootstrap only — do not use directly for app certs)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-cluster-issuer
spec:
  selfSigned: {}
EOF

# Create Root CA Certificate (self-signed, valid 10 years)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: narwhal-root-ca
  namespace: platform-system
spec:
  isCA: true
  commonName: "Narwhal IDP Root CA"
  secretName: narwhal-root-ca-secret
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: selfsigned-cluster-issuer
    kind: ClusterIssuer
  duration: 87600h
  renewBefore: 8760h
EOF

# Wait for CA certificate to be ready
echo "Waiting for Root CA certificate..."
kubectl wait --for=condition=Ready certificate/narwhal-root-ca -n platform-system --timeout=60s

# Create CA ClusterIssuer (signs all application certificates)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: narwhal-ca-issuer
spec:
  ca:
    secretName: narwhal-root-ca-secret
EOF

echo "cert-manager installed with CA issuer"

#=========================================
# Prometheus Stack (Prometheus, Grafana, Alertmanager)
#=========================================
echo "=== Installing Prometheus Stack ==="
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update prometheus-community

# Grafana admin credentials — create namespace first, then build Secret
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

# grafana-secrets: admin credentials (used by GitOps YAML via grafana.admin.existingSecret)
# Keys: admin-user, admin-password
GRAFANA_ADMIN_PASS=$(generate_password)
if ! kubectl get secret grafana-secrets -n monitoring &>/dev/null; then
  kubectl create secret generic grafana-secrets \
    --from-literal=admin-user=admin \
    --from-literal=admin-password="${GRAFANA_ADMIN_PASS}" \
    -n monitoring
  echo "Grafana admin secret created (grafana-secrets)"
else
  GRAFANA_ADMIN_PASS=$(kubectl get secret grafana-secrets -n monitoring \
    -o jsonpath='{.data.admin-password}' | base64 -d)
  echo "Grafana admin secret already exists, reusing"
fi

helm upgrade --install prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --version 81.5.1 \
  --set grafana.assertNoLeakedSecrets=false \
  --set grafana.admin.existingSecret=grafana-secrets \
  --set grafana.admin.userKey=admin-user \
  --set grafana.admin.passwordKey=admin-password \
  --set grafana.persistence.enabled=true \
  --set grafana.persistence.storageClassName=nfs-csi \
  --set grafana.persistence.size=5Gi \
  --set prometheus.prometheusSpec.retention=7d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=nfs-csi \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=10Gi \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.storageClassName=nfs-csi \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.resources.requests.storage=5Gi \
  --set prometheus-node-exporter.tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set prometheus-node-exporter.tolerations[0].operator=Exists \
  --set prometheus-node-exporter.tolerations[0].effect=NoSchedule || echo "WARN: Prometheus Stack install issue, continuing..."

# Opt Grafana out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment prometheus-stack-grafana -n monitoring --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "Prometheus Stack installed"

#=========================================
# Loki (Log Aggregation) - Simple deployment
#=========================================
echo "=== Installing Loki ==="
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update grafana

cat <<'LOKIVALUES' > /tmp/loki-values.yaml
deploymentMode: SingleBinary
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1
  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h
  storage:
    type: filesystem
    bucketNames:
      chunks: chunks
      ruler: ruler
      admin: admin
singleBinary:
  replicas: 1
  persistence:
    enabled: true
    storageClass: nfs-csi
    size: 10Gi
read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0
gateway:
  enabled: false
chunksCache:
  enabled: false
resultsCache:
  enabled: false
monitoring:
  selfMonitoring:
    enabled: false
  lokiCanary:
    enabled: false
test:
  enabled: false
LOKIVALUES

helm upgrade --install loki grafana/loki \
  --namespace monitoring \
  --version 6.52.0 \
  -f /tmp/loki-values.yaml || echo "WARN: Loki install issue, continuing..."

rm /tmp/loki-values.yaml
echo "Loki installed"

#=========================================
# Promtail (Log Collector)
#=========================================
echo "=== Installing Promtail ==="
helm upgrade --install promtail grafana/promtail \
  --namespace monitoring \
  --version 6.17.1 \
  --set config.clients[0].url=http://loki:3100/loki/api/v1/push \
  --set tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set tolerations[0].operator=Exists \
  --set tolerations[0].effect=NoSchedule || echo "WARN: Promtail install issue, continuing..."

echo "Promtail installed"

#=========================================
# Tempo (Distributed Tracing)
#=========================================
echo "=== Installing Tempo ==="
helm upgrade --install tempo grafana/tempo \
  --namespace monitoring \
  --version 1.24.4 \
  --set tempo.storage.trace.backend=local \
  --set persistence.enabled=true \
  --set persistence.storageClassName=nfs-csi \
  --set persistence.size=10Gi || echo "WARN: Tempo install issue, continuing..."

echo "Tempo installed"

#=========================================
# Kyverno (Policy Management)
#=========================================
echo "=== Installing Kyverno ==="
helm repo add kyverno https://kyverno.github.io/kyverno/
helm repo update kyverno

helm upgrade --install kyverno kyverno/kyverno \
  --namespace platform-system \
  --create-namespace \
  --version 3.7.0 \
  --set admissionController.replicas=1 \
  --set backgroundController.replicas=1 \
  --set cleanupController.replicas=1 \
  --set reportsController.replicas=1 || echo "WARN: Kyverno install issue, continuing..."

echo "Kyverno installed"

#=========================================
# Headlamp (Kubernetes UI)
#=========================================
echo "=== Installing Headlamp ==="
helm repo add headlamp https://kubernetes-sigs.github.io/headlamp/
helm repo update headlamp

cat > /tmp/headlamp-values.yaml << 'EOF'
config:
  oidc:
    clientID: headlamp
    issuerURL: https://keycloak.local.narwhal.io/realms/kubernetes
    scopes: openid,profile,email,groups
    # clientSecret loaded from headlamp-oidc-secret (created by 11-keycloak.sh)
    secret:
      create: false
      name: headlamp-oidc-secret
    externalSecret:
      enabled: true
      name: headlamp-oidc-secret
initContainers:
  - name: ca-bundle
    image: ghcr.io/headlamp-k8s/headlamp:v0.40.0
    command: ['sh', '-c', 'cat /etc/ssl/certs/ca-certificates.crt /narwhal-ca/ca.crt > /combined/ca-certificates.crt']
    volumeMounts:
      - name: narwhal-ca
        mountPath: /narwhal-ca
        readOnly: true
      - name: combined-certs
        mountPath: /combined
volumes:
  - name: narwhal-ca
    secret:
      secretName: narwhal-ca-cert
  - name: combined-certs
    emptyDir: {}
volumeMounts:
  - name: combined-certs
    mountPath: /etc/ssl/certs/ca-certificates.crt
    subPath: ca-certificates.crt
    readOnly: true
EOF

helm upgrade --install headlamp headlamp/headlamp \
  --namespace devtools \
  --create-namespace \
  --version 0.40.0 \
  -f /tmp/headlamp-values.yaml || echo "WARN: Headlamp install issue, continuing..."

rm /tmp/headlamp-values.yaml

# Opt Headlamp out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment headlamp -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "Headlamp installed"

#=========================================
# OAuth2 Proxy (Gateway Authentication)
#=========================================
echo "=== Installing OAuth2 Proxy ==="
helm repo add oauth2-proxy https://oauth2-proxy.github.io/manifests
helm repo update oauth2-proxy

# oauth2-proxy-secrets is created by 11-keycloak.sh (cookie-secret + client-secret)
# Here we only configure non-secret settings; existingSecret handles credentials
cat > /tmp/oauth2-proxy-values.yaml << 'EOF'
replicaCount: 1
config:
  clientID: oauth2-proxy
  # clientSecret and cookieSecret loaded from existingSecret (created by 11-keycloak.sh)
  existingSecret: oauth2-proxy-secrets
  configFile: |-
    provider = "keycloak-oidc"
    provider_display_name = "Keycloak"
    oidc_issuer_url = "https://keycloak.local.narwhal.io/realms/kubernetes"
    redirect_url = "https://oauth2-proxy.local.narwhal.io/oauth2/callback"
    upstreams = ["static://200"]
    email_domains = ["*"]
    cookie_secure = true
    cookie_domains = [".local.narwhal.io"]
    whitelist_domains = [".local.narwhal.io"]
    set_xauthrequest = true
    set_authorization_header = true
    pass_access_token = true
    pass_authorization_header = true
    skip_provider_button = true
    code_challenge_method = "S256"
    insecure_oidc_skip_issuer_verification = true
    ssl_insecure_skip_verify = true
    allowed_groups = ["cluster-admin", "developer", "viewer"]
extraArgs:
  - --skip-jwt-bearer-tokens=true
service:
  type: ClusterIP
  portNumber: 80
EOF

helm upgrade --install oauth2-proxy oauth2-proxy/oauth2-proxy \
  --namespace iam \
  --create-namespace \
  --version 10.1.3 \
  -f /tmp/oauth2-proxy-values.yaml || echo "WARN: OAuth2 Proxy install issue, continuing..."

rm /tmp/oauth2-proxy-values.yaml

# Opt OAuth2-Proxy out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment oauth2-proxy -n iam --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "OAuth2 Proxy installed"

#=========================================
# SeaweedFS (S3-compatible Object Storage)
#=========================================
echo "=== Installing SeaweedFS ==="
helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs/helm
helm repo update seaweedfs

helm upgrade --install seaweedfs seaweedfs/seaweedfs \
  --namespace storage \
  --create-namespace \
  --version 4.0.407 \
  --set global.storageClass=nfs-csi \
  --set master.enabled=true \
  --set master.replicas=1 \
  --set master.data.type=persistentVolumeClaim \
  --set master.data.size=1Gi \
  --set master.data.storageClass=nfs-csi \
  --set volume.enabled=true \
  --set volume.replicas=1 \
  --set volume.data.type=persistentVolumeClaim \
  --set volume.data.size=50Gi \
  --set volume.data.storageClass=nfs-csi \
  --set filer.enabled=true \
  --set filer.replicas=1 \
  --set filer.data.type=persistentVolumeClaim \
  --set filer.data.size=5Gi \
  --set filer.data.storageClass=nfs-csi \
  --set filer.s3.enabled=true \
  --set filer.s3.port=8333 \
  --set filer.s3.allowEmptyFolder=true \
  --set s3.enabled=true || echo "WARN: SeaweedFS install issue, continuing..."

# Create S3 buckets for platform apps
# All apps using SeaweedFS S3: Tempo, Velero, Loki, CNPG backup
echo "Creating SeaweedFS S3 buckets..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=seaweedfs,app.kubernetes.io/component=filer -n storage --timeout=120s || true
for bucket in tempo velero loki cnpg-backup; do
  echo "  Creating bucket: ${bucket}"
  kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell" 2>/dev/null || true
done

# Verify buckets were created
echo "Verifying S3 buckets..."
BUCKET_LIST=$(kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.list' | weed shell" 2>/dev/null || true)
for bucket in tempo velero loki cnpg-backup; do
  if echo "${BUCKET_LIST}" | grep -q "${bucket}"; then
    echo "  ✓ ${bucket}"
  else
    echo "  ✗ ${bucket} - WARN: bucket may not exist"
  fi
done
echo "S3 buckets ready"

echo "SeaweedFS installed"

#=========================================
# Harbor (Container Registry) - ARM64 images
#=========================================
echo "=== Installing Harbor ==="
helm repo add harbor https://helm.goharbor.io
helm repo update harbor

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

helm upgrade --install harbor harbor/harbor \
  --namespace devtools \
  --version 1.18.2 \
  --set expose.type=clusterIP \
  --set expose.tls.enabled=false \
  --set externalURL=http://harbor.local \
  --set existingSecretAdminPassword=harbor-secrets \
  --set existingSecretAdminPasswordKey=HARBOR_ADMIN_PASSWORD \
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
  --set core.image.repository=ghcr.io/dasomel/goharbor/harbor-core \
  --set core.image.tag=latest \
  --set jobservice.image.repository=ghcr.io/dasomel/goharbor/harbor-jobservice \
  --set jobservice.image.tag=latest \
  --set registry.registry.image.repository=ghcr.io/dasomel/goharbor/registry-photon \
  --set registry.registry.image.tag=latest \
  --set registry.controller.image.repository=ghcr.io/dasomel/goharbor/harbor-registryctl \
  --set registry.controller.image.tag=latest \
  --set portal.image.repository=ghcr.io/dasomel/goharbor/harbor-portal \
  --set portal.image.tag=latest \
  --set nginx.image.repository=ghcr.io/dasomel/goharbor/nginx-photon \
  --set nginx.image.tag=latest \
  --set redis.internal.image.repository=ghcr.io/dasomel/goharbor/redis-photon \
  --set redis.internal.image.tag=latest \
  --set exporter.image.repository=ghcr.io/dasomel/goharbor/harbor-exporter \
  --set exporter.image.tag=latest || echo "WARN: Harbor install issue, continuing..."

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

# Wait for Harbor API to respond
HARBOR_API="https://harbor.local.narwhal.io/api/v2.0"
for attempt in $(seq 1 15); do
  HARBOR_HEALTH=$(curl -sk -o /dev/null -w '%{http_code}' "${HARBOR_API}/systeminfo" 2>/dev/null || echo "000")
  if [ "${HARBOR_HEALTH}" = "200" ]; then
    echo "Harbor API ready"
    break
  fi
  echo "Harbor API not ready (HTTP ${HARBOR_HEALTH}), attempt ${attempt}/15..."
  sleep 10
done

if [ "${HARBOR_HEALTH}" = "200" ]; then
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

#=========================================
# OpenBao (Secret Management)
#=========================================
echo "=== Installing OpenBao ==="
helm repo add openbao https://openbao.github.io/openbao-helm
helm repo update openbao

helm upgrade --install openbao openbao/openbao \
  --namespace storage \
  --create-namespace \
  --version 0.11.0 \
  --set server.image.tag=2.2.0 \
  --set server.ha.enabled=false \
  --set server.ha.replicas=1 \
  --set server.ha.raft.enabled=true \
  --set server.dataStorage.enabled=true \
  --set server.dataStorage.storageClass=nfs-csi \
  --set server.dataStorage.size=10Gi \
  --set server.auditStorage.enabled=true \
  --set server.auditStorage.storageClass=nfs-csi \
  --set server.auditStorage.size=5Gi \
  --set ui.enabled=true || echo "WARN: OpenBao install issue, continuing..."

# Auto init + unseal OpenBao
echo "Waiting for OpenBao pod..."
kubectl wait --for=condition=Ready=false pod/openbao-0 -n storage --timeout=120s 2>/dev/null || true
sleep 5

OPENBAO_INITIALIZED=$(kubectl exec openbao-0 -n storage -- bao status -format=json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("initialized",""))' 2>/dev/null || echo "")

if [ "${OPENBAO_INITIALIZED}" = "True" ]; then
  echo "OpenBao already initialized, checking unseal key..."
  UNSEAL_KEY=$(kubectl get secret openbao-init -n storage -o jsonpath='{.data.unseal_keys_b64}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
  if [ -n "${UNSEAL_KEY}" ]; then
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n storage -- bao operator unseal "${UNSEAL_KEY}" || true
  else
    echo "WARN: OpenBao initialized but unseal key not found in openbao-init secret"
  fi
elif [ "${OPENBAO_INITIALIZED}" = "False" ]; then
  echo "Initializing OpenBao..."
  INIT_JSON=$(kubectl exec openbao-0 -n storage -- bao operator init -key-shares=1 -key-threshold=1 -format=json 2>/dev/null || echo "")
  if [ -n "${INIT_JSON}" ]; then
    UNSEAL_KEY=$(echo "${INIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["unseal_keys_b64"][0])')
    ROOT_TOKEN=$(echo "${INIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["root_token"])')
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n storage -- bao operator unseal "${UNSEAL_KEY}" || true
    echo "Saving credentials to openbao-init secret..."
    kubectl create secret generic openbao-init -n storage \
      --from-literal=unseal_keys_b64="${UNSEAL_KEY}" \
      --from-literal=root_token="${ROOT_TOKEN}" \
      --dry-run=client -o yaml | kubectl apply -f -
    echo "OpenBao initialized and unsealed"
  else
    echo "WARN: OpenBao init failed, manual init required"
  fi
else
  echo "WARN: Could not determine OpenBao state, skipping init"
fi

echo "OpenBao installed"

#=========================================
# Velero (Backup & Restore)
#=========================================
echo "=== Installing Velero ==="
helm repo add vmware-tanzu https://vmware-tanzu.github.io/helm-charts
helm repo update vmware-tanzu

# S3 credentials — prefer environment overrides, fall back to Secret or defaults
S3_ACCESS_KEY="${S3_ACCESS_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d || echo "admin")}"
S3_SECRET_KEY="${S3_SECRET_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d || echo "")}"

# Persist S3 credentials into a dedicated Secret (idempotent)
# 'cloud' key uses AWS credentials file format required by velero-plugin-for-aws
# (referenced by gitops/apps/velero.yaml as existingSecret: velero-s3-credentials)
kubectl create namespace storage --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic velero-s3-credentials \
  --from-literal=access-key="${S3_ACCESS_KEY}" \
  --from-literal=secret-key="${S3_SECRET_KEY}" \
  --from-literal=cloud="[default]
aws_access_key_id = ${S3_ACCESS_KEY}
aws_secret_access_key = ${S3_SECRET_KEY}" \
  -n storage --dry-run=client -o yaml | kubectl apply -f -

cat > /tmp/velero-values.yaml << EOF
initContainers:
  - name: velero-plugin-for-aws
    image: velero/velero-plugin-for-aws:v1.11.1
    volumeMounts:
      - mountPath: /target
        name: plugins
configuration:
  backupStorageLocation:
    - name: default
      provider: aws
      bucket: velero
      config:
        region: us-east-1
        s3ForcePathStyle: "true"
        s3Url: http://seaweedfs-s3.storage.svc.cluster.local:8333
  volumeSnapshotLocation:
    - name: default
      provider: aws
      config:
        region: us-east-1
  defaultBackupStorageLocation: default
  uploaderType: kopia
  defaultVolumesToFsBackup: true
credentials:
  useSecret: true
  secretContents:
    cloud: |
      [default]
      aws_access_key_id = ${S3_ACCESS_KEY}
      aws_secret_access_key = ${S3_SECRET_KEY}
snapshotsEnabled: false
# Disable CRD upgrade hook - alpine/k8s musl binaries can't exec in velero glibc container
upgradeCRDs: false
kubectl:
  image:
    # docker.io: registry.k8s.io/kubectl은 distroless(shell 없음), ghcr.io 대안 부재
    repository: docker.io/alpine/k8s
    tag: "1.31.4"
deployNodeAgent: true
nodeAgent:
  podVolumePath: /var/lib/kubelet/pods
  privileged: true
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
EOF

helm upgrade --install velero vmware-tanzu/velero \
  --namespace storage \
  --create-namespace \
  --version 11.3.2 \
  -f /tmp/velero-values.yaml || echo "WARN: Velero install issue, continuing..."

rm /tmp/velero-values.yaml
echo "Velero installed"

#=========================================
# Apply Traefik Gateway Routes
#=========================================
echo "=== Applying Traefik Gateway Routes ==="

# Wait for Traefik to be ready (may take time during initial provisioning)
echo "Waiting for Traefik deployment..."
for attempt in $(seq 1 12); do
  if kubectl get deployment traefik -n platform-system >/dev/null 2>&1; then
    kubectl wait --for=condition=Available deployment/traefik -n platform-system --timeout=60s 2>/dev/null && break
  fi
  echo "Traefik not ready yet, attempt ${attempt}/12..."
  sleep 15
done

# Wait for GatewayClass to be created by Traefik
echo "Waiting for Traefik GatewayClass..."
for attempt in $(seq 1 10); do
  if kubectl get gatewayclass traefik >/dev/null 2>&1; then
    echo "Traefik GatewayClass ready"
    break
  fi
  echo "GatewayClass not ready, attempt ${attempt}/10..."
  sleep 10
done

kubectl apply -f /home/vagrant/configs/gitops/resources/traefik-routes.yaml || true

# Wait for TLS certificate to be ready
echo "Waiting for TLS certificate..."
for attempt in $(seq 1 10); do
  CERT_READY=$(kubectl get certificate traefik-tls -n platform-system -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
  if [ "${CERT_READY}" = "True" ]; then
    echo "TLS certificate ready"
    break
  fi
  echo "TLS certificate not ready yet, attempt ${attempt}/10..."
  sleep 10
done

#=========================================
# Distribute CA cert to SSO app namespaces
#=========================================
echo "=== Distributing CA cert to SSO namespaces ==="
CA_CERT=$(kubectl get secret traefik-tls -n platform-system -o jsonpath='{.data.ca\.crt}' 2>/dev/null || echo "")
if [ -n "${CA_CERT}" ]; then
  for ns in devtools iam monitoring storage; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null
    kubectl create secret generic narwhal-ca-cert \
      --from-literal=ca.crt="$(echo "${CA_CERT}" | base64 -d)" \
      -n "${ns}" --dry-run=client -o yaml | kubectl apply -f -
  done
  echo "CA cert distributed to SSO namespaces"
else
  echo "WARN: traefik-tls CA cert not found, SSO apps may not verify Keycloak TLS"
fi

echo "=== Platform Apps Installation Complete ==="
echo ""
echo "Installed apps:"
echo "  - metallb (LoadBalancer)"
echo "  - traefik (Gateway API)"
echo "  - cert-manager (TLS automation)"
echo "  - prometheus-stack (Monitoring)"
echo "  - loki (Log aggregation)"
echo "  - promtail (Log collector)"
echo "  - tempo (Distributed tracing)"
echo "  - kyverno (Policy management)"
echo "  - headlamp (Kubernetes UI)"
echo "  - oauth2-proxy (Gateway auth)"
echo "  - seaweedfs (S3 storage)"
echo "  - harbor (Container registry)"
echo "  - openbao (Secret management)"
echo "  - velero (Backup)"
echo ""
echo "Access via DNS (configure client DNS to 192.168.56.10):"
echo "  ArgoCD:   http://argocd.local.narwhal.io"
echo "  Grafana:  http://grafana.local.narwhal.io"
echo "  Gitea:    http://gitea.local.narwhal.io"
echo "  Harbor:   http://harbor.local.narwhal.io"
echo "  Keycloak: http://keycloak.local.narwhal.io"
echo "  Headlamp: http://headlamp.local.narwhal.io"
echo "  OpenBao:  http://openbao.local.narwhal.io"
echo ""
echo "MetalLB IP: 192.168.56.200"
echo "DNS Server: 192.168.56.10 (master node)"
echo ""
