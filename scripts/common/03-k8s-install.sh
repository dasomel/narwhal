#!/bin/bash
set -euo pipefail

K8S_VERSION="${K8S_VERSION:-1.35}"
# Patch version resolved by APT repo (v1.35 repo provides 1.35.1+)
K8S_PATCH_VERSION="${K8S_PATCH_VERSION:-1.35.1}"

echo "=== Kubernetes v${K8S_PATCH_VERSION} Installation ==="

# Retry wrapper for transient mirror/CDN failures. A clean install once aborted because a
# worker briefly could not reach prod-cdn.packages.k8s.io while fetching kubelet/kubeadm,
# which left the node unjoined and skipped phase2 entirely.
retry() {
  local n=1 max=5
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after ${max} attempts: $*" >&2
      return 1
    fi
    echo "  attempt ${n}/${max} failed, retrying in 15s..." >&2
    n=$((n + 1))
    sleep 15
  done
}

# Add K8s APT repository (pinned to v${K8S_VERSION} minor series)
sudo mkdir -p /etc/apt/keyrings
retry curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/Release.key" -o /tmp/k8s-release.key
sudo gpg --batch --yes --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg /tmp/k8s-release.key
rm -f /tmp/k8s-release.key

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/ /" | \
  sudo tee /etc/apt/sources.list.d/kubernetes.list

# Install kubeadm, kubelet, kubectl (retried — pkgs.k8s.io CDN can be flaky mid-provision)
# Version pinned via APT repo (v1.35 repo only provides 1.35.x packages)
retry sudo apt-get update
retry sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl

# Enable kubelet
sudo systemctl enable kubelet

# Configure crictl
cat <<EOF | sudo tee /etc/crictl.yaml
runtime-endpoint: unix:///run/containerd/containerd.sock
image-endpoint: unix:///run/containerd/containerd.sock
timeout: 10
debug: false
EOF

# kubectl completion
cat <<'EOF' >> /home/vagrant/.bashrc

# kubectl
source <(kubectl completion bash)
alias k=kubectl
complete -o default -F __start_kubectl k
EOF

echo "=== Kubernetes Installation Done ==="
