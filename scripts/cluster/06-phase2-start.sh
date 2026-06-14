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

# Critical scripts - failure stops Phase 2
CRITICAL_SCRIPTS=(
  "07-cnpg.sh"
  "08-1-networking.sh"
  "11-keycloak.sh"
  "11-2-keycloak-config.sh"
  "11-4-keycloak-apiserver.sh"
)

# Run all scripts in order (07→08-1~08-6→09→10→11-keycloak,11-2,11-3,11-4→12→13→14)
# Critical scripts will abort Phase 2 on failure; non-critical will warn and continue
for script in "07-cnpg.sh" "08-1-networking.sh" "08-2-monitoring.sh" "08-3-security.sh" "08-4-storage.sh" "08-5-registry.sh" "08-6-tls-routes.sh" "09-istio-ambient.sh" "10-dnsmasq.sh" "11-keycloak.sh" "11-2-keycloak-config.sh" "11-3-keycloak-clients.sh" "11-4-keycloak-apiserver.sh" "12-gitea.sh" "13-argocd.sh" "13-2-narwhal-portal-bindings.sh" "14-gitops-bootstrap.sh"; do
  echo "============================================"
  # Determine if this script is critical
  is_critical=false
  for crit in "${CRITICAL_SCRIPTS[@]}"; do
    if [[ "${script}" == "${crit}" ]]; then
      is_critical=true
      break
    fi
  done

  if [[ "${is_critical}" == "true" ]]; then
    echo ">>> Running ${script} (critical)..."
    echo "============================================"
    if ! bash "${SCRIPT_DIR}/${script}"; then
      echo "ERROR: Critical script ${script} failed. Phase 2 cannot continue safely."
      echo "Fix the error above and re-run: vagrant provision master-1 --provision-with phase2-platform"
      exit 1
    fi
  else
    echo ">>> Running ${script}..."
    echo "============================================"
    bash "${SCRIPT_DIR}/${script}" || echo "WARN: ${script} had issues, continuing..."
  fi
done

echo ""
echo "============================================"
echo "Phase 2: Platform Apps Installation Complete"
echo "============================================"
