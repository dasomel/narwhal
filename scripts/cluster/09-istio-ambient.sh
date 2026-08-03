#!/bin/bash
set -euo pipefail

echo "=== Installing Istio Ambient Mesh ==="

ISTIO_VERSION="${ISTIO_VERSION:-1.30.1}"

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Wait for API server
#=========================================
echo "Waiting for API server..."
for attempt in $(seq 1 30); do
  if kubectl cluster-info &>/dev/null; then
    echo "API server ready"
    break
  fi
  echo "API server not ready, attempt ${attempt}/30..."
  sleep 10
done

#=========================================
# Create namespace
#=========================================
kubectl create namespace istio-system --dry-run=client -o yaml | kubectl apply -f -

#=========================================
# Add Istio Helm repo
#=========================================
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo update istio

#=========================================
# 1) istio-base (CRDs)
#=========================================
echo "Installing istio-base (CRDs)..."
helm upgrade --install istio-base istio/base \
  --force-conflicts \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  --timeout 120s || echo "WARN: istio-base install issue, continuing..."

# Wait for CRDs to be established
echo "Waiting for Istio CRDs..."
for crd in peerauthentications.security.istio.io authorizationpolicies.security.istio.io; do
  for attempt in $(seq 1 20); do
    if kubectl get crd "${crd}" &>/dev/null; then
      echo "CRD ${crd} ready"
      break
    fi
    sleep 3
  done
done

#=========================================
# 2) istiod (control plane, ambient profile)
#=========================================
echo "Installing istiod (ambient profile)..."

cat > /tmp/istiod-values.yaml << 'EOF'
profile: ambient
replicaCount: 2
# autoscaleEnabled must be false, else the chart's HPA overrides replicaCount back to 1 (SPOF)
autoscaleEnabled: false
# native PDB disabled; an explicit PDB is applied from gitops/resources/istiod-pdb.yaml
global:
  defaultPodDisruptionBudget:
    enabled: false
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 512Mi
tolerations:
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready
    operator: Exists
    effect: NoSchedule
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
              - key: app
                operator: In
                values:
                  - istiod
          topologyKey: kubernetes.io/hostname
EOF

helm upgrade --install istiod istio/istiod \
  --force-conflicts \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  -f /tmp/istiod-values.yaml \
  --timeout 180s || echo "WARN: istiod install issue, continuing..."

rm /tmp/istiod-values.yaml

# Apply separate PodDisruptionBudget for extra safety
kubectl apply -f /home/vagrant/configs/gitops/resources/istiod-pdb.yaml || echo "WARN: istiod PDB apply issue, continuing..."

# Wait for istiod to be ready
echo "Waiting for istiod..."
kubectl wait --for=condition=Ready pod -l app=istiod -n istio-system --timeout=180s || true

#=========================================
# 3) istio-cni (ambient CNI node agent)
#=========================================
echo "Installing istio-cni (ambient profile)..."

cat > /tmp/istio-cni-values.yaml << 'EOF'
profile: ambient
cni:
  ambient:
    enabled: true
tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready
    operator: Exists
    effect: NoSchedule
EOF

helm upgrade --install istio-cni istio/cni \
  --force-conflicts \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  -f /tmp/istio-cni-values.yaml \
  --timeout 180s || echo "WARN: istio-cni install issue, continuing..."

rm /tmp/istio-cni-values.yaml

# Wait for istio-cni DaemonSet
echo "Waiting for istio-cni DaemonSet..."
kubectl rollout status daemonset/istio-cni-node -n istio-system --timeout=180s || true

#=========================================
# 4) ztunnel (node proxy DaemonSet)
#=========================================
echo "Installing ztunnel..."

cat > /tmp/ztunnel-values.yaml << 'EOF'
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    memory: 256Mi
tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready
    operator: Exists
    effect: NoSchedule
EOF

helm upgrade --install ztunnel istio/ztunnel \
  --force-conflicts \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  -f /tmp/ztunnel-values.yaml \
  --timeout 180s || echo "WARN: ztunnel install issue, continuing..."

rm /tmp/ztunnel-values.yaml

# Wait for ztunnel DaemonSet
echo "Waiting for ztunnel DaemonSet..."
kubectl rollout status daemonset/ztunnel -n istio-system --timeout=180s || true

#=========================================
# 5) Label namespaces for ambient mesh
#=========================================
echo "Labeling namespaces for ambient mesh..."

AMBIENT_NAMESPACES=(
  platform-system
  devtools
  monitoring
  storage
  database
  dev
)
# NOTE: 'iam' excluded — Keycloak in iam namespace must be reachable
# from APISIX (cross-namespace plain HTTP). ztunnel ambient intercept blocks
# inbound plain HTTP even when pods have istio.io/dataplane-mode=none label,
# causing upstream timeout from APISIX -> keycloak-service ClusterIP.

for ns in "${AMBIENT_NAMESPACES[@]}"; do
  if kubectl get namespace "${ns}" &>/dev/null; then
    kubectl label namespace "${ns}" istio.io/dataplane-mode=ambient --overwrite
    echo "  Labeled ${ns} for ambient mesh"
  else
    echo "  WARN: namespace ${ns} not found, skipping label"
  fi
done

#=========================================
# 5b) Waypoint proxies (L7 telemetry)
#=========================================
# Waypoints generate istio_requests_total (req/s, error rate, p95) used by the
# portal service map. Without them ambient mode only emits L4 TCP bytes.
# Scope: HTTP-heavy namespaces only —
#   database excluded (CNPG replication path stays L4 to avoid an Envoy hop),
#   storage  excluded (OpenBao/SeaweedFS: avoid disturbing unseal/replication),
#   dev      excluded (no steady traffic).
WAYPOINT_NAMESPACES=(
  platform-system
  devtools
  monitoring
)

echo "Deploying waypoint proxies for L7 telemetry..."
for ns in "${WAYPOINT_NAMESPACES[@]}"; do
  if kubectl get namespace "${ns}" &>/dev/null; then
    cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: waypoint
  namespace: ${ns}
  labels:
    istio.io/waypoint-for: service
spec:
  gatewayClassName: istio-waypoint
  listeners:
    - name: mesh
      port: 15008
      protocol: HBONE
EOF
    kubectl label namespace "${ns}" istio.io/use-waypoint=waypoint --overwrite
    echo "  Waypoint deployed for ${ns}"
  else
    echo "  WARN: namespace ${ns} not found, skipping waypoint"
  fi
done

# Prometheus scrape for waypoint pods — the stock istio PodMonitors only cover
# ztunnel/istiod, so waypoint istio_requests_total is never collected without this.
# (prometheus-stack requires the release label to pick up monitors.)
cat <<'EOF' | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: istio-waypoints
  namespace: istio-system
  labels:
    release: prometheus-stack
spec:
  namespaceSelector:
    any: true
  selector:
    matchLabels:
      gateway.networking.k8s.io/gateway-class-name: istio-waypoint
  podMetricsEndpoints:
    - interval: 15s
      path: /stats/prometheus
      port: metrics
EOF
echo "  Waypoint PodMonitor applied"

#=========================================
# 6) PeerAuthentication PERMISSIVE (mesh-wide mTLS)
#=========================================
# PERMISSIVE allows non-mesh traffic (kubelet probes, MetalLB/APISIX external traffic)
# to reach mesh services. ztunnel still enforces mTLS for mesh-to-mesh communication.
echo "Applying mesh-wide PERMISSIVE mTLS..."
cat <<'EOF' | kubectl apply -f -
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: istio-system
spec:
  mtls:
    mode: PERMISSIVE
EOF

echo ""
echo "=== Istio Ambient Mesh Installation Complete ==="
echo ""
echo "Installed components:"
echo "  - istio-base (CRDs)"
echo "  - istiod (control plane, ambient profile)"
echo "  - istio-cni (CNI node agent)"
echo "  - ztunnel (node proxy DaemonSet)"
echo ""
echo "Ambient mesh namespaces (${#AMBIENT_NAMESPACES[@]}):"
printf '  - %s\n' "${AMBIENT_NAMESPACES[@]}"
echo ""
echo "mTLS: PERMISSIVE (mesh-wide; ztunnel enforces mTLS for mesh-to-mesh)"
echo ""
