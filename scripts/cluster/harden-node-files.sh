#!/bin/bash
# CIS node file-permission hardening (1.1.9 CNI, 1.1.12 etcd dir, 4.1.1 kubelet
# service, 4.1.7 CA, 4.1.9 kubelet config). All targets are read by root/kubelet
# (root ignores perms/ownership), so tightening to 600 / etcd:etcd is non-breaking.
# Idempotent. Non-fatal per item (set -u only).
set -u
changed=""

# 1.1.9 — CNI config files -> 600  (dir already 700)
if [ -d /etc/cni/net.d ]; then
  find /etc/cni/net.d -type f -exec chmod 600 {} \; 2>/dev/null && changed="$changed cni"
fi

# 4.1.9 — kubelet config.yaml -> 600
[ -f /var/lib/kubelet/config.yaml ] && chmod 600 /var/lib/kubelet/config.yaml && changed="$changed kubelet-config"

# 4.1.1 — kubelet service unit + drop-ins -> 600
[ -f /usr/lib/systemd/system/kubelet.service ] && chmod 600 /usr/lib/systemd/system/kubelet.service && changed="$changed kubelet-svc"
for d in /etc/systemd/system/kubelet.service.d /usr/lib/systemd/system/kubelet.service.d; do
  [ -d "$d" ] && find "$d" -type f -exec chmod 600 {} \; 2>/dev/null && changed="$changed ${d##*/}"
done

# 4.1.7 — kubernetes CA file -> 600 (public cert, but CIS wants restrictive; root reads it)
[ -f /etc/kubernetes/pki/ca.crt ] && chmod 600 /etc/kubernetes/pki/ca.crt && changed="$changed ca.crt"

# 1.2.x — control-plane static pod manifest files -> 600 (kubeadm makes these 600
# natively; included here in case a manual edit reset them to 644).
[ -d /etc/kubernetes/manifests ] && chmod 600 /etc/kubernetes/manifests/*.yaml 2>/dev/null && changed="$changed manifests"

# 1.1.12 — etcd data dir ownership etcd:etcd (masters only). etcd runs as root in the
# static pod, so root still has full access regardless of dir owner. Create the system
# user if absent. Non-recursive: only the dir ownership is what CIS checks.
if [ -d /var/lib/etcd ]; then
  id -u etcd >/dev/null 2>&1 || useradd -r -M -s /usr/sbin/nologin etcd 2>/dev/null || groupadd -r etcd 2>/dev/null
  if chown -R etcd:etcd /var/lib/etcd 2>/dev/null; then changed="$changed etcd-dir"; else echo "  WARN: etcd chown failed"; fi
fi

echo "$(hostname): hardened ->$changed"
