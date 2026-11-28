#!/bin/bash
# Shared helper for CIS 4.2.6 (kubelet protectKernelDefaults=true) kernel sysctl setup.
# D-KUBELET-KERNEL-DEFAULTS: kubelet refuses to start when protectKernelDefaults=true if any
# of the 6 kernel parameters below does not match the live /proc/sys value. Persist under
# /etc/sysctl.d/95-kubelet-kernel-defaults.conf, apply via sysctl --system (catching any
# later override file that would break reboot survival), and verify every live value.
# Cost: fails provisioning loudly rather than letting kubelet crashloop on startup.
# Escape hatch: roll back protectKernelDefaults via scripts/ops/apply-kubelet-kernel-defaults.sh --rollback.

KUBELET_KERNEL_SYSCTL_FILE="${KUBELET_KERNEL_SYSCTL_FILE:-/etc/sysctl.d/95-kubelet-kernel-defaults.conf}"

# Canonical key=expected pairs checked by Kubernetes v1.35 kubelet container_manager_linux.go.
KUBELET_KERNEL_SYSCTL_PAIRS=(
  "vm.overcommit_memory=1"
  "vm.panic_on_oom=0"
  "kernel.panic=10"
  "kernel.panic_on_oops=1"
  "kernel.keys.root_maxkeys=1000000"
  "kernel.keys.root_maxbytes=25000000"
)

_kubelet_sysctl_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

verify_kubelet_kernel_defaults() {
  local proc_root="${KUBELET_SYSCTL_PROC_ROOT:-/proc/sys}"
  local pair key expected proc_path actual bad=0
  for pair in "${KUBELET_KERNEL_SYSCTL_PAIRS[@]}"; do
    key="${pair%%=*}"
    expected="${pair#*=}"
    proc_path="${proc_root}/${key//./\/}"
    if [ ! -r "${proc_path}" ]; then
      echo "ERROR: kubelet kernel default sysctl unreadable: ${proc_path} (expected ${key}=${expected})" >&2
      bad=1
      continue
    fi
    actual="$(tr -d '[:space:]' < "${proc_path}")"
    if [ "${actual}" != "${expected}" ]; then
      echo "ERROR: kubelet kernel default mismatch: ${key}=${actual} (expected ${expected})" >&2
      bad=1
    fi
  done
  return "${bad}"
}

ensure_kubelet_kernel_defaults() {
  local sysctl_file="${KUBELET_KERNEL_SYSCTL_FILE:-/etc/sysctl.d/95-kubelet-kernel-defaults.conf}"
  local src_conf=""
  local candidate
  for candidate in \
    "${STAGE_DIR:-/home/vagrant}/scripts/ops/lib/95-kubelet-kernel-defaults.conf" \
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../ops/lib" 2>/dev/null && pwd || true)/95-kubelet-kernel-defaults.conf"; do
    if [ -r "${candidate}" ]; then
      src_conf="${candidate}"
      break
    fi
  done
  if [ -z "${src_conf}" ]; then
    echo "ERROR: 95-kubelet-kernel-defaults.conf source not found" >&2
    return 1
  fi

  _kubelet_sysctl_sudo install -d -o root -g root -m 0755 "$(dirname "${sysctl_file}")"
  _kubelet_sysctl_sudo install -o root -g root -m 0644 "${src_conf}" "${sysctl_file}"
  # Apply the dedicated file first, then reload the full sysctl.d precedence order so any
  # higher-numbered override (e.g. 99-*.conf) surfaces NOW rather than after a reboot.
  _kubelet_sysctl_sudo sysctl -p "${sysctl_file}" >/dev/null
  _kubelet_sysctl_sudo sysctl --system >/dev/null
  if ! verify_kubelet_kernel_defaults; then
    echo "ERROR: one or more CIS 4.2.6 sysctls failed verification after sysctl --system (check for overriding /etc/sysctl.d/9[6-9]-*.conf or /etc/sysctl.conf)" >&2
    return 1
  fi
}
