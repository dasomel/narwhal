#!/bin/bash
set -euo pipefail

# narwhal-cluster-heal: master-1 cluster-level post-reboot recovery
# Runs as narwhal-cluster-heal.service (oneshot, master-1 only) on every boot.
# Best-effort: always exits 0; controllers recreate deleted pods.

LOG_PREFIX="[narwhal-cluster-heal]"
export KUBECONFIG=/etc/kubernetes/admin.conf

log() { echo "${LOG_PREFIX} $*"; }

#=========================================
# Blast-radius guard.
#
# Every deletion below is driven by an awk predicate, and on 2026-07-31 one of
# those predicates evaluated true for every pod it examined (wrong field indices
# — see the Cilium block). The healer then force-deleted the entire Cilium
# DaemonSet on every boot, taking the CNI out cluster-wide, and its own
# convergence check reported success afterwards.
#
# The invariant that would have caught it: a filter selecting 100% of the
# population it examined is describing a broken filter, not a broken cluster.
# Deleting every pod of a DaemonSet is also useless as a remedy — the controller
# recreates the same set, so the only guaranteed effect is the outage in between.
#
# Deliberately triggers on "all", not on a fraction: a genuine node loss can
# legitimately strand a large share of pods in Unknown, and refusing to clean
# those up would break the healer's actual job. Only "everything, with nothing
# left healthy" is unambiguously a bug in the caller.
#=========================================
selection_is_sane() {
  local scope="$1" selected="$2" total="$3"
  if [[ "${selected}" -eq 0 ]]; then
    return 1
  fi
  if [[ "${total}" -gt 1 && "${selected}" -eq "${total}" ]]; then
    log "REFUSING to heal ${scope}: the filter selected all ${total} pod(s)."
    log "  A predicate that matches its entire population is broken; deleting them"
    log "  would only cause an outage the controller then has to recover from."
    log "  Nothing deleted. Investigate ${scope} by hand."
    return 1
  fi
  return 0
}

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
  # Columns for a single-namespace `--no-headers` query: $1=NAME $2=READY
  # $3=STATUS $4=RESTARTS. The previous filter used the `-A` layout ($3=READY,
  # $4=STATUS) against this one, so it compared STATUS to "1/1" and RESTARTS to
  # "Running" — both always true. Every run force-deleted the entire Cilium
  # DaemonSet and the operator, healthy or not: a whole-cluster CNI outage
  # caused by the healer itself (verified 2026-07-31: matched 6/6 healthy pods).
  # Ready ratios are also read, not enumerated — the old "1/1 or 2/2" allowlist
  # would have called any 3/3 pod unhealthy.
  all_pods=$(kubectl get pods -n kube-system -l "${label}" --no-headers 2>/dev/null || true)
  [[ -z "${all_pods}" ]] && continue
  unhealthy=$(awk '{ split($2,r,"/"); if (r[1]!=r[2] || $3!="Running") print $1 }' <<<"${all_pods}")
  selection_is_sane "cilium (${label})" \
    "$(grep -c . <<<"${unhealthy}" || true)" "$(grep -c . <<<"${all_pods}" || true)" || continue
  while read -r pod; do
    log "Deleting unhealthy Cilium pod: ${pod}"
    kubectl delete pod "${pod}" -n kube-system --ignore-not-found \
      --force --grace-period=0 || true
  done <<<"${unhealthy}"
done

#=========================================
# Delete ALL Unknown pods cluster-wide
# (controllers recreate; Unknown = node lost contact)
#
# This covers istio-system too. A separate istio-system block used to sit above
# this one, matching `$4=="Unknown"` against a single-namespace listing where $4
# is RESTARTS — so it never fired once, and the "Deleting Unknown Istio pod"
# lines people saw in the journal always came from here. Removed rather than
# repaired: fixing the index would only have force-deleted the same pods a few
# lines earlier. Don't re-add a per-namespace pass for this.
#
# Columns here are the `-A` layout: $1=NS $2=NAME $3=READY $4=STATUS. This was
# the one pod filter in the file whose indices were correct.
#=========================================
log "=== Deleting all cluster-wide Unknown pods ==="
all_pods=$(kubectl get pods -A --no-headers 2>/dev/null || true)
unknown=$(awk '$4=="Unknown" { print $1, $2 }' <<<"${all_pods}")
if selection_is_sane "cluster-wide Unknown pods" \
     "$(grep -c . <<<"${unknown}" || true)" "$(grep -c . <<<"${all_pods}" || true)"; then
  while read -r ns pod; do
    log "Deleting Unknown pod ${ns}/${pod}"
    kubectl delete pod "${pod}" -n "${ns}" --ignore-not-found \
      --force --grace-period=0 || true
  done <<<"${unknown}"
fi

#=========================================
# Delete CrashLoopBackOff / CreateContainerError
# pods in platform namespaces for a fresh start.
# Namespaces: platform-system, devtools, monitoring,
#             storage, security-system
#=========================================
PLATFORM_NS="platform-system devtools monitoring storage security-system"
log "=== Deleting CrashLoop/CreateContainerError pods in platform namespaces ==="
for ns in ${PLATFORM_NS}; do
  # $3 is STATUS in a single-namespace listing. This read $4 (RESTARTS) until
  # 2026-08-02, so the block never deleted anything in its entire life — the
  # same field-index mistake as the Cilium block, failing closed instead of
  # open. Activating it is a real behaviour change: CrashLooping platform pods
  # now actually get reset at boot, which is what the block was always for.
  # Deletion here is graceful (no --force) and stays that way.
  ns_pods=$(kubectl get pods -n "${ns}" --no-headers 2>/dev/null || true)
  [[ -z "${ns_pods}" ]] && continue
  crashed=$(awk '$3=="CrashLoopBackOff" || $3=="CreateContainerError" { print $1 }' <<<"${ns_pods}")
  selection_is_sane "${ns} CrashLoop/CreateContainerError" \
    "$(grep -c . <<<"${crashed}" || true)" "$(grep -c . <<<"${ns_pods}" || true)" || continue
  while read -r pod; do
    log "Deleting ${ns}/${pod} (status reset)"
    kubectl delete pod "${pod}" -n "${ns}" --ignore-not-found || true
  done <<<"${crashed}"
done

#=========================================
# Convergence poll: wait up to 5 min for
# non-running pods <= 3 (excl. scan-vulnerabilityreport)
#=========================================
# Converged means zero not-Ready pods, not "few enough". The old threshold was
# `<=3` with no recorded basis, and it is what let the 2026-07-29 boot report
# success while seaweedfs-volume-0 sat unready and its object store refused
# writes. A tolerance that nobody can justify is a tolerance that hides exactly
# the pod you needed to see; if the cluster genuinely settles with stragglers,
# the log below names them and the service still exits 0 (best-effort).
CONVERGE_MAX=0
log "=== Polling for convergence (<=${CONVERGE_MAX} not-Ready pods, up to 450s) ==="
# 45x10s: the pods cluster-heal kicks (Unknown ghosts, CrashLoops) can take
# several minutes to finish restarting after they reschedule; 300s was
# observed to expire ~1min before the cluster actually reached 0 non-running,
# producing a misleading "manual investigation" warning. 450s covers it.
CONVERGED=false
# Convergence means Running AND Ready. Counting STATUS alone lets a pod that is
# Running at 0/1 pass as converged — the seaweedfs-volume-0 case (2026-07-29),
# where the cluster read as healed while its object store was refusing writes.
# Columns here are the `-A` layout: $3=READY, $4=STATUS.
count_unready() {
  kubectl get pods -A --no-headers 2>/dev/null \
    | grep -v "scan-vulnerabilityreport" \
    | awk '$4!="Completed" && $4!="Succeeded" { split($3,r,"/"); if (r[1]!=r[2] || $4!="Running") c++ } END { print c+0 }'
}
for i in $(seq 1 45); do
  NOT_RUNNING=$(count_unready)
  log "  attempt ${i}/45 — ${NOT_RUNNING} not-Ready pod(s)"
  if [[ "${NOT_RUNNING}" -le "${CONVERGE_MAX}" ]]; then
    CONVERGED=true
    log "Cluster converged: ${NOT_RUNNING} not-Ready pods"
    break
  fi
  sleep 10
done

if [[ "${CONVERGED}" == "false" ]]; then
  FINAL=$(count_unready)
  log "Convergence not reached within 450s; final not-Ready count: ${FINAL}"
  # Name them. "3 pods not ready" is where the previous investigation stalled —
  # the count was visible for an hour and the pod behind it was not.
  kubectl get pods -A --no-headers 2>/dev/null \
    | grep -v "scan-vulnerabilityreport" \
    | awk '$4!="Completed" && $4!="Succeeded" { split($3,r,"/"); if (r[1]!=r[2] || $4!="Running") printf "  %s/%s %s %s\n", $1, $2, $3, $4 }' \
    | while read -r line; do log "${line}"; done
  log "Manual investigation may be required"
fi

log "cluster-heal complete; exiting 0"
exit 0
