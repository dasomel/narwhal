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

# Source BASE_DOMAIN from cluster.env if DOMAIN is not already set (e.g. non-Vagrant run).
CLUSTER_ENV="/home/vagrant/configs/cluster.env"
if [[ -z "${DOMAIN:-}" && -f "${CLUSTER_ENV}" ]]; then
  BASE_DOMAIN_LINE=$(grep -E '^\s*BASE_DOMAIN\s*=' "${CLUSTER_ENV}" | tail -1 || true)
  if [[ -n "${BASE_DOMAIN_LINE}" ]]; then
    export DOMAIN="${BASE_DOMAIN_LINE#*=}"
    DOMAIN="${DOMAIN%% *}"  # strip trailing spaces/comments
  fi
fi
export DOMAIN="${DOMAIN:-local.narwhal.internal}"

# Wait for all nodes to be Ready
echo "Waiting for all cluster nodes to be Ready..."
kubectl wait --for=condition=Ready node --all --timeout=300s

echo ""
echo "=== Cluster Status ==="
kubectl get nodes
echo ""

#=========================================
# D6: Cilium health-gate + self-healing recovery
# Root cause: cilium-operator hits transient Unauthorized during bring-up (SA token
# not yet issued) → "Failed to start hive" → containerd reserves container name →
# operator stays in CreateContainerError even after apiserver stabilises.
# Fix: poll for a healthy Cilium before handing off to Phase-2 scripts; if still
# degraded after half the timeout, delete wedged/Unknown/CrashLoop pods so they
# reschedule fresh, then re-poll. Exit 1 only if recovery doesn't converge.
#=========================================
cilium_health_gate() {
  local max_wait=300   # seconds total
  local interval=10
  local deadline=$(( $(date +%s) + max_wait ))
  local recovered=false

  echo "=== Cilium health-gate: waiting for all cilium pods Ready (up to ${max_wait}s) ==="

  while true; do
    local now
    now=$(date +%s)
    local elapsed=$(( now - (deadline - max_wait) ))

    # Count expected cilium agent pods (one per node)
    local total_nodes
    total_nodes=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')

    # Count Ready cilium agents
    local ready_agents
    ready_agents=$(kubectl get pods -n kube-system -l k8s-app=cilium \
      --field-selector status.phase=Running --no-headers 2>/dev/null \
      | awk '$2=="1/1"' | wc -l | tr -d ' ')

    # Operator ready?
    local op_ready
    op_ready=$(kubectl get pods -n kube-system -l io.cilium/app=operator \
      --no-headers 2>/dev/null | awk '$2=="1/1"' | wc -l | tr -d ' ')

    echo "  [+${elapsed}s] cilium agents Ready: ${ready_agents}/${total_nodes}, operator Ready: ${op_ready}"

    if [[ "${ready_agents}" -ge "${total_nodes}" && "${op_ready}" -ge 1 ]]; then
      echo "  Cilium fully healthy."
      return 0
    fi

    # Mid-point: trigger recovery by evicting wedged pods so they reschedule
    if [[ "${now}" -ge "$(( deadline - max_wait/2 ))" && "${recovered}" == "false" ]]; then
      echo "  WARN: Cilium still degraded at mid-point — purging Unknown/CrashLoop/CreateContainerError pods"
      # Delete wedged cilium-operator pod
      kubectl delete pods -n kube-system -l io.cilium/app=operator \
        --field-selector 'status.phase!=Running' --ignore-not-found 2>/dev/null || true
      # Also catch CreateContainerError (phase=Pending) operator pods
      kubectl get pods -n kube-system -l io.cilium/app=operator --no-headers 2>/dev/null \
        | grep -v '1/1' | awk '{print $1}' \
        | xargs -r kubectl delete pod -n kube-system --ignore-not-found 2>/dev/null || true
      # Delete Unknown and CrashLoopBackOff cilium agent pods
      kubectl delete pods -n kube-system -l k8s-app=cilium \
        --field-selector 'status.phase=Unknown' --ignore-not-found 2>/dev/null || true
      kubectl get pods -n kube-system -l k8s-app=cilium --no-headers 2>/dev/null \
        | grep -E 'Unknown|CrashLoopBackOff|CreateContainerError|Error' | awk '{print $1}' \
        | xargs -r kubectl delete pod -n kube-system --ignore-not-found 2>/dev/null || true
      # Delete stale not-ready cilium-envoy pods so they reschedule
      kubectl get pods -n kube-system -l k8s-app=cilium-envoy --no-headers 2>/dev/null \
        | grep -v '1/1' | awk '{print $1}' \
        | xargs -r kubectl delete pod -n kube-system --ignore-not-found 2>/dev/null || true
      recovered=true
      echo "  Wedged pods purged — waiting for reschedule..."
    fi

    if [[ "${now}" -ge "${deadline}" ]]; then
      echo "ERROR: Cilium did not become healthy within ${max_wait}s. Agents Ready: ${ready_agents}/${total_nodes}, Operator: ${op_ready}" >&2
      echo "  Run: kubectl get pods -n kube-system | grep cilium" >&2
      return 1
    fi

    sleep "${interval}"
  done
}

cilium_health_gate

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
  # 12 stands up the Gitea that 14 pushes to, and 14 hands ten platform apps to ArgoCD:
  # cert-manager, prometheus-stack, loki, tempo, alloy, gitea, harbor, openbao, kyverno,
  # headlamp. Both were non-critical, so when Gitea was still ImagePullBackOff the push
  # failed, 14 warned, and Phase 2 printed "Complete" over a cluster with zero ArgoCD
  # applications — grafana, the dashboard, the portal and velero-ui all 503 with nothing
  # behind them. A step whose failure silently removes most of the platform is not
  # non-critical.
  "12-gitea.sh"
  "14-gitops-bootstrap.sh"
)

# Run all scripts in order (07→08-1~08-6→09→10→11-keycloak,11-2,11-3,11-4→12→13→14)
# Critical scripts will abort Phase 2 on failure; non-critical will warn and continue
for script in "07-cnpg.sh" "08-1-networking.sh" "08-2-monitoring.sh" "08-3-security.sh" "08-4-storage.sh" "08-5-registry.sh" "08-6-tls-routes.sh" "09-istio-ambient.sh" "10-dnsmasq.sh" "11-keycloak.sh" "11-2-keycloak-config.sh" "11-3-keycloak-clients.sh" "11-4-keycloak-apiserver.sh" "12-gitea.sh" "13-argocd.sh" "13-2-narwhal-portal-bindings.sh" "14-gitops-bootstrap.sh" "15-narwhal-portal.sh"; do
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
