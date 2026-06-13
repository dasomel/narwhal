#!/bin/bash
#=============================================================================
# Script: 99-recover-mesh.sh
# Description: Idempotent self-healing recovery script for the Narwhal cluster.
#              Recovers from worker node loss, istiod control plane failures,
#              ztunnel/istio-cni DaemonSet issues, stuck/ghost terminating pods,
#              and ServiceAccount-token authentication broken pods.
# Usage:
#   ./99-recover-mesh.sh
# Overrides (Env Vars):
#   WAIT_TIMEOUT (default: 180s)
#   GHOST_GRACE_MINUTES (default: 10)
#=============================================================================
set -euo pipefail

# Environment variables with default values
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180s}"
GHOST_GRACE_MINUTES="${GHOST_GRACE_MINUTES:-10}"

echo "=== Narwhal Cluster Mesh Recovery Self-Healing Automation ==="
echo "Configuration:"
echo "  WAIT_TIMEOUT: ${WAIT_TIMEOUT}"
echo "  GHOST_GRACE_MINUTES: ${GHOST_GRACE_MINUTES} minutes"
echo "============================================================"

# Helper function to parse ISO 8601 timestamps to UNIX epoch seconds.
# Works across GNU date, BSD date, and Busybox/Alpine date implementations.
parse_iso8601_to_epoch() {
  local ts="$1"
  local epoch

  # Try GNU/Busybox date format directly
  epoch=$(date -d "$ts" +%s 2>/dev/null)
  if [ -n "$epoch" ] && [ "$epoch" -gt 0 ] 2>/dev/null; then
    echo "$epoch"
    return
  fi

  # Try removing 'Z' and replacing 'T' with a space
  local formatted="${ts/T/ }"
  formatted="${formatted%Z}"
  epoch=$(date -d "$formatted" +%s 2>/dev/null)
  if [ -n "$epoch" ] && [ "$epoch" -gt 0 ] 2>/dev/null; then
    echo "$epoch"
    return
  fi

  # Try BSD (macOS) date format
  epoch=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$ts" +%s 2>/dev/null)
  if [ -n "$epoch" ] && [ "$epoch" -gt 0 ] 2>/dev/null; then
    echo "$epoch"
    return
  fi

  echo "0"
}

# Helper to check if a DaemonSet is fully ready (desired == ready and desired > 0)
check_daemonset_ready() {
  local ns="$1"
  local ds_name="$2"

  if ! kubectl -n "$ns" get daemonset "$ds_name" >/dev/null 2>&1; then
    echo "DaemonSet ${ds_name} not found in namespace ${ns}. Skipping readiness check."
    return 0
  fi

  local desired
  desired=$(kubectl -n "$ns" get daemonset "$ds_name" -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo "0")
  local ready
  ready=$(kubectl -n "$ns" get daemonset "$ds_name" -o jsonpath='{.status.numberReady}' 2>/dev/null || echo "0")

  [ -z "$desired" ] && desired=0
  [ -z "$ready" ] && ready=0

  echo "DaemonSet ${ds_name} status: desired=${desired}, ready=${ready}"
  if [ "$desired" -eq 0 ] || [ "$ready" -lt "$desired" ]; then
    return 1 # Not fully ready
  else
    return 0 # Fully ready
  fi
}

# ----------------------------------------------------------------------------
# Step 1: Wait until all nodes are Ready
# ----------------------------------------------------------------------------
echo ""
echo "[Step 1/6] Verifying node readiness..."
if ! kubectl wait --for=condition=Ready node --all --timeout="${WAIT_TIMEOUT}" 2>/dev/null; then
  echo "WARNING: Some nodes did not become Ready within ${WAIT_TIMEOUT}. VMs may be intentionally down."
else
  echo "All nodes are Ready."
fi

# ----------------------------------------------------------------------------
# Step 2: Recover istiod deployment
# ----------------------------------------------------------------------------
echo ""
echo "[Step 2/6] Verifying istiod mesh control plane..."
if ! kubectl -n istio-system get deployment istiod >/dev/null 2>&1; then
  echo "istiod deployment not found in namespace istio-system. Skipping istiod recovery."
else
  # Retrieve endpoint information and deployment replicas
  addresses=$(kubectl -n istio-system get endpoints istiod -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || echo "")
  ready_replicas=$(kubectl -n istio-system get deployment istiod -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
  desired_replicas=$(kubectl -n istio-system get deployment istiod -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")

  [ -z "$ready_replicas" ] && ready_replicas=0
  [ -z "$desired_replicas" ] && desired_replicas=0

  echo "istiod status: desired_replicas=${desired_replicas}, ready_replicas=${ready_replicas}"
  if [ -z "$addresses" ] || [ "$ready_replicas" -lt "$desired_replicas" ] || [ "$desired_replicas" -eq 0 ]; then
    echo "istiod is not healthy (endpoints missing or ready replicas < desired). Cleaning up stuck pods..."
    
    # Identify istiod pods and delete any that are on NotReady nodes or in stuck phases
    istiod_pods=$(kubectl -n istio-system get pods -l app=istiod -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\t"}{.status.phase}{"\n"}{end}' || true)
    
    if [ -n "$istiod_pods" ]; then
      while read -r pod_name node_name pod_phase; do
        if [ -z "$pod_name" ]; then continue; fi
        
        # Get the node status of the pod's node
        node_status="Unknown"
        if [ -n "$node_name" ]; then
          node_status=$(kubectl get node "$node_name" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
        fi
        
        is_terminating=$(kubectl -n istio-system get pod "$pod_name" -o jsonpath='{.metadata.deletionTimestamp}' 2>/dev/null || echo "")
        
        if [ "$pod_phase" = "Unknown" ] || [ -n "$is_terminating" ] || [ "$node_status" != "True" ]; then
          echo "Force-deleting stuck istiod pod: ${pod_name} (Node: ${node_name:-None}, NodeReady: ${node_status}, Phase: ${pod_phase}, Terminating: ${is_terminating:+"Yes"})"
          kubectl -n istio-system delete pod "$pod_name" --grace-period=0 --force || true
        fi
      done <<< "$istiod_pods"
    fi
    
    echo "Waiting for istiod deployment to roll out successfully..."
    kubectl -n istio-system rollout status deployment/istiod --timeout="${WAIT_TIMEOUT}" || true
  else
    echo "istiod control plane is healthy."
  fi

  echo "Current istiod endpoints:"
  kubectl -n istio-system get endpoints istiod || true
fi

# ----------------------------------------------------------------------------
# Step 3: Check and rollout restart ztunnel / istio-cni DaemonSets
# ----------------------------------------------------------------------------
echo ""
echo "[Step 3/6] Verifying ztunnel and istio-cni DaemonSets..."
for ds in ztunnel istio-cni; do
  if ! check_daemonset_ready "istio-system" "$ds"; then
    echo "DaemonSet ${ds} is not fully ready. Triggering rollout restart..."
    kubectl -n istio-system rollout restart daemonset/"$ds" || true
    echo "Waiting for DaemonSet ${ds} rollout status..."
    kubectl -n istio-system rollout status daemonset/"$ds" --timeout="${WAIT_TIMEOUT}" || true
  else
    echo "DaemonSet ${ds} is healthy."
  fi
done

# ----------------------------------------------------------------------------
# Step 4: Ghost reaper (delete Unknown phase pods & stuck Terminating pods)
# ----------------------------------------------------------------------------
echo ""
echo "[Step 4/6] Reaping ghost/stuck pods cluster-wide..."
# Output format: deletionTimestamp|namespace|name|phase
pods_list=$(kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.deletionTimestamp}{"|"}{.metadata.namespace}{"|"}{.metadata.name}{"|"}{.status.phase}{"\n"}{end}' 2>/dev/null || true)

current_time=$(date +%s)
grace_seconds=$((GHOST_GRACE_MINUTES * 60))
reaped_count=0

if [ -n "$pods_list" ]; then
  while IFS='|' read -r del_ts ns name phase; do
    if [ -z "$ns" ] || [ -z "$name" ]; then continue; fi

    # Case A: Pod is in phase Unknown
    if [ "$phase" = "Unknown" ]; then
      echo "Ghost pod found in phase Unknown: ${ns}/${name}. Force deleting..."
      kubectl -n "$ns" delete pod "$name" --grace-period=0 --force || true
      reaped_count=$((reaped_count + 1))
    
    # Case B: Pod is terminating and has exceeded the grace threshold.
    # Note: Running/Succeeded/Pending pods WITHOUT deletionTimestamp are NEVER touched,
    # because they have del_ts empty, bypassing this branch entirely.
    elif [ -n "$del_ts" ]; then
      del_epoch=$(parse_iso8601_to_epoch "$del_ts")
      if [ "$del_epoch" -gt 0 ]; then
        age=$((current_time - del_epoch))
        if [ "$age" -gt "$grace_seconds" ]; then
          echo "Stuck Terminating pod found: ${ns}/${name} (Age: $((age / 60))m, Phase: ${phase}). Force deleting..."
          kubectl -n "$ns" delete pod "$name" --grace-period=0 --force || true
          reaped_count=$((reaped_count + 1))
        fi
      else
        echo "WARNING: Could not parse deletion timestamp '${del_ts}' for pod ${ns}/${name}. Skipping."
      fi
    fi
  done <<< "$pods_list"
fi
echo "Ghost pod reaping finished. Total pods reaped: ${reaped_count}."

# ----------------------------------------------------------------------------
# Step 5: Recreate auth-broken pods (CrashLoopBackOff with "provide credentials")
# ----------------------------------------------------------------------------
echo ""
echo "[Step 5/6] Recreating auth-broken pods..."
# Get all waiting status reasons for pod containers.
# Output format: reasons_separated_by_space|namespace|name
crash_pods=$(kubectl get pods -A -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}{"|"}{.metadata.namespace}{"|"}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep -E '\bCrashLoopBackOff\b' || true)
recreated_count=0

if [ -n "$crash_pods" ]; then
  while IFS='|' read -r reasons ns name; do
    if [ -z "$ns" ] || [ -z "$name" ]; then continue; fi
    
    case "$reasons" in
      *CrashLoopBackOff*)
        # Fetch log snippet from all containers in the pod to search for credential issues
        logs=$(kubectl -n "$ns" logs "$name" --all-containers=true --tail=100 2>/dev/null || true)
        
        # Check specifically for "provide credentials" token auth failure signature
        if echo "$logs" | grep -F "provide credentials" >/dev/null 2>&1; then
          echo "Auth-broken pod detected: ${ns}/${name}. Deleting to recreate with fresh token..."
          kubectl -n "$ns" delete pod "$name" || true
          recreated_count=$((recreated_count + 1))
        fi
        ;;
    esac
  done <<< "$crash_pods"
fi
echo "Auth-broken pod recreation finished. Total pods recreated: ${recreated_count}."

# ----------------------------------------------------------------------------
# Step 6: Print final summary & Static Manifest Warning
# ----------------------------------------------------------------------------
echo ""
echo "[Step 6/6] Final recovery status summary:"
echo "==================== NODE STATES ===================="
kubectl get nodes -o 'custom-columns=NAME:.metadata.name,STATUS:.status.conditions[?(@.type=="Ready")].status,VERSION:.status.nodeInfo.kubeletVersion' || true

echo ""
echo "==================== SERVICE MESH READYNESS ===================="
if kubectl -n istio-system get deployment istiod >/dev/null 2>&1; then
  kubectl -n istio-system get deployment istiod -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.spec.replicas || true
else
  echo "istiod deployment: NOT FOUND"
fi
if kubectl -n istio-system get daemonset ztunnel >/dev/null 2>&1; then
  kubectl -n istio-system get daemonset ztunnel -o custom-columns=NAME:.metadata.name,DESIRED:.status.desiredNumberScheduled,READY:.status.numberReady || true
else
  echo "ztunnel daemonset: NOT FOUND"
fi
if kubectl -n istio-system get daemonset istio-cni >/dev/null 2>&1; then
  kubectl -n istio-system get daemonset istio-cni -o custom-columns=NAME:.metadata.name,DESIRED:.status.desiredNumberScheduled,READY:.status.numberReady || true
else
  echo "istio-cni daemonset: NOT FOUND"
fi

echo ""
echo "==================== NON-RUNNING PODS CLUSTER-WIDE ===================="
non_running_count=$(kubectl get pods -A --field-selector=status.phase!=Running --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
echo "Total non-Running pods: ${non_running_count}"
if [ "${non_running_count}" -gt 0 ]; then
  kubectl get pods -A --field-selector=status.phase!=Running -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase || true
fi

# Detect if kube-apiserver static pods are in CreateContainerError
apiserver_errors=$(kubectl get pods -n kube-system -l component=kube-apiserver -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}{end}' 2>/dev/null | grep -E 'CreateContainerError' || true)

if [ -n "$apiserver_errors" ]; then
  echo ""
  echo "================================================================================"
  echo "WARNING: One or more kube-apiserver pods are stuck in CreateContainerError!"
  echo "This usually happens when the container engine is out of sync with the static pod."
  echo "To recover this, you MUST SSH into each master host and toggle the static manifest:"
  echo ""
  echo "  sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/"
  echo "  sleep 5"
  echo "  sudo mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/"
  echo "================================================================================"
fi

echo ""
echo "=== Mesh Recovery Script Execution Complete ==="
