#!/usr/bin/env bash
# check-vm-disk.sh (D-VM-DISK-GUARD): host-side warning when a Vagrant/VMware VM disk has grown
# far past what its guest actually uses, or the host is running out of space.
#
# Why: each VM disk is a growable sparse linked clone. Every block the guest ever writes stays
# allocated on the host, NVMe has no discard and the linked clone cannot be shrunk
# (docs/vagrant/operations.md "VM 디스크 증가와 회수"). worker-3 reached 298G with 43G used in the
# guest, the host hit 96% and ENOSPC corrupted containerd snapshots. The only reclaim is to
# recreate the node, so the point is to notice early.
#
# Usage: scripts/ops/check-vm-disk.sh [--strict]
#   default  prints [OK]/[WARN] per VM and always exits 0 (safe to call from up.sh)
#   --strict exits 1 when anything warns
# Thresholds (GB, env-overridable):
#   VM_DISK_WARN_GAP_GB  host dir size minus guest used  (default 30)
#   VM_DISK_WARN_ABS_GB  host dir size alone             (default 120)
#   VM_DISK_MIN_FREE_GB  free space on the host volume   (default 40, the 2026-11-05 guard)
# The guest's used space includes the linked-clone base image that the host dir does not, so a
# fresh VM shows a negative gap; only growth beyond the guest's own usage trips the gap check.
# Test seam: VM_DISK_FIXTURE=<file> with "vm host_gb guest_gb" lines and VM_DISK_FREE_GB replace
# du/ssh/df so the thresholds can be exercised without VMs.
set -euo pipefail

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

WARN_GAP_GB="${VM_DISK_WARN_GAP_GB:-30}"
WARN_ABS_GB="${VM_DISK_WARN_ABS_GB:-120}"
MIN_FREE_GB="${VM_DISK_MIN_FREE_GB:-40}"
MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"
WORKER_IP_BASE="${WORKER_IP_BASE:-192.168.56.2}"

WARNED=0
warn() { echo "[WARN] $*"; WARNED=1; }
ok() { echo "[OK] $*"; }

# vm_ip <vm>: the host-only address, which is static and unique (vagrant ssh can reach the wrong
# VM when VMware NAT hands two MACs the same address; see scripts/up.sh master1_exec).
vm_ip() {
  case "$1" in
    master-[1-3]) echo "${MASTER_IP_BASE}$(( ${1#master-} - 1 ))" ;;
    worker-[1-3]) echo "${WORKER_IP_BASE}${1#worker-}" ;;
  esac
}

host_gb() {
  local vmx dir kb
  vmx=$(cat ".vagrant/machines/$1/vmware_desktop/id" 2>/dev/null) || return 1
  dir=$(dirname "${vmx}")
  [ -d "${dir}" ] || return 1
  kb=$(du -sk "${dir}" | awk '{print $1}')
  echo $(( kb / 1048576 ))
}

guest_gb() {
  local key=".vagrant/machines/$1/vmware_desktop/private_key" out
  [ -r "${key}" ] || return 1
  out=$(ssh -n -i "${key}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR -o ConnectTimeout=5 "vagrant@$(vm_ip "$1")" \
    "df -BG --output=used / | tail -1" 2>/dev/null | tr -dc '0-9') || return 1
  [ -n "${out}" ] || return 1
  echo "${out}"
}

check_vm() {
  local vm="$1" h="$2" g="$3"
  if [ -z "${h}" ]; then
    echo "[SKIP] ${vm}: no VM directory (not created yet)"
    return
  fi
  if [ -z "${g}" ]; then
    if [ "${h}" -ge "${WARN_ABS_GB}" ]; then
      warn "${vm}: host disk ${h}G >= ${WARN_ABS_GB}G (guest df unavailable). Plan a recreate: docs/vagrant/operations.md"
    else
      ok "${vm}: host disk ${h}G (guest df unavailable)"
    fi
    return
  fi
  local gap=$(( h - g ))
  if [ "${gap}" -ge "${WARN_GAP_GB}" ] || [ "${h}" -ge "${WARN_ABS_GB}" ]; then
    warn "${vm}: host disk ${h}G vs guest used ${g}G (gap ${gap}G; thresholds gap ${WARN_GAP_GB}G / abs ${WARN_ABS_GB}G). The vmdk never shrinks; recreate the node to reclaim: docs/vagrant/operations.md"
  else
    ok "${vm}: host disk ${h}G vs guest used ${g}G (gap ${gap}G)"
  fi
}

echo "=== VM disk growth check ==="
if [ -n "${VM_DISK_FIXTURE:-}" ]; then
  while read -r vm h g; do
    [ -n "${vm}" ] && check_vm "${vm}" "${h}" "${g}"
  done < "${VM_DISK_FIXTURE}"
  FREE_GB="${VM_DISK_FREE_GB:-1000}"
else
  for vm in master-1 master-2 master-3 worker-1 worker-2 worker-3; do
    check_vm "${vm}" "$(host_gb "${vm}" || true)" "$(guest_gb "${vm}" || true)"
  done
  FREE_GB=$(( $(df -k . | awk 'NR==2 {print $4}') / 1048576 ))
fi

if [ "${FREE_GB}" -lt "${MIN_FREE_GB}" ]; then
  warn "host free space ${FREE_GB}G < ${MIN_FREE_GB}G: a full host corrupts containerd snapshots (ENOSPC)"
else
  ok "host free space ${FREE_GB}G (>= ${MIN_FREE_GB}G)"
fi

[ "${STRICT}" -eq 1 ] && [ "${WARNED}" -eq 1 ] && exit 1
exit 0
