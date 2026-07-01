#!/bin/bash
set -euo pipefail

# narwhal-boot-heal: per-node stale-container wedge recovery
# Runs as narwhal-boot-heal.service (oneshot) on every boot, AND as
# narwhal-boot-heal.timer (periodic, every 2min) for the life of the node.
# Safe on first provisioning: exits immediately if kubelet.conf absent.
#
# Usage: narwhal-boot-heal.sh [--periodic]
#   (no args)   boot-time mode: sleeps 45s to let containerd/kubelet settle
#   --periodic  periodic-timer mode: skips the settle sleep (node already up)

LOG_PREFIX="[narwhal-boot-heal]"
MODE="boot"
[[ "${1:-}" == "--periodic" ]] && MODE="periodic"

log() { echo "${LOG_PREFIX} [${MODE}] $*"; }

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
# D-clock: ensure clock is synced before wedge detection
# (narwhal-clock-sync.service already gates kubelet, but boot-heal
# may fire before waitsync completes on slow NTP paths). Only needed
# once at boot — periodic runs assume the clock has long since synced.
#=========================================
if [[ "${MODE}" == "boot" ]]; then
  log "Ensuring clock sync before wedge checks..."
  chronyc waitsync 10 0.1 1 0 2>/dev/null || true

  #=========================================
  # Allow containerd + kubelet to settle
  #=========================================
  log "Sleeping 45s to let containerd/kubelet settle..."
  sleep 45
fi

#=========================================
# Wedge detection
#
# NOTE: `crictl` is NOT installed on narwhal nodes (containerd 2.x's
# built-in CRI is used directly by kubelet; no standalone crictl binary
# ships in this image). An earlier version of this script shelled out to
# `crictl ps`, which always fails with "command not found" — making the
# old "crictl ps failed" branch fire WEDGE=true unconditionally on every
# single run, healthy or not. That is exactly the kind of false-positive
# that would make a 2-minute periodic timer restart containerd+kubelet
# on a perfectly healthy node forever. Detection below uses only tools
# confirmed present on every node: `systemctl`, `ctr` (part of
# containerd itself), and `journalctl`.
#
# Signals (any one -> restart):
#   1. kubelet not active
#   2. `ctr` cannot talk to containerd (containerd unresponsive/wedged)
#   3. Repeated "failed to reserve container name" in the kubelet
#      journal within the last 90s — the exact signature of the
#      containerd 2.x stale-container-name-reservation wedge (verified
#      2026-07-02: a killed/reparented container task leaves its name
#      reserved in containerd's CRI store, so every kubelet retry to
#      recreate it fails with this specific message, repeating every
#      ~10-15s). A healthy node never emits this message, so this signal
#      has effectively zero false-positive risk.
#=========================================
WEDGE=false
WEDGE_REASON=""

if ! systemctl is-active --quiet kubelet; then
  WEDGE=true
  WEDGE_REASON="kubelet inactive ($(systemctl is-active kubelet || true))"
elif ! ctr --namespace k8s.io containers ls &>/dev/null; then
  WEDGE=true
  WEDGE_REASON="ctr containers ls failed — containerd may be wedged/unresponsive"
else
  RESERVE_ERRORS=$(journalctl -u kubelet --since "-90 seconds" --no-pager 2>/dev/null \
    | grep -c "failed to reserve container name" || true)
  if [[ "${RESERVE_ERRORS}" -ge 2 ]]; then
    WEDGE=true
    WEDGE_REASON="${RESERVE_ERRORS} repeated 'failed to reserve container name' errors in the last 90s — stale container-name-reservation wedge"
  fi
fi

#=========================================
# Recovery: restart containerd then kubelet.
# If that alone doesn't clear the wedge within ~30s (only meaningful
# for the reserve-name signal — the same repeated-error check is
# re-run), escalate to the deep recovery: kill the task still holding
# the reserved container name, remove its containerd container object
# (drops the CRI-store name reservation), then restart kubelet again.
#=========================================
deep_recovery() {
  log "Attempting deep recovery: clearing stuck container-name reservations"
  local ids
  ids=$(journalctl -u kubelet --since "-120 seconds" --no-pager 2>/dev/null \
    | grep -oP 'is reserved for \\"\K[a-f0-9]{20,}(?=\\")' | sort -u || true)
  if [[ -z "${ids}" ]]; then
    log "WARN: no specific reserved container IDs found in recent kubelet logs; skipping targeted cleanup"
    return
  fi
  local id pid
  for id in ${ids}; do
    pid=$(ctr -n k8s.io tasks ls 2>/dev/null | awk -v i="${id}" '$1==i{print $2}')
    if [[ -n "${pid}" ]]; then
      log "Killing task pid ${pid} for stuck container ${id}"
      kill -9 "${pid}" 2>/dev/null || true
      sleep 1
    fi
    ctr -n k8s.io tasks rm "${id}" &>/dev/null || true
    ctr -n k8s.io containers rm "${id}" &>/dev/null || true
    rm -rf "/var/lib/containerd/io.containerd.grpc.v1.cri/containers/${id}"
    log "Cleared stale reservation for container ${id}"
  done
  log "Deep recovery cleanup applied; restarting kubelet"
  systemctl restart kubelet || log "WARN: kubelet restart failed after deep recovery"
}

if [[ "${WEDGE}" == "true" ]]; then
  log "Wedge detected: ${WEDGE_REASON}"
  log "Restarting containerd..."
  systemctl restart containerd || { log "WARN: containerd restart failed; continuing"; }
  sleep 5
  log "Restarting kubelet..."
  systemctl restart kubelet || { log "WARN: kubelet restart failed; continuing"; }

  if [[ "${WEDGE_REASON}" == *"reserve"* ]]; then
    log "Waiting 30s to verify the reserve-name wedge cleared..."
    sleep 30
    RECHECK=$(journalctl -u kubelet --since "-30 seconds" --no-pager 2>/dev/null \
      | grep -c "failed to reserve container name" || true)
    if [[ "${RECHECK}" -ge 2 ]]; then
      log "Plain restart did not clear the wedge (${RECHECK} reserve errors still occurring in last 30s); escalating"
      deep_recovery
    else
      log "Plain restart appears to have cleared the wedge"
    fi
  fi
  log "Restart/recovery complete; systemd unit exits — journald captures this log"
else
  log "No wedge detected on $(hostname); node looks healthy"
fi

exit 0
