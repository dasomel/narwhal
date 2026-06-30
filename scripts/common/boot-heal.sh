#!/bin/bash
set -euo pipefail

# narwhal-boot-heal: per-node stale-container wedge recovery
# Runs as narwhal-boot-heal.service (oneshot) on every boot.
# Safe on first provisioning: exits immediately if kubelet.conf absent.

LOG_PREFIX="[narwhal-boot-heal]"

log() { echo "${LOG_PREFIX} $*"; }

#=========================================
# GUARD: node not yet joined — never
# interfere with first provisioning
#=========================================
if [[ ! -f /etc/kubernetes/kubelet.conf ]]; then
  log "kubelet.conf absent — node not yet joined; skipping"
  exit 0
fi

log "Node $(hostname) is cluster-joined; starting boot-heal check"

#=========================================
# Allow containerd + kubelet to settle
#=========================================
log "Sleeping 45s to let containerd/kubelet settle..."
sleep 45

#=========================================
# Wedge detection
# Signals (any one → restart):
#   1. kubelet not active
#   2. crictl ps command fails
#   3. Containers stuck in non-Running/non-Exited state (Unknown, Created)
#=========================================
CRICTL_SOCK="unix:///run/containerd/containerd.sock"
WEDGE=false
WEDGE_REASON=""

if ! systemctl is-active --quiet kubelet; then
  WEDGE=true
  WEDGE_REASON="kubelet inactive ($(systemctl is-active kubelet || true))"
elif ! crictl --runtime-endpoint "${CRICTL_SOCK}" ps &>/dev/null; then
  WEDGE=true
  WEDGE_REASON="crictl ps failed — containerd may be wedged"
else
  # Count containers in a bad transient state (not Running, not Exited).
  # "Unknown" and stuck "Created" are hallmarks of the stale-container wedge.
  STUCK=$(crictl --runtime-endpoint "${CRICTL_SOCK}" ps -a 2>/dev/null \
    | awk 'NR>1 && $4!="Running" && $4!="Exited" { c++ } END { print c+0 }')
  if [[ "${STUCK}" -gt 0 ]]; then
    WEDGE=true
    WEDGE_REASON="${STUCK} container(s) in non-Running/non-Exited state (Unknown/Created)"
  fi
fi

#=========================================
# Recovery: restart containerd then kubelet
#=========================================
if [[ "${WEDGE}" == "true" ]]; then
  log "Wedge detected: ${WEDGE_REASON}"
  log "Restarting containerd..."
  systemctl restart containerd || { log "WARN: containerd restart failed; continuing"; }
  sleep 5
  log "Restarting kubelet..."
  systemctl restart kubelet || { log "WARN: kubelet restart failed; continuing"; }
  log "Restart complete; systemd unit exits — journald captures this log"
else
  log "No wedge detected on $(hostname); node looks healthy"
fi

exit 0
