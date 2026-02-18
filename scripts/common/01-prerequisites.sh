#!/bin/bash
set -euo pipefail

echo "=== Prerequisites Installation ==="

CLUSTER_NAME="${CLUSTER_NAME:-narwhal}"
MASTER_COUNT="${MASTER_COUNT:-2}"
MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
WORKER_COUNT="${WORKER_COUNT:-2}"
WORKER_IP_BASE="${WORKER_IP_BASE:-192.168.56.2}"
NODE_IP="${NODE_IP:-}"

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
    # Look for interface with the IP already assigned
    PRIV_IFACE=$(ip -o addr show | grep "${NODE_IP}" | awk '{print $2}' | head -1)
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

# Add master-1 dnsmasq as primary DNS for *.local.narwhal.io resolution
# The ~local.narwhal.io routing domain ensures only matching queries go to dnsmasq
# On master-1, 08-dnsmasq.sh replaces systemd-resolved entirely, so this is a no-op
if systemctl is-active --quiet systemd-resolved; then
  sudo mkdir -p /etc/systemd/resolved.conf.d
  sudo tee /etc/systemd/resolved.conf.d/dns.conf << EOF
[Resolve]
DNS=${MASTER_IP_BASE}0 ${MASTER_IP_BASE}1 8.8.8.8 8.8.4.4
FallbackDNS=1.1.1.1
Domains=~local.narwhal.io
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


echo "=== Prerequisites Done ==="
