#!/bin/bash
set -euo pipefail

# narwhal-cluster-heal: master-1 cluster-level post-reboot recovery
# Runs as narwhal-cluster-heal.service (oneshot, master-1 only) on every boot.
# Best-effort: always exits 0; controllers recreate deleted pods.

LOG_PREFIX="[narwhal-cluster-heal]"
export KUBECONFIG=/etc/kubernetes/admin.conf

log() { echo "${LOG_PREFIX} $*"; }

#=========================================
# GUARD: wait for API server (up to 5 min)
#=========================================
log "Waiting for kube-apiserver (up to 300s)..."
API_OK=false
for i in $(seq 1 30); do
  if kubectl get --raw /healthz &>/dev/null; then
    API_OK=true
    log "API server healthy (attempt ${i}/30)"
    break
  fi
  log "  attempt ${i}/30 — API not ready, sleeping 10s..."
  sleep 10
done

if [[ "${API_OK}" == "false" ]]; then
  log "API server never became reachable; exiting 0 (best-effort)"
  exit 0
fi

#=========================================
# Wait for >= 5 of 6 nodes Ready (bounded)
#=========================================
log "Waiting for cluster quorum (>=5/6 nodes Ready, up to 300s)..."
QUORUM=false
for i in $(seq 1 30); do
  READY_COUNT=$(kubectl get nodes --no-headers 2>/dev/null \
    | grep -c " Ready " || true)
  if [[ "${READY_COUNT}" -ge 5 ]]; then
    QUORUM=true
    log "Quorum reached: ${READY_COUNT} nodes Ready"
    break
  fi
  log "  attempt ${i}/30 — ${READY_COUNT}/6 nodes Ready, sleeping 10s..."
  sleep 10
done

if [[ "${QUORUM}" == "false" ]]; then
  log "Quorum not reached; proceeding with best-effort healing anyway"
fi

#=========================================
# Heal Cilium: delete Unknown/not-Ready pods
# Labels: k8s-app=cilium, io.cilium/app=operator
#=========================================
log "=== Cilium healing ==="
for label in "k8s-app=cilium" "io.cilium/app=operator"; do
  kubectl get pods -n kube-system -l "${label}" --no-headers 2>/dev/null \
    | awk '$3!="1/1" && $3!="2/2" || $4!="Running" { print $1 }' \
    | while read -r pod; do
        log "Deleting unhealthy Cilium pod: ${pod}"
        kubectl delete pod "${pod}" -n kube-system --ignore-not-found \
          --force --grace-period=0 || true
      done
done

#=========================================
# Heal Istio CNI pods in istio-system
# (istiod, istio-cni, ztunnel — delete Unknown)
#=========================================
log "=== Istio healing (istio-system Unknown pods) ==="
kubectl get pods -n istio-system --no-headers 2>/dev/null \
  | awk '$4=="Unknown" { print $1 }' \
  | while read -r pod; do
      log "Deleting Unknown Istio pod: ${pod}"
      kubectl delete pod "${pod}" -n istio-system --ignore-not-found \
        --force --grace-period=0 || true
    done

#=========================================
# Delete ALL Unknown pods cluster-wide
# (controllers recreate; Unknown = node lost contact)
#=========================================
log "=== Deleting all cluster-wide Unknown pods ==="
kubectl get pods -A --no-headers 2>/dev/null \
  | awk '$4=="Unknown" { print $1, $2 }' \
  | while read -r ns pod; do
      log "Deleting Unknown pod ${ns}/${pod}"
      kubectl delete pod "${pod}" -n "${ns}" --ignore-not-found \
        --force --grace-period=0 || true
    done

#=========================================
# Delete CrashLoopBackOff / CreateContainerError
# pods in platform namespaces for a fresh start.
# Namespaces: platform-system, devtools, monitoring,
#             storage, security-system
#=========================================
PLATFORM_NS="platform-system devtools monitoring storage security-system"
log "=== Deleting CrashLoop/CreateContainerError pods in platform namespaces ==="
for ns in ${PLATFORM_NS}; do
  kubectl get pods -n "${ns}" --no-headers 2>/dev/null \
    | awk '$4=="CrashLoopBackOff" || $4=="CreateContainerError" { print $1 }' \
    | while read -r pod; do
        log "Deleting ${ns}/${pod} (${pod} status reset)"
        kubectl delete pod "${pod}" -n "${ns}" --ignore-not-found || true
      done
done

#=========================================
# Convergence poll: wait up to 5 min for
# non-running pods <= 3 (excl. scan-vulnerabilityreport)
#=========================================
log "=== Polling for convergence (<=3 non-running pods, up to 300s) ==="
CONVERGED=false
for i in $(seq 1 30); do
  NOT_RUNNING=$(kubectl get pods -A --no-headers 2>/dev/null \
    | grep -v "scan-vulnerabilityreport" \
    | awk '$4!="Running" && $4!="Completed" && $4!="Succeeded" { c++ } END { print c+0 }')
  log "  attempt ${i}/30 — ${NOT_RUNNING} non-running pod(s)"
  if [[ "${NOT_RUNNING}" -le 3 ]]; then
    CONVERGED=true
    log "Cluster converged: ${NOT_RUNNING} non-running pods"
    break
  fi
  sleep 10
done

if [[ "${CONVERGED}" == "false" ]]; then
  FINAL=$(kubectl get pods -A --no-headers 2>/dev/null \
    | grep -v "scan-vulnerabilityreport" \
    | awk '$4!="Running" && $4!="Completed" && $4!="Succeeded" { c++ } END { print c+0 }')
  log "Convergence not reached within 300s; final non-running count: ${FINAL}"
  log "Manual investigation may be required"
fi

log "cluster-heal complete; exiting 0"
exit 0
