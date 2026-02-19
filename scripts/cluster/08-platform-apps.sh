#!/bin/bash
set -euo pipefail

echo "=== Installing Platform Apps via Helm ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# MetalLB (LoadBalancer for bare-metal)
#=========================================
echo "=== Installing MetalLB ==="
helm repo add metallb https://metallb.github.io/metallb
helm repo update metallb

helm upgrade --install metallb metallb/metallb \
  --namespace metallb-system \
  --create-namespace \
  --version 0.15.3 \
  --set speaker.tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set speaker.tolerations[0].operator=Exists \
  --set speaker.tolerations[0].effect=NoSchedule || echo "WARN: MetalLB install issue, continuing..."

# Wait for MetalLB controller to be ready
echo "Waiting for MetalLB controller..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=controller -n metallb-system --timeout=120s || true

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
  --namespace traefik \
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
  --namespace cert-manager \
  --create-namespace \
  --version v1.19.3 \
  --set crds.enabled=true || echo "WARN: cert-manager install issue, continuing..."

# Create self-signed ClusterIssuer
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-cluster-issuer
spec:
  selfSigned: {}
EOF

echo "cert-manager installed"

#=========================================
# Prometheus Stack (Prometheus, Grafana, Alertmanager)
#=========================================
echo "=== Installing Prometheus Stack ==="
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update prometheus-community

helm upgrade --install prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --version 81.5.1 \
  --set grafana.assertNoLeakedSecrets=false \
  --set grafana.adminPassword=admin \
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
  --namespace kyverno \
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

helm upgrade --install headlamp headlamp/headlamp \
  --namespace headlamp \
  --create-namespace \
  --version 0.40.0 \
  --set config.oidc.clientID=headlamp \
  --set config.oidc.clientSecret=headlamp-secret \
  --set config.oidc.issuerURL=https://keycloak.local.narwhal.io/realms/kubernetes \
  --set config.oidc.scopes="openid\,profile\,email\,groups" \
  --set "volumes[0].name=narwhal-ca" \
  --set "volumes[0].secret.secretName=narwhal-ca-cert" \
  --set "volumeMounts[0].name=narwhal-ca" \
  --set "volumeMounts[0].mountPath=/etc/ssl/certs/narwhal-ca.crt" \
  --set "volumeMounts[0].subPath=ca.crt" \
  --set "volumeMounts[0].readOnly=true" || echo "WARN: Headlamp install issue, continuing..."

echo "Headlamp installed"

#=========================================
# OAuth2 Proxy (Gateway Authentication)
#=========================================
echo "=== Installing OAuth2 Proxy ==="
helm repo add oauth2-proxy https://oauth2-proxy.github.io/manifests
helm repo update oauth2-proxy

# Generate random cookie secret
COOKIE_SECRET=$(openssl rand -hex 16)

cat > /tmp/oauth2-proxy-values.yaml << EOF
replicaCount: 1
config:
  clientID: oauth2-proxy
  clientSecret: oauth2-proxy-secret
  cookieSecret: "${COOKIE_SECRET}"
  configFile: |-
    provider = "keycloak-oidc"
    provider_display_name = "Keycloak"
    oidc_issuer_url = "https://keycloak.local.narwhal.io/realms/kubernetes"
    redirect_url = "http://oauth2-proxy.local.narwhal.io/oauth2/callback"
    upstreams = ["static://200"]
    email_domains = ["*"]
    cookie_secure = false
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
extraArgs:
  - --skip-jwt-bearer-tokens=true
service:
  type: ClusterIP
  portNumber: 80
EOF

helm upgrade --install oauth2-proxy oauth2-proxy/oauth2-proxy \
  --namespace oauth2-proxy \
  --create-namespace \
  --version 10.1.3 \
  -f /tmp/oauth2-proxy-values.yaml || echo "WARN: OAuth2 Proxy install issue, continuing..."

rm /tmp/oauth2-proxy-values.yaml
echo "OAuth2 Proxy installed"

#=========================================
# SeaweedFS (S3-compatible Object Storage)
#=========================================
echo "=== Installing SeaweedFS ==="
helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs/helm
helm repo update seaweedfs

helm upgrade --install seaweedfs seaweedfs/seaweedfs \
  --namespace seaweedfs \
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
echo "Creating SeaweedFS S3 buckets..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=seaweedfs,app.kubernetes.io/component=filer -n seaweedfs --timeout=120s || true
for bucket in tempo velero; do
  kubectl exec -n seaweedfs seaweedfs-filer-0 -- sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell" 2>/dev/null || true
done
echo "S3 buckets created"

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
kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f -

# Apply ExternalName service to connect to narwhal-db from harbor namespace
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
      targetPort: 5432
EOF

helm upgrade --install harbor harbor/harbor \
  --namespace harbor \
  --version 1.18.2 \
  --set expose.type=clusterIP \
  --set expose.tls.enabled=false \
  --set externalURL=http://harbor.local \
  --set harborAdminPassword=Harbor12345 \
  --set database.type=external \
  --set database.external.host=harbor-db-rw \
  --set database.external.port=5432 \
  --set database.external.username=harbor \
  --set database.external.password=harbor-db-password \
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

echo "Harbor installed"

#=========================================
# OpenBao (Secret Management)
#=========================================
echo "=== Installing OpenBao ==="
helm repo add openbao https://openbao.github.io/openbao-helm
helm repo update openbao

helm upgrade --install openbao openbao/openbao \
  --namespace openbao \
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
kubectl wait --for=condition=Ready=false pod/openbao-0 -n openbao --timeout=120s 2>/dev/null || true
sleep 5

OPENBAO_INITIALIZED=$(kubectl exec openbao-0 -n openbao -- bao status -format=json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("initialized",""))' 2>/dev/null || echo "")

if [ "${OPENBAO_INITIALIZED}" = "True" ]; then
  echo "OpenBao already initialized, checking unseal key..."
  UNSEAL_KEY=$(kubectl get secret openbao-init -n openbao -o jsonpath='{.data.unseal_keys_b64}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
  if [ -n "${UNSEAL_KEY}" ]; then
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n openbao -- bao operator unseal "${UNSEAL_KEY}" || true
  else
    echo "WARN: OpenBao initialized but unseal key not found in openbao-init secret"
  fi
elif [ "${OPENBAO_INITIALIZED}" = "False" ]; then
  echo "Initializing OpenBao..."
  INIT_JSON=$(kubectl exec openbao-0 -n openbao -- bao operator init -key-shares=1 -key-threshold=1 -format=json 2>/dev/null || echo "")
  if [ -n "${INIT_JSON}" ]; then
    UNSEAL_KEY=$(echo "${INIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["unseal_keys_b64"][0])')
    ROOT_TOKEN=$(echo "${INIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["root_token"])')
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n openbao -- bao operator unseal "${UNSEAL_KEY}" || true
    echo "Saving credentials to openbao-init secret..."
    kubectl create secret generic openbao-init -n openbao \
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

cat > /tmp/velero-values.yaml << 'EOF'
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
        s3Url: http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333
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
      aws_access_key_id = admin
      aws_secret_access_key = admin
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
  --namespace velero \
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
  if kubectl get deployment traefik -n traefik >/dev/null 2>&1; then
    kubectl wait --for=condition=Available deployment/traefik -n traefik --timeout=60s 2>/dev/null && break
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
  CERT_READY=$(kubectl get certificate traefik-tls -n traefik -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "")
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
CA_CERT=$(kubectl get secret traefik-tls -n traefik -o jsonpath='{.data.ca\.crt}' 2>/dev/null || echo "")
if [ -n "${CA_CERT}" ]; then
  for ns in headlamp gitea harbor monitoring oauth2-proxy; do
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
