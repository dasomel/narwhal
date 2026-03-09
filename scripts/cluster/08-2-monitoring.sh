#!/bin/bash
set -euo pipefail
source /home/vagrant/scripts/common/lib.sh

echo "=== Installing Monitoring Apps (Prometheus, Loki, Promtail, Tempo) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

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

echo "=== Monitoring Apps Installation Complete ==="
