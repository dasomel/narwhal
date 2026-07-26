#!/bin/bash
set -euo pipefail

echo "=== Prerequisites Installation ==="

#=========================================
# Force apt to use IPv4
#=========================================
# These VMs have no working IPv6 route. Mirrors like pkgs.k8s.io resolve to IPv6
# first, so apt picks the AAAA record and fails with "Network is unreachable"
# (observed on workers fetching kubeadm/kubelet/kubectl on Ubuntu 26.04). Pin IPv4.
echo 'Acquire::ForceIPv4 "true";' | sudo tee /etc/apt/apt.conf.d/99force-ipv4 >/dev/null

CLUSTER_NAME="${CLUSTER_NAME:-narwhal}"
MASTER_COUNT="${MASTER_COUNT:-3}"
MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
WORKER_COUNT="${WORKER_COUNT:-3}"
WORKER_IP_BASE="${WORKER_IP_BASE:-192.168.56.2}"
NODE_IP="${NODE_IP:-}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

#=========================================
# Ensure private network IP is assigned
# VMware Vagrant plugin sometimes fails to create netplan config
#=========================================
if [ -n "${NODE_IP}" ]; then
  echo "Ensuring private network IP ${NODE_IP} is assigned..."

  # Find the interface that should have this IP (second ethernet, not the NAT one)
  # Wait for the interface to appear
  PRIV_IFACE=""
  for attempt in {1..30}; do
    # Look for interface with the IP already assigned (|| true: grep returns 1 on no match, pipefail would exit)
    PRIV_IFACE=$(ip -o addr show | grep "${NODE_IP}" | awk '{print $2}' | head -1 || true)
    if [ -n "${PRIV_IFACE}" ]; then
      echo "Private network IP already assigned on ${PRIV_IFACE}"
      break
    fi

    # Find the second ethernet interface (not the NAT one)
    NAT_IFACE=$(ip -o addr show | grep "172\\.16\\." | awk '{print $2}' | head -1)
    CANDIDATE=$(ip -o link show | grep -v lo | grep -v "${NAT_IFACE:-__none__}" | awk -F': ' '{print $2}' | head -1)

    if [ -n "${CANDIDATE}" ]; then
      # Check if it has no IPv4 address
      HAS_IP=$(ip -o -4 addr show "${CANDIDATE}" 2>/dev/null | wc -l)
      if [ "${HAS_IP}" -eq 0 ]; then
        echo "Configuring ${CANDIDATE} with ${NODE_IP}/24 via netplan..."
        sudo tee /etc/netplan/50-vagrant.yaml > /dev/null <<NETEOF
---
network:
  version: 2
  renderer: networkd
  ethernets:
    ${CANDIDATE}:
      addresses:
      - ${NODE_IP}/24
NETEOF
        sudo chmod 600 /etc/netplan/50-vagrant.yaml
        sudo netplan apply 2>&1 || true
        sleep 2
        # Verify
        if ip -o addr show | grep -q "${NODE_IP}"; then
          PRIV_IFACE="${CANDIDATE}"
          echo "Private network IP ${NODE_IP} assigned on ${PRIV_IFACE}"
          break
        fi
      fi
    fi

    echo "Waiting for private network interface... (${attempt}/30)"
    sleep 2
  done

  if [ -z "${PRIV_IFACE}" ]; then
    echo "ERROR: Could not assign private network IP ${NODE_IP}"
    ip -o addr show
    exit 1
  fi
fi

#=========================================
# Configure DNS (fix VMware DNS issues)
#=========================================
echo "Configuring DNS..."

# Add master-1 dnsmasq as primary DNS for *.${DOMAIN} resolution
# The ~${DOMAIN} routing domain ensures only matching queries go to dnsmasq
# On master-1, 10-dnsmasq.sh replaces systemd-resolved entirely, so this is a no-op.
# Cloud: dnsmasq is skipped (L2-bound), so do NOT repoint DNS at the master nodes
# (that would break resolution). Keep the default cloud resolver; *.${DOMAIN} is
# covered by /etc/hosts entries below + in-cluster CoreDNS.
if [ "${PROVIDER:-vagrant}" != "kakao" ] && systemctl is-active --quiet systemd-resolved; then
  sudo mkdir -p /etc/systemd/resolved.conf.d
  # Build DNS list dynamically from all master IPs
  MASTER_DNS=""
  for idx in $(seq 0 $((MASTER_COUNT - 1))); do
    MASTER_DNS="${MASTER_DNS}${MASTER_IP_BASE}${idx} "
  done
  # Primary DNS = master dnsmasq nodes only; public resolvers go to FallbackDNS.
  # Cramming masters + 8.8.8.8 + 8.8.4.4 into DNS= exceeds the glibc 3-nameserver
  # limit ("Nameserver limits were exceeded, some omitted") so the public ones
  # were silently dropped from /etc/resolv.conf anyway. FallbackDNS is the correct
  # place for them — systemd-resolved uses it when the primary servers fail.
  sudo tee /etc/systemd/resolved.conf.d/dns.conf << EOF
[Resolve]
DNS=${MASTER_DNS}
FallbackDNS=8.8.8.8 8.8.4.4 1.1.1.1
Domains=~${DOMAIN}
EOF
  sudo systemctl restart systemd-resolved
fi

# Verify DNS is working
echo "Testing DNS resolution..."
nslookup registry.k8s.io || echo "DNS test warning, continuing..."

# Configure /etc/hosts
cat <<EOF | sudo tee -a /etc/hosts

# Kubernetes Cluster
${VIP_ADDRESS}   ${CLUSTER_NAME}-vip
EOF

# Add master nodes
for i in $(seq 1 "$MASTER_COUNT"); do
  MASTER_IP="${MASTER_IP_BASE}$((i - 1))"
  echo "${MASTER_IP}   ${CLUSTER_NAME}-master-${i}" | sudo tee -a /etc/hosts
done

# Backward compatibility alias (narwhal-master -> master-1)
echo "${MASTER_IP_BASE}0   ${CLUSTER_NAME}-master" | sudo tee -a /etc/hosts

# Add worker nodes
for i in $(seq 1 "$WORKER_COUNT"); do
  echo "${WORKER_IP_BASE}${i}   ${CLUSTER_NAME}-worker-${i}" | sudo tee -a /etc/hosts
done

# DNS leak fix: systemd-resolved leaks *.local.narwhal.internal to NAT interface (enp2s0),
# causing harbor.local.narwhal.internal to resolve to its public AWS IP → TLS 'unrecognized name'.
# Pin harbor to the APISIX gateway's MetalLB LB IP so all nodes resolve it
# internally. NOTE: this is the APISIX LB IP (192.168.56.200), NOT the
# kube-apiserver VIP (${VIP_ADDRESS}=192.168.56.100) — harbor is served via APISIX.
APISIX_LB_IP="${APISIX_LB_IP:-192.168.56.200}"
HARBOR_HOSTS_ENTRY="${APISIX_LB_IP}   harbor.${DOMAIN}"
if ! grep -q "harbor.${DOMAIN}" /etc/hosts; then
  echo "${HARBOR_HOSTS_ENTRY}" | sudo tee -a /etc/hosts
  echo "Added harbor.${DOMAIN} -> ${APISIX_LB_IP} to /etc/hosts"
else
  echo "harbor.${DOMAIN} already present in /etc/hosts, skipping"
fi
#=========================================
# Configure Clock Synchronization (chrony)
#=========================================
echo "Configuring clock synchronization via chrony..."
if ! command -v chronyc &>/dev/null; then
  echo "Installing chrony..."
  sudo apt-get update || echo "WARN: apt-get update issue, continuing..."
  sudo apt-get install -y chrony || echo "WARN: chrony install issue, continuing..."
fi

# Detect the chrony unit name (chrony.service on 24.04; chronyd.service on some releases).
# Ubuntu 26.04 ships no systemd-timesyncd, so chrony is the single source of truth — no
# timesyncd fallback (it only produced noisy "Unit not found" errors on 26.04).
sudo systemctl daemon-reload 2>/dev/null || true
CHRONY_SVC=""
for c in chrony chronyd; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${c}\.service"; then
    CHRONY_SVC="$c"
    break
  fi
done
if [ -n "${CHRONY_SVC}" ]; then
  # D12: Ubuntu's default `makestep 1 3` only allows chrony to hard-step the clock
  # during its first 3 corrections after startup; afterwards it falls back to slow
  # slewing only. On a host under heavy CPU contention (many VMs/background jobs),
  # the guest clock can drift again well into the node's uptime, and slew alone
  # can't catch up (observed: chrony sources stuck ~400ms off an hour after boot),
  # causing containerd/kubelet timestamp issues (0/1 <invalid>, CreateContainerError)
  # long after the initial sync. The observed stuck offset (~400ms, persisting for
  # over an hour) was UNDER Ubuntu's default 1-second makestep threshold, so it was
  # never stepped at all — chrony was relying solely on its slow default slew rate
  # (~83ppm), which takes roughly (offset / slew_rate) ≈ 400ms / 0.0000833 ≈ 80
  # minutes to correct. `makestep 0.1 -1` steps immediately whenever the offset
  # exceeds 100ms, for the life of the daemon, so drift converges in seconds.
  # Edited before the restart below so chronyd is only bounced once.
  CHRONY_CONF="/etc/chrony/chrony.conf"
  if [ -f "${CHRONY_CONF}" ]; then
    sudo sed -i -E 's/^makestep\s+[0-9.]+\s+[0-9-]+/makestep 0.1 -1/' "${CHRONY_CONF}"
    grep -q "^makestep 0.1 -1" "${CHRONY_CONF}" || echo "makestep 0.1 -1" | sudo tee -a "${CHRONY_CONF}" >/dev/null
  fi

  echo "Enabling and starting ${CHRONY_SVC}..."
  sudo systemctl enable "${CHRONY_SVC}" || true
  sudo systemctl restart "${CHRONY_SVC}" || true
  sudo timedatectl set-ntp true || true
  sudo chronyc -a makestep || true

  # D-clock/D12: gate kubelet (via ordering, not a hard block) on chrony sync so the
  # clock is correct before static pods record their startTime. Without this, a
  # post-boot makestep() produces ghost containers with zero timestamps → 0/1
  # <invalid> on all static pods. Loosened from the original single-shot
  # `chronyc waitsync 30 0.01 1.0 0` (10ms tolerance failed reliably under host
  # load, and the unit's own hard failure only delayed the log, not kubelet, since
  # the drop-in uses Wants= not Requires=): poll for up to 2 minutes with a
  # relaxed 200ms tolerance, and — critically — ALWAYS exit 0 so the unit never
  # reports "failed"; the ordering (After=) still delays kubelet while polling.
  sudo tee /usr/local/bin/narwhal-clock-sync.sh >/dev/null <<'SCRIPT'
#!/bin/bash
# D12: best-effort clock-sync gate. Never fails — see 01-prerequisites.sh D12 note.
for _ in $(seq 1 24); do
  if chronyc waitsync 1 0.2 1.0 0 &>/dev/null; then
    echo "narwhal-clock-sync: synced within 200ms"
    exit 0
  fi
  sleep 5
done
echo "narwhal-clock-sync: WARN did not converge to 200ms within 2min, proceeding anyway"
exit 0
SCRIPT
  sudo chmod 0755 /usr/local/bin/narwhal-clock-sync.sh

  sudo tee /etc/systemd/system/narwhal-clock-sync.service >/dev/null <<'UNIT'
[Unit]
Description=Wait for chrony clock sync before kubelet (best-effort, never fails)
DefaultDependencies=no
After=chrony.service
Wants=chrony.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/narwhal-clock-sync.sh

[Install]
WantedBy=kubelet.service
WantedBy=containerd.service
UNIT
  # D14: containerd must ALSO be gated on clock sync, not just kubelet. Root cause
  # found live: on a cold VM boot the guest clock can start off by hours (observed
  # a -9h correction); 02-containerd.sh (which installs+starts containerd) runs
  # AFTER this script, so on a fresh install containerd itself is usually fine —
  # but on every REBOOT, containerd.service starts from systemd's normal boot
  # ordering, independent of this script, and can start creating/timestamping
  # container objects BEFORE chrony corrects a large drift. When chrony later
  # steps the clock backward, those already-created objects appear to have
  # CreatedAt timestamps in the "future" relative to the corrected clock, which
  # corrupts containerd's CRI store bookkeeping and produces the persistent
  # "failed to reserve container name" wedge (kubelet retries collide with the
  # stale future-timestamped reservation forever). Same drop-in for both services.
  for svc in kubelet containerd; do
    sudo mkdir -p "/etc/systemd/system/${svc}.service.d"
    sudo tee "/etc/systemd/system/${svc}.service.d/10-after-chrony.conf" >/dev/null <<'DROP'
[Unit]
After=narwhal-clock-sync.service
Wants=narwhal-clock-sync.service
DROP
  done
  sudo systemctl daemon-reload
  sudo systemctl enable narwhal-clock-sync.service || true
  echo "Clock-sync gate installed: narwhal-clock-sync.service blocks kubelet until synced"
else
  echo "WARN: chrony service not found after install; clock sync left to the hypervisor"
fi


# ──────────────────────────────────────────────
# Kernel prerequisites for kubeadm
#
# Nothing in this repo used to establish these — the pre-baked box already ships
# /etc/sysctl.d/k8s-network.conf and /etc/modules-load.d/k8s.conf, so the dependency
# was invisible until a plain cloud image was used. There kubeadm init dies at
# preflight with
#   [ERROR FileContent--proc-sys-net-ipv4-ip_forward]: contents are not set to 1
# Idempotent, so it is a no-op on the box.
#
# br_netfilter must be loaded BEFORE the bridge sysctls are applied: the
# net.bridge.* keys do not exist until the module is in, and sysctl silently skips
# missing keys.
# ──────────────────────────────────────────────
echo "Ensuring kubeadm kernel prerequisites (modules + sysctl)..."
sudo tee /etc/modules-load.d/k8s.conf >/dev/null <<'MODEOF'
overlay
br_netfilter
MODEOF
for mod in overlay br_netfilter; do
  sudo modprobe "${mod}" 2>/dev/null || echo "  WARN: modprobe ${mod} failed"
done

sudo tee /etc/sysctl.d/k8s-network.conf >/dev/null <<'SYSEOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
SYSEOF
sudo sysctl --system >/dev/null 2>&1 || true

echo "  ip_forward=$(cat /proc/sys/net/ipv4/ip_forward)" \
     "bridge-nf-call-iptables=$(cat /proc/sys/net/bridge/bridge-nf-call-iptables 2>/dev/null || echo NA)"

echo "=== Prerequisites Done ==="
