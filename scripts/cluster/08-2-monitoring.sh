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
    --set kubeProxy.enabled=false \
    --set kubeEtcd.enabled=true \
    --set kubeEtcd.service.port=2381 \
    --set kubeEtcd.service.targetPort=2381 \
    --set kubeEtcd.service.selector.component=etcd \
    --set kubeEtcd.serviceMonitor.scheme=http \
    --set-string kubeEtcd.serviceMonitor.bearerTokenFile= \
    --set-json 'alertmanager.alertmanagerSpec.alertmanagerConfigSelector={"matchLabels":{"narwhal.io/notification-channels":"true"}}' \
    --set-json 'alertmanager.alertmanagerSpec.alertmanagerConfigNamespaceSelector={"matchLabels":{"kubernetes.io/metadata.name":"monitoring"}}' \
    --set alertmanager.alertmanagerSpec.alertmanagerConfigMatcherStrategy.type=None \
    --set grafana.assertNoLeakedSecrets=false \
    --set 'grafana.grafana\.ini.tracing\.opentelemetry\.otlp.address=k8s-monitoring-alloy-logs.monitoring.svc.cluster.local:4317' \
    --set grafana.admin.existingSecret=grafana-secrets \
    --set grafana.admin.userKey=admin-user \
    --set grafana.admin.passwordKey=admin-password \
    --set grafana.initChownData.enabled=false \
    --set grafana.persistence.enabled=true \
    --set grafana.persistence.storageClassName=nfs-csi \
    --set grafana.persistence.size=5Gi \
    --set prometheus.prometheusSpec.retention=7d \
    --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
    --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=nfs-csi \
    --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=10Gi \
    --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.storageClassName=nfs-csi \
    --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.resources.requests.storage=5Gi \
    --set-json 'alertmanager.alertmanagerSpec.resources={"requests":{"cpu":"25m","memory":"64Mi"},"limits":{"cpu":"200m","memory":"256Mi"}}' \
    --set-json 'prometheus-node-exporter.resources={"requests":{"cpu":"50m","memory":"64Mi"},"limits":{"cpu":"200m","memory":"128Mi"}}' \
    --set-json 'prometheus-node-exporter.containerSecurityContext={"allowPrivilegeEscalation":false,"readOnlyRootFilesystem":true,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
    --set-json 'kube-state-metrics.resources={"requests":{"cpu":"25m","memory":"64Mi"},"limits":{"cpu":"200m","memory":"256Mi"}}' \
    --set-json 'prometheusOperator.resources={"requests":{"cpu":"50m","memory":"128Mi"},"limits":{"cpu":"250m","memory":"256Mi"}}' \
    --set-json 'prometheusOperator.prometheusConfigReloader.resources={"requests":{"cpu":"10m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}}' \
    --set-json 'grafana.resources={"requests":{"cpu":"50m","memory":"128Mi"},"limits":{"cpu":"250m","memory":"512Mi"}}' \
    --set-json 'grafana.containerSecurityContext={"allowPrivilegeEscalation":false,"readOnlyRootFilesystem":true,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
    --set-json 'grafana.extraEmptyDirMounts=[{"name":"tmp","mountPath":"/tmp"}]' \
    --set-json 'grafana.sidecar.resources={"requests":{"cpu":"25m","memory":"64Mi"},"limits":{"cpu":"100m","memory":"128Mi"}}' \
    --set-json 'grafana.sidecar.securityContext={"allowPrivilegeEscalation":false,"readOnlyRootFilesystem":true,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}' \
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
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      memory: 768Mi
sidecar:
  resources:
    requests:
      cpu: 10m
      memory: 64Mi
    limits:
      cpu: 100m
      memory: 192Mi
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
# D-LOKI-CANARY-DISABLE: top-level key in chart 18.4.0 (not under monitoring)
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
  tempo:
    type: otlp
    url: tempo.monitoring.svc.cluster.local:4317
    protocol: grpc
    tls:
      insecure: true
    metrics:
      enabled: false
    logs:
      enabled: false
    traces:
      enabled: true
collectors:
  alloy-logs:
    presets: [small, daemonset, filesystem-log-reader]
    includeDestinations:
      - tempo
    service:
      internalTrafficPolicy: Local
    extraConfig: |
      otelcol.receiver.otlp "otlp_traces" {
        grpc {
          endpoint = "0.0.0.0:4317"
          include_metadata = false
          max_recv_msg_size = "4MiB"
          read_buffer_size = "512KiB"
          write_buffer_size = "32KiB"
        }
        http {
          endpoint = "0.0.0.0:4318"
          include_metadata = false
          max_request_body_size = "20MiB"
        }
        debug_metrics {
          disable_high_cardinality_metrics = true
        }
        output {
          traces = [otelcol.processor.memory_limiter.otlp_traces.input]
        }
      }

      otelcol.processor.memory_limiter "otlp_traces" {
        check_interval = "1s"
        limit = "384MiB"
        output {
          traces = [otelcol.processor.transform.strip_client_k8s.input]
        }
      }

      otelcol.processor.transform "strip_client_k8s" {
        error_mode = "propagate"
        trace_statements {
          context = "resource"
          statements = [
            `delete_matching_keys(attributes, "^k8s[.]")`,
            `delete_key(attributes, "service.namespace")`,
          ]
        }
        output {
          traces = [otelcol.processor.k8sattributes.otlp_traces.input]
        }
      }

      otelcol.processor.k8sattributes "otlp_traces" {
        passthrough = false
        filter {
          node = sys.env("HOSTNAME")
        }
        extract {
          metadata = ["k8s.namespace.name","k8s.pod.name","k8s.deployment.name","k8s.statefulset.name","k8s.daemonset.name","k8s.job.name","k8s.node.name"]
        }
        pod_association {
          source {
            from = "connection"
          }
        }
        output {
          traces = [otelcol.processor.attributes.tempo.input]
        }
      }
    alloy:
      extraPorts:
        - name: otlp-grpc
          port: 4317
          targetPort: 4317
          protocol: TCP
          appProtocol: grpc
        - name: otlp-http
          port: 4318
          targetPort: 4318
          protocol: TCP
          appProtocol: http
      mounts:
        extra:
          - name: tmp
            mountPath: /tmp
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop:
            - ALL
          add:
            - CHOWN
            - DAC_OVERRIDE
            - FOWNER
            - FSETID
            - KILL
            - SETGID
            - SETUID
            - SETPCAP
            - NET_BIND_SERVICE
            - SYS_CHROOT
            - MKNOD
            - AUDIT_WRITE
            - SETFCAP
        seccompProfile:
          type: RuntimeDefault
      configReloader:
        resources:
          requests:
            cpu: 10m
            memory: 32Mi
          limits:
            cpu: 100m
            memory: 64Mi
        securityContext:
          allowPrivilegeEscalation: false
          readOnlyRootFilesystem: true
          runAsNonRoot: true
          runAsUser: 65534
          capabilities:
            drop:
              - ALL
          seccompProfile:
            type: RuntimeDefault
    controller:
      volumes:
        extra:
          - name: tmp
            emptyDir: {}
    configReloader:
      resources:
        requests:
          cpu: 10m
          memory: 32Mi
        limits:
          cpu: 100m
          memory: 64Mi
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        runAsNonRoot: true
        runAsUser: 65534
        capabilities:
          drop:
            - ALL
        seccompProfile:
          type: RuntimeDefault
podLogsViaLoki:
  enabled: true
  collector: alloy-logs
  destinations: []
applicationObservability:
  enabled: false
alloy-operator:
  podSecurityContext:
    runAsNonRoot: true
    runAsUser: 4242
    runAsGroup: 4242
    fsGroup: 4242
    seccompProfile:
      type: RuntimeDefault
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop:
        - ALL
    seccompProfile:
      type: RuntimeDefault
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 256Mi
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
    --set persistence.size=10Gi \
    --set tempo.securityContext.allowPrivilegeEscalation=false \
    --set tempo.securityContext.readOnlyRootFilesystem=true \
    --set 'tempo.securityContext.capabilities.drop[0]=ALL' \
    --set securityContext.seccompProfile.type=RuntimeDefault \
    --set extraVolumes[0].name=tmp \
    --set-json 'extraVolumes[0].emptyDir={}' \
    --set tempo.extraVolumeMounts[0].name=tmp \
    --set tempo.extraVolumeMounts[0].mountPath=/tmp; then
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
