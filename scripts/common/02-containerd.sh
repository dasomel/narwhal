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

# Enable registry config_path so containerd reads per-registry certs.d entries.
# This is required for Harbor (harbor.local.narwhal.internal) to use the narwhal CA without
# global insecure_registries (which would bypass TLS entirely).
if grep -q 'config_path\s*=\s*""' /etc/containerd/config.toml; then
  sudo sed -i 's|config_path\s*=\s*""|config_path = "/etc/containerd/certs.d"|' /etc/containerd/config.toml
  echo "containerd: config_path set to /etc/containerd/certs.d"
elif ! grep -q 'config_path\s*=\s*"/etc/containerd/certs.d"' /etc/containerd/config.toml; then
  # config_path line is absent or has a different value — add it under [plugins."io.containerd.grpc.v1.cri".registry]
  sudo sed -i '/\[plugins\."io\.containerd\.grpc\.v1\.cri"\.registry\]/a\    config_path = "/etc/containerd/certs.d"' /etc/containerd/config.toml
  echo "containerd: inserted config_path under registry section"
else
  echo "containerd: config_path already set, skipping"
fi

# Create certs.d entry for harbor.${DOMAIN}.
# Internal cluster registry behind APISIX — skip TLS verify (no node CA trust
# needed; this is the verified pull path for harbor.${DOMAIN}).
DOMAIN="${DOMAIN:-local.narwhal.internal}"
HARBOR_CERTS_DIR="/etc/containerd/certs.d/harbor.${DOMAIN}"
sudo mkdir -p "${HARBOR_CERTS_DIR}"
if [ ! -f "${HARBOR_CERTS_DIR}/hosts.toml" ]; then
  sudo tee "${HARBOR_CERTS_DIR}/hosts.toml" > /dev/null <<HTOML
server = "https://harbor.${DOMAIN}"

[host."https://harbor.${DOMAIN}"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
HTOML
  echo "containerd: created ${HARBOR_CERTS_DIR}/hosts.toml"
else
  echo "containerd: ${HARBOR_CERTS_DIR}/hosts.toml already exists, skipping"
fi

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
