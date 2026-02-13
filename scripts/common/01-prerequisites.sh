#!/bin/bash
set -euo pipefail

echo "=== Prerequisites Installation ==="

CLUSTER_NAME="${CLUSTER_NAME:-narwhal}"
MASTER_IP="${MASTER_IP:-192.168.56.10}"
WORKER_COUNT="${WORKER_COUNT:-2}"
WORKER_IP_BASE="${WORKER_IP_BASE:-192.168.56.2}"

#=========================================
# Configure DNS (fix VMware DNS issues)
#=========================================
echo "Configuring DNS..."

# Add Google DNS as fallback for VMware NAT DNS issues
if systemctl is-active --quiet systemd-resolved; then
  sudo mkdir -p /etc/systemd/resolved.conf.d
  sudo tee /etc/systemd/resolved.conf.d/dns.conf << 'EOF'
[Resolve]
DNS=8.8.8.8 8.8.4.4
FallbackDNS=1.1.1.1
EOF
  sudo systemctl restart systemd-resolved
fi

# Verify DNS is working
echo "Testing DNS resolution..."
nslookup registry.k8s.io || echo "DNS test warning, continuing..."

# Configure /etc/hosts
cat <<EOF | sudo tee -a /etc/hosts

# Kubernetes Cluster
${MASTER_IP}   ${CLUSTER_NAME}-master
EOF

for i in $(seq 1 "$WORKER_COUNT"); do
  echo "${WORKER_IP_BASE}${i}   ${CLUSTER_NAME}-worker-${i}" | sudo tee -a /etc/hosts
done

# Install yq (not included in base box)
YQ_VERSION="v4.50.1"
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi
if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi
sudo wget -qO /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${ARCH}"
sudo chmod +x /usr/local/bin/yq

echo "=== Prerequisites Done ==="
