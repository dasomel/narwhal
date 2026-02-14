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
  --version 0.15.3 || echo "WARN: MetalLB install issue, continuing..."

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

# Install experimental Gateway API CRDs (TCPRoute, TLSRoute, UDPRoute)
# Required by Traefik's kubernetesGateway provider with experimentalChannel
echo "Installing Gateway API experimental CRDs..."
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.3.0/experimental-install.yaml 2>&1 | grep -E "created|configured" || true

helm repo add traefik https://traefik.github.io/charts
helm repo update traefik

helm upgrade --install traefik traefik/traefik \
  --namespace traefik \
  --create-namespace \
  --version 39.0.0 \
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

helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --version 81.5.1 \
  --set grafana.adminPassword=admin \
  --set grafana.persistence.enabled=true \
  --set grafana.persistence.storageClassName=nfs-csi \
  --set grafana.persistence.size=5Gi \
  --set prometheus.prometheusSpec.retention=7d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=nfs-csi \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=10Gi \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.storageClassName=nfs-csi \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.resources.requests.storage=5Gi || echo "WARN: Prometheus Stack install issue, continuing..."

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
  --set config.clients[0].url=http://loki:3100/loki/api/v1/push || echo "WARN: Promtail install issue, continuing..."

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
  --set config.oidc.scopes="openid\,profile\,email\,groups" || echo "WARN: Headlamp install issue, continuing..."

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
kubectl:
  image:
    # docker.io: registry.k8s.io/kubectl은 distroless(shell 없음), ghcr.io 대안 부재
    repository: docker.io/alpine/k8s
    tag: "1.35.0"
deployNodeAgent: true
nodeAgent:
  podVolumePath: /var/lib/kubelet/pods
  privileged: true
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
kubectl apply -f /home/vagrant/configs/gitops/resources/traefik-routes.yaml || true

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
