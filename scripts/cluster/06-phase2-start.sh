#!/bin/bash
set -euo pipefail

echo "============================================"
echo "Phase 2: Platform Apps Installation"
echo "============================================"
echo ""

# Use local kubeconfig for reliability
export KUBECONFIG=/home/vagrant/.kube/config-local

# Export MASTER_COUNT for downstream scripts (10-dnsmasq.sh, etc.)
export MASTER_COUNT="${MASTER_COUNT:-3}"
export MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"

# Wait for all nodes to be Ready
echo "Waiting for all cluster nodes to be Ready..."
kubectl wait --for=condition=Ready node --all --timeout=300s

echo ""
echo "=== Cluster Status ==="
kubectl get nodes
echo ""

#=========================================
# Apply control-plane NoSchedule taint
#=========================================
# During Phase 1, taint was removed so DaemonSets could schedule on master-only cluster.
# Now that workers are available, re-apply taint to keep platform apps on workers.
echo "Applying control-plane NoSchedule taint to master nodes..."
for node in $(kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers -o custom-columns=NAME:.metadata.name); do
  kubectl taint nodes "${node}" node-role.kubernetes.io/control-plane:NoSchedule --overwrite 2>/dev/null || true
done
echo "Control-plane taint applied. Platform apps will schedule on workers only."
echo ""

# Phase 2 scripts
SCRIPT_DIR="/home/vagrant/scripts/cluster"

scripts=(
  "07-cnpg.sh"
  "08-platform-apps.sh"
  "09-istio-ambient.sh"
  "10-dnsmasq.sh"
  "11-keycloak.sh"
  "12-gitea.sh"
  "13-argocd.sh"
  "14-gitops-bootstrap.sh"
)

for script in "${scripts[@]}"; do
  echo "============================================"
  echo ">>> Running ${script}..."
  echo "============================================"
  bash "${SCRIPT_DIR}/${script}" || echo "WARN: ${script} had issues, continuing..."
done

echo ""
echo "============================================"
echo "Phase 2: Platform Apps Installation Complete"
echo "============================================"
