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
# On master-1, 10-dnsmasq.sh replaces systemd-resolved entirely, so this is a no-op
if systemctl is-active --quiet systemd-resolved; then
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
  echo "Enabling and starting ${CHRONY_SVC}..."
  sudo systemctl enable "${CHRONY_SVC}" || true
  sudo systemctl restart "${CHRONY_SVC}" || true
  sudo timedatectl set-ntp true || true
  sudo chronyc -a makestep || true
else
  echo "WARN: chrony service not found after install; clock sync left to the hypervisor"
fi


echo "=== Prerequisites Done ==="
