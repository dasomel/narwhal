#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh
source /home/vagrant/scripts/common/lib.sh

echo "=== Installing Monitoring Apps (Prometheus, Loki, Alloy, Tempo) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Prometheus Stack (Prometheus, Grafana, Alertmanager)
#=========================================
echo "=== Installing Prometheus Stack ==="

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

PROMETHEUS_STACK_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install prometheus-stack "$(chart kube-prometheus-stack)" \
    --force-conflicts \
    --namespace monitoring \
    --create-namespace \
    --version 86.2.3 \
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
    --set prometheus-node-exporter.tolerations[0].effect=NoSchedule; then
    PROMETHEUS_STACK_OK=true; break
  fi
  echo "Prometheus Stack install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${PROMETHEUS_STACK_OK}" != true ]; then
  echo "ERROR: Prometheus Stack install failed after 5 attempts." >&2
  exit 1
fi

# Opt Grafana out of Istio ambient mesh (SSO cookie handling)
kubectl patch deployment prometheus-stack-grafana -n monitoring --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]' 2>/dev/null || true

echo "Prometheus Stack installed"

#=========================================
# Loki (Log Aggregation) - Simple deployment
#=========================================
echo "=== Installing Loki ==="

# D-grafana-community: grafana/loki and grafana/tempo went GEL-only; the OSS
# community fork moved to this separate repo (see VERSIONS.md "Resolved this
# cycle" note). Kept as a distinct repo alias, not a rename of `grafana`,
# since other grafana/* charts (promtail's old home, if ever reused) still
# live at the original repo.

cat <<'LOKIVALUES' > /tmp/loki-values.yaml
deploymentMode: Monolithic
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
  lokiCanary:
    enabled: false
test:
  enabled: false
LOKIVALUES

LOKI_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install loki "$(chart loki)" \
    --force-conflicts \
    --namespace monitoring \
    --version 18.4.0 \
    -f /tmp/loki-values.yaml; then
    LOKI_OK=true; break
  fi
  echo "Loki install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${LOKI_OK}" != true ]; then
  echo "ERROR: Loki install failed after 5 attempts." >&2
  exit 1
fi

rm /tmp/loki-values.yaml
echo "Loki installed"

#=========================================
# Grafana Alloy via k8s-monitoring (Log Collector)
# Replaces Promtail (EOL 2026-03-02, no functional fixes upstream since).
# k8s-monitoring is a meta-chart that deploys alloy-operator + an Alloy
# DaemonSet scoped to pod-log collection only (podLogsViaLoki) — every
# other feature (clusterMetrics, hostMetrics, clusterEvents, kube-state-
# metrics, node-exporter) defaults to false, so this does not duplicate
# anything already provided by the prometheus-stack installed above.
#=========================================
echo "=== Installing Grafana Alloy (k8s-monitoring) ==="
cat <<'K8SMONVALUES' > /tmp/k8s-monitoring-values.yaml
cluster:
  name: "narwhal"
destinations:
  loki:
    type: loki
    url: http://loki:3100/loki/api/v1/push
collectors:
  alloy-logs:
    presets: [small, daemonset, filesystem-log-reader]
podLogsViaLoki:
  enabled: true
  collector: alloy-logs
  destinations: []
K8SMONVALUES

K8S_MONITORING_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install k8s-monitoring "$(chart k8s-monitoring)" \
    --force-conflicts \
    --namespace monitoring \
    --version 4.2.0 \
    -f /tmp/k8s-monitoring-values.yaml; then
    K8S_MONITORING_OK=true; break
  fi
  echo "k8s-monitoring install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${K8S_MONITORING_OK}" != true ]; then
  echo "ERROR: k8s-monitoring install failed after 5 attempts." >&2
  exit 1
fi

rm /tmp/k8s-monitoring-values.yaml
echo "Grafana Alloy (k8s-monitoring) installed"

#=========================================
# Tempo (Distributed Tracing)
#=========================================
echo "=== Installing Tempo ==="
TEMPO_OK=false
for attempt in 1 2 3 4 5; do
  # D-vparquet2: chart 2.2.3's default appVersion (2.10.7) removes vParquet2
  # block-encoding support entirely. Pin image.tag=2.9.0 until a live audit
  # of the SeaweedFS `tempo` bucket confirms no vParquet2-encoded blocks
  # exist (see VERSIONS.md). Lift this pin once confirmed safe.
  if helm upgrade --install tempo "$(chart tempo)" \
    --force-conflicts \
    --namespace monitoring \
    --version 2.2.3 \
    --set tempo.tag=2.9.0 \
    --set tempo.storage.trace.backend=local \
    --set persistence.enabled=true \
    --set persistence.storageClassName=nfs-csi \
    --set persistence.size=10Gi; then
    TEMPO_OK=true; break
  fi
  echo "Tempo install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${TEMPO_OK}" != true ]; then
  echo "ERROR: Tempo install failed after 5 attempts." >&2
  exit 1
fi

echo "Tempo installed"

echo "=== Monitoring Apps Installation Complete ==="
