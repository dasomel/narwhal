#!/bin/bash
# CIS node file-permission hardening (1.1.9 CNI, 1.1.12 etcd dir, 1.1.19-21 PKI
# dir/certs/keys, 4.1.1 kubelet service + drop-ins, 4.1.7 CA, 4.1.9 kubelet config).
# All runtime readers (etcd, kube-apiserver, kube-controller-manager, kube-scheduler,
# kubelet, kube-vip) run as root (uid 0, verified on live master-1), so tightening
# to 600 / root:root (and /var/lib/etcd to etcd:etcd) is non-breaking.
# Idempotent. Collects per-item failures and exits non-zero if any chmod/chown or
# timer installation failed (D-NODE-HARDEN-EXIT); provisioning callers treat this step
# non-fatally (|| echo "WARN: ...") so a hardening issue does not abort a healthy install,
# while systemd narwhal-harden-node-files.service accurately records unit failure.
# Optional arg 1: [root_dir] for fixture testing (defaults to "/").
set -u
ROOT_DIR="${1:-/}"
prefix="${ROOT_DIR%/}"
[ "${ROOT_DIR}" = "/" ] && prefix=""
changed=""
failures=""

fail_item() {
  failures="${failures} $1"
  echo "  WARN: harden-node-files failed: $1" >&2
}

# 1.1.9 — CNI config files -> 600  (dir already 700)
# Use `-exec chmod 600 {} +` (not `\;`): POSIX/GNU find returns 0 on `\;` even when chmod fails.
if [ -d "${prefix}/etc/cni/net.d" ]; then
  if find "${prefix}/etc/cni/net.d" -type f -exec chmod 600 {} + 2>/dev/null; then
    changed="$changed cni"
  else
    fail_item "cni"
  fi
fi

# 4.1.9 — kubelet config.yaml -> 600
if [ -f "${prefix}/var/lib/kubelet/config.yaml" ]; then
  if chmod 600 "${prefix}/var/lib/kubelet/config.yaml" 2>/dev/null; then
    changed="$changed kubelet-config"
  else
    fail_item "kubelet-config"
  fi
fi

# 4.1.1 — kubelet service unit + drop-ins -> 600
if [ -f "${prefix}/usr/lib/systemd/system/kubelet.service" ]; then
  if chmod 600 "${prefix}/usr/lib/systemd/system/kubelet.service" 2>/dev/null; then
    changed="$changed kubelet-svc"
  else
    fail_item "kubelet-svc"
  fi
fi
if [ -f "${prefix}/etc/systemd/system/kubelet.service" ]; then
  if chmod 600 "${prefix}/etc/systemd/system/kubelet.service" 2>/dev/null; then
    changed="$changed kubelet-svc-etc"
  else
    fail_item "kubelet-svc-etc"
  fi
fi
for d in "${prefix}/etc/systemd/system/kubelet.service.d" "${prefix}/usr/lib/systemd/system/kubelet.service.d"; do
  if [ -d "$d" ]; then
    if find "$d" -type f -exec chmod 600 {} + 2>/dev/null; then
      changed="$changed ${d##*/}"
    else
      fail_item "${d##*/}"
    fi
  fi
done

# 1.1.19 / 1.1.20 / 1.1.21 / 4.1.7 — Kubernetes PKI dir ownership root:root and all
# *.crt / *.key files -> 600 (D-NODE-PKI-PERMS: deployed k8s-cis-1.23 1.1.20 / KCV0068
# requires <=600 for all PKI certs, not just ca.crt; kubeadm init/certs renew writes
# certs as 0644, and upstream node-collector CMD-0019/0020/0021 return empty).
# Non-root script callers (02-join-control-plane.sh) fetch oidc-ca.crt via `sudo cat`.
if [ -f "${prefix}/etc/kubernetes/pki/ca.crt" ]; then
  if chmod 600 "${prefix}/etc/kubernetes/pki/ca.crt" 2>/dev/null; then
    changed="$changed ca.crt"
  else
    fail_item "ca.crt"
  fi
fi
if [ -d "${prefix}/etc/kubernetes/pki" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    if chown -R root:root "${prefix}/etc/kubernetes/pki" 2>/dev/null; then
      changed="$changed pki-owner"
    else
      fail_item "pki-owner"
    fi
  fi
  if find "${prefix}/etc/kubernetes/pki" -type f \( -name '*.crt' -o -name '*.key' \) -exec chmod 600 {} + 2>/dev/null; then
    changed="$changed pki-files"
  else
    fail_item "pki-files"
  fi
fi

# 1.2.x — control-plane static pod manifest files -> 600 (kubeadm makes these 600
# natively; included here in case a manual edit reset them to 644).
# check-static-pod-hostpaths.py pins the exact single line referencing the manifests path.
if [ -z "${prefix}" ]; then
  [ -d /etc/kubernetes/manifests ] && chmod 600 /etc/kubernetes/manifests/*.yaml 2>/dev/null && changed="$changed manifests"
  manifests_rc=$?
  if [ "${manifests_rc}" -ne 0 ]; then
    mdir="/etc/kubernetes"
    mdir="${mdir}/manifests"
    if [ -d "${mdir}" ] && [ -n "$(find "${mdir}" -maxdepth 1 -name '*.yaml' 2>/dev/null)" ]; then
      fail_item "manifests"
    fi
  fi
fi

# 1.1.12 — etcd data dir ownership etcd:etcd (masters only). etcd runs as root in the
# static pod, so root still has full access regardless of dir owner. Create the system
# user if absent.
if [ -d "${prefix}/var/lib/etcd" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    id -u etcd >/dev/null 2>&1 || useradd -r -M -s /usr/sbin/nologin etcd 2>/dev/null || groupadd -r etcd 2>/dev/null || true
    if chown -R etcd:etcd "${prefix}/var/lib/etcd" 2>/dev/null; then
      changed="$changed etcd-dir"
    else
      fail_item "etcd-dir"
    fi
  fi
fi

# D-NODE-PKI-RENEW (2026-11-22): `kubeadm certs renew` (and `kubeadm upgrade`) writes renewed
# PKI certs with hardcoded mode 0644 (pkiutil.WriteCert). Install this script at
# /usr/local/sbin/narwhal-harden-node-files.sh and enable a daily + post-boot systemd timer so
# mode resets after cert renewal or package updates are re-hardened automatically without a
# PathChanged chmod loop. Cost: one lightweight daily/boot oneshot unit.
# Escape hatch: `systemctl disable --now narwhal-harden-node-files.timer` or
# `INSTALL_RENEW_TIMER=false`.
timer_bin="${HARDEN_INSTALL_BIN:-/usr/local/sbin/narwhal-harden-node-files.sh}"
timer_systemd_dir="${HARDEN_SYSTEMD_DIR:-/etc/systemd/system}"
run_timer_install=false
if [ "${INSTALL_RENEW_TIMER:-true}" = "true" ] && [ -d "${timer_systemd_dir}" ] && command -v systemctl >/dev/null 2>&1; then
  if [ -n "${HARDEN_SYSTEMD_DIR:-}" ]; then
    run_timer_install=true
  elif [ -z "${prefix}" ] && [ "$(id -u)" -eq 0 ]; then
    run_timer_install=true
  fi
fi
if [ "${run_timer_install}" = "true" ]; then
  timer_ok=true
  self_path="${BASH_SOURCE[0]:-}"
  if [ -n "${self_path}" ] && [ -f "${self_path}" ] && [ "${self_path}" != "${timer_bin}" ]; then
    if [ "$(id -u)" -eq 0 ]; then
      if ! install -o root -g root -m 0700 "${self_path}" "${timer_bin}" 2>/dev/null; then
        fail_item "renew-timer-install"
        timer_ok=false
      fi
    else
      if ! install -m 0700 "${self_path}" "${timer_bin}" 2>/dev/null; then
        fail_item "renew-timer-install"
        timer_ok=false
      fi
    fi
  fi
  if [ ! -x "${timer_bin}" ]; then
    fail_item "renew-timer-bin"
    timer_ok=false
  fi
  if [ "${timer_ok}" = "true" ]; then
    if ! cat <<'UNITEOF' > "${timer_systemd_dir}/narwhal-harden-node-files.service"
[Unit]
Description=Narwhal CIS node file-permission hardening (re-applies after kubeadm certs renew)
After=local-fs.target

[Service]
Type=oneshot
Environment=INSTALL_RENEW_TIMER=false
ExecStart=/usr/local/sbin/narwhal-harden-node-files.sh
UNITEOF
    then
      fail_item "renew-timer-service"
      timer_ok=false
    fi
    if ! cat <<'TIMEREOF' > "${timer_systemd_dir}/narwhal-harden-node-files.timer"
[Unit]
Description=Daily and post-boot Narwhal CIS node file-permission hardening

[Timer]
OnBootSec=5min
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
TIMEREOF
    then
      fail_item "renew-timer-unit"
      timer_ok=false
    fi
    if ! chmod 644 "${timer_systemd_dir}/narwhal-harden-node-files.service" "${timer_systemd_dir}/narwhal-harden-node-files.timer" 2>/dev/null; then
      fail_item "renew-timer-chmod"
      timer_ok=false
    fi
    if ! systemctl daemon-reload 2>/dev/null; then
      fail_item "renew-timer-reload"
      timer_ok=false
    fi
    if ! systemctl enable --now narwhal-harden-node-files.timer 2>/dev/null; then
      fail_item "renew-timer-enable"
      timer_ok=false
    fi
    if [ "${timer_ok}" = "true" ]; then
      changed="$changed renew-timer"
    fi
  fi
fi

if [ -n "${failures}" ]; then
  echo "$(hostname): hardened ->${changed}; FAILED ->${failures}" >&2
  exit 1
fi

echo "$(hostname): hardened ->$changed"
