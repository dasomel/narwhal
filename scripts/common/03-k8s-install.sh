#!/bin/bash
set -euo pipefail

K8S_VERSION="${K8S_VERSION:-1.35}"
# The exact patch release to install. This used to be decorative: it appeared only in
# the banner below while apt installed whatever the v1.35 repo currently served, so on
# 2026-08-04 the banner announced 1.35.5 while every node came up on 1.35.7. Two things
# went wrong with that. A clean install today and one next month silently produce
# different clusters, which makes "clean install works" unfalsifiable; and
# scripts/airgap/01-generate-image-list.sh parses this same value out of the Vagrantfile
# to build the offline bundle, so the bundle was assembled for a version nothing runs.
K8S_PATCH_VERSION="${K8S_PATCH_VERSION:-1.35.7}"

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

# AIRGAP: the bundle already carries kubelet/kubeadm/kubectl and 01-prerequisites.sh has
# pointed APT at it, so adding pkgs.k8s.io here would both fail (no route) and undo that.
# The version resolution and pinned install below are unchanged — they read whatever APT
# is serving, which is exactly the point of doing the switch upstream of this script.
if [ "${AIRGAP:-0}" = "1" ]; then
  echo "AIRGAP=1: using the bundle's APT repo, not pkgs.k8s.io"
else

# Add K8s APT repository (pinned to v${K8S_VERSION} minor series)
sudo mkdir -p /etc/apt/keyrings
retry curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/Release.key" -o /tmp/k8s-release.key
sudo gpg --batch --yes --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg /tmp/k8s-release.key
rm -f /tmp/k8s-release.key

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${K8S_VERSION}/deb/ /" | \
  sudo tee /etc/apt/sources.list.d/kubernetes.list

fi  # end online-only APT source setup

# Install kubeadm, kubelet, kubectl (retried — pkgs.k8s.io CDN can be flaky mid-provision)
#
# Pinned to the exact patch release. The apt version string carries a packaging suffix
# (1.35.7-1.1), so match on the prefix rather than hard-coding it — the suffix changes
# without the Kubernetes version changing. `--allow-downgrades` matters on re-provision:
# without it, a node that already picked up a newer patch refuses to move back to the
# pinned one and the fleet ends up mixed.
retry sudo apt-get update
# No `exit` inside the awk and no `head` after it: either closes the pipe while
# apt-cache is still writing, apt-cache takes SIGPIPE, and `set -o pipefail` turns that
# into a script-killing failure before any of the error handling below can run. The
# first version of this line did exactly that and every node died here with no message
# at all — see the CLAUDE.md rule about pipefail and early-closing pipes.
K8S_APT_VERSION="$(apt-cache madison kubelet 2>/dev/null | awk -v v="${K8S_PATCH_VERSION}-" '$3 ~ "^"v && !seen {print $3; seen=1}')"
if [ -z "${K8S_APT_VERSION}" ]; then
  echo "ERROR: kubelet ${K8S_PATCH_VERSION} not offered by the v${K8S_VERSION} APT repo." >&2
  echo "       Available:" >&2
  apt-cache madison kubelet | awk '{print "         "$3}' | head -5 >&2
  echo "       Update K8S_PATCH_VERSION in Vagrantfile to one of the above." >&2
  exit 1
fi
echo "Pinning kubelet/kubeadm/kubectl to ${K8S_APT_VERSION}"
retry sudo apt-get install -y --allow-downgrades \
  "kubelet=${K8S_APT_VERSION}" "kubeadm=${K8S_APT_VERSION}" "kubectl=${K8S_APT_VERSION}"
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
