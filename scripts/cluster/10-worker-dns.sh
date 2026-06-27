#!/bin/bash
set -euo pipefail

#=========================================
# Worker Node DNS Resolver Configuration
#=========================================
# Configures systemd-resolved on worker nodes to forward *.local.narwhal.internal
# queries to the master-1 dnsmasq instance (192.168.56.10).
# Without this, workers resolve harbor.local.narwhal.internal via public upstream
# and image pulls fail with "tls: unrecognized name".
#
# Idempotent: re-running is safe (tee overwrites the drop-in).

MASTER_IP="${MASTER_IP:-192.168.56.10}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"
IFACE="${IFACE:-eth1}"

echo "=== Configuring worker DNS resolver for ${DOMAIN} ==="
echo "Master dnsmasq: ${MASTER_IP}"
echo "Interface: ${IFACE}"

# Drop-in: route *.local.narwhal.internal to master dnsmasq only (split-DNS).
# DNS=  points the interface-specific resolver at master-1.
# Domains=~  marks it as a routing-only domain (no search suffix).
sudo mkdir -p /etc/systemd/resolved.conf.d
sudo tee /etc/systemd/resolved.conf.d/narwhal-worker.conf << EOF
[Resolve]
DNS=${MASTER_IP}
Domains=~${DOMAIN}
EOF

sudo systemctl restart systemd-resolved

# Verify: the domain must resolve via the master dnsmasq.
echo ""
echo "=== Verifying DNS resolution ==="
if resolvectl query "argocd.${DOMAIN}" > /dev/null 2>&1; then
  echo "OK: argocd.${DOMAIN} resolved successfully"
else
  echo "WARN: argocd.${DOMAIN} not resolvable yet (dnsmasq may still be starting on master)"
fi

echo ""
echo "=== Worker DNS resolver configured ==="
echo "*.${DOMAIN} -> ${MASTER_IP} (via systemd-resolved drop-in)"
