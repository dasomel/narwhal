#!/bin/bash
set -euo pipefail

CONTAINERD_VERSION="${CONTAINERD_VERSION:-1.7.*}"

echo "=== containerd ${CONTAINERD_VERSION} Installation ==="

# Install containerd (apt-get update required for fresh box)
# The kube-ready-box already ships containerd; pin to the requested series when the
# distro repo offers it (Ubuntu 24.04 -> 1.7.x), otherwise fall back to the repo
# default (Ubuntu 26.04 ships containerd 2.x). K8s 1.35 supports containerd 1.7+ and 2.x.
sudo apt-get update
if ! sudo apt-get install -y containerd="${CONTAINERD_VERSION}"; then
  echo "containerd=${CONTAINERD_VERSION} not available in repo; installing distro default containerd..."
  sudo apt-get install -y containerd
fi

# Configure containerd
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null

# Enable SystemdCgroup (required for K8s)
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

# Update sandbox_image
sudo sed -i 's|sandbox_image = ".*"|sandbox_image = "registry.k8s.io/pause:3.10"|' /etc/containerd/config.toml

# Configure service limits
sudo mkdir -p /etc/systemd/system/containerd.service.d
cat <<EOF | sudo tee /etc/systemd/system/containerd.service.d/limits.conf
[Service]
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Restart=always
RestartSec=5
EOF

# Restart service
sudo systemctl daemon-reload
sudo systemctl restart containerd
sudo systemctl enable containerd

echo "=== containerd Done ==="
