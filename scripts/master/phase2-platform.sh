#!/bin/bash
set -euo pipefail

echo "============================================"
echo "Phase 2: Platform Apps Installation"
echo "============================================"
echo ""

# Use local kubeconfig for reliability
export KUBECONFIG=/home/vagrant/.kube/config-local

# Wait for all nodes to be Ready
echo "Waiting for all cluster nodes to be Ready..."
kubectl wait --for=condition=Ready node --all --timeout=300s

echo ""
echo "=== Cluster Status ==="
kubectl get nodes
echo ""

# Phase 2 scripts
SCRIPT_DIR="/home/vagrant/scripts/master"

scripts=(
  "06-cnpg.sh"
  "07-platform-apps.sh"
  "08-dnsmasq.sh"
  "09-keycloak.sh"
  "10-gitea.sh"
  "11-argocd.sh"
  "12-gitops-bootstrap.sh"
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
