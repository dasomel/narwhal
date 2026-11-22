#!/bin/bash
# CIS node file-permission hardening (1.1.9 CNI, 1.1.12 etcd dir, 1.1.19-21 PKI
# dir/certs/keys, 4.1.1 kubelet service + drop-ins, 4.1.7 CA, 4.1.9 kubelet config).
# All runtime readers (etcd, kube-apiserver, kube-controller-manager, kube-scheduler,
# kubelet, kube-vip) run as root (uid 0, verified on live master-1), so tightening
# to 600 / root:root (and /var/lib/etcd to etcd:etcd) is non-breaking.
# Idempotent. Non-fatal per item (set -u only).
# Optional arg 1: [root_dir] for fixture testing (defaults to "/").
set -u
ROOT_DIR="${1:-/}"
prefix="${ROOT_DIR%/}"
[ "${ROOT_DIR}" = "/" ] && prefix=""
changed=""

# 1.1.9 — CNI config files -> 600  (dir already 700)
if [ -d "${prefix}/etc/cni/net.d" ]; then
  find "${prefix}/etc/cni/net.d" -type f -exec chmod 600 {} \; 2>/dev/null && changed="$changed cni"
fi

# 4.1.9 — kubelet config.yaml -> 600
[ -f "${prefix}/var/lib/kubelet/config.yaml" ] && chmod 600 "${prefix}/var/lib/kubelet/config.yaml" && changed="$changed kubelet-config"

# 4.1.1 — kubelet service unit + drop-ins -> 600
[ -f "${prefix}/usr/lib/systemd/system/kubelet.service" ] && chmod 600 "${prefix}/usr/lib/systemd/system/kubelet.service" && changed="$changed kubelet-svc"
[ -f "${prefix}/etc/systemd/system/kubelet.service" ] && chmod 600 "${prefix}/etc/systemd/system/kubelet.service" && changed="$changed kubelet-svc-etc"
for d in "${prefix}/etc/systemd/system/kubelet.service.d" "${prefix}/usr/lib/systemd/system/kubelet.service.d"; do
  [ -d "$d" ] && find "$d" -type f -exec chmod 600 {} \; 2>/dev/null && changed="$changed ${d##*/}"
done

# 1.1.19 / 1.1.20 / 1.1.21 / 4.1.7 — Kubernetes PKI dir ownership root:root and all
# *.crt / *.key files -> 600 (D-NODE-PKI-PERMS: deployed k8s-cis-1.23 1.1.20 / KCV0068
# requires <=600 for all PKI certs, not just ca.crt; kubeadm init/certs renew writes
# certs as 0644, and upstream node-collector CMD-0019/0020/0021 return empty).
# Non-root script callers (02-join-control-plane.sh) fetch oidc-ca.crt via `sudo cat`.
[ -f "${prefix}/etc/kubernetes/pki/ca.crt" ] && chmod 600 "${prefix}/etc/kubernetes/pki/ca.crt" && changed="$changed ca.crt"
if [ -d "${prefix}/etc/kubernetes/pki" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    chown -R root:root "${prefix}/etc/kubernetes/pki" 2>/dev/null && changed="$changed pki-owner"
  fi
  find "${prefix}/etc/kubernetes/pki" -type f \( -name '*.crt' -o -name '*.key' \) -exec chmod 600 {} \; 2>/dev/null && changed="$changed pki-files"
fi

# 1.2.x — control-plane static pod manifest files -> 600 (kubeadm makes these 600
# natively; included here in case a manual edit reset them to 644).
if [ -z "${prefix}" ]; then
  [ -d /etc/kubernetes/manifests ] && chmod 600 /etc/kubernetes/manifests/*.yaml 2>/dev/null && changed="$changed manifests"
fi

# 1.1.12 — etcd data dir ownership etcd:etcd (masters only). etcd runs as root in the
# static pod, so root still has full access regardless of dir owner. Create the system
# user if absent.
if [ -d "${prefix}/var/lib/etcd" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    id -u etcd >/dev/null 2>&1 || useradd -r -M -s /usr/sbin/nologin etcd 2>/dev/null || groupadd -r etcd 2>/dev/null
    if chown -R etcd:etcd "${prefix}/var/lib/etcd" 2>/dev/null; then changed="$changed etcd-dir"; else echo "  WARN: etcd chown failed"; fi
  fi
fi

# D-NODE-PKI-RENEW (2026-11-22): `kubeadm certs renew` (and `kubeadm upgrade`) writes renewed
# PKI certs with hardcoded mode 0644 (pkiutil.WriteCert). Install this script at
# /usr/local/sbin/narwhal-harden-node-files.sh and enable a daily + post-boot systemd timer so
# mode resets after cert renewal or package updates are re-hardened automatically without a
# PathChanged chmod loop. Cost: one lightweight daily/boot oneshot unit.
# Escape hatch: `systemctl disable --now narwhal-harden-node-files.timer` or
# `INSTALL_RENEW_TIMER=false`.
if [ -z "${prefix}" ] && [ "$(id -u)" -eq 0 ] && [ "${INSTALL_RENEW_TIMER:-true}" = "true" ] \
  && [ -d /etc/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
  self_path="${BASH_SOURCE[0]:-}"
  if [ -n "${self_path}" ] && [ -f "${self_path}" ] && [ "${self_path}" != "/usr/local/sbin/narwhal-harden-node-files.sh" ]; then
    install -o root -g root -m 0700 "${self_path}" /usr/local/sbin/narwhal-harden-node-files.sh 2>/dev/null || true
  fi
  if [ -x /usr/local/sbin/narwhal-harden-node-files.sh ]; then
    cat <<'UNITEOF' > /etc/systemd/system/narwhal-harden-node-files.service
[Unit]
Description=Narwhal CIS node file-permission hardening (re-applies after kubeadm certs renew)
After=local-fs.target

[Service]
Type=oneshot
Environment=INSTALL_RENEW_TIMER=false
ExecStart=/usr/local/sbin/narwhal-harden-node-files.sh
UNITEOF
    cat <<'TIMEREOF' > /etc/systemd/system/narwhal-harden-node-files.timer
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
    chmod 644 /etc/systemd/system/narwhal-harden-node-files.service /etc/systemd/system/narwhal-harden-node-files.timer 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now narwhal-harden-node-files.timer 2>/dev/null && changed="$changed renew-timer" || true
  fi
fi

echo "$(hostname): hardened ->$changed"
