#!/bin/bash
set -euo pipefail

MASTER_IP="${MASTER_IP:-192.168.56.10}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"
SERVICE_CIDR="${SERVICE_CIDR:-10.96.0.0/12}"

# OIDC Configuration (Keycloak)
OIDC_ISSUER_URL="${OIDC_ISSUER_URL:-http://keycloak-service.keycloak.svc.cluster.local:8080/realms/kubernetes}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-kubernetes}"

echo "=== Kubernetes Cluster Initialization ==="
echo "Master IP: ${MASTER_IP}"
echo "OIDC Issuer: ${OIDC_ISSUER_URL}"

# Generate kubeadm config (use MASTER_IP for single-master setup)
# For multi-master HA, add VIP to certSANs and use kube-vip
cat <<EOF > /tmp/kubeadm-config.yaml
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: stable
controlPlaneEndpoint: "${MASTER_IP}:6443"
networking:
  podSubnet: "${POD_NETWORK_CIDR}"
  serviceSubnet: "${SERVICE_CIDR}"
apiServer:
  certSANs:
    - "${MASTER_IP}"
    - "narwhal-master"
    - "localhost"
    - "127.0.0.1"
# OIDC configuration requires HTTPS - configure after TLS setup
# extraArgs:
#   - name: oidc-issuer-url
#     value: "${OIDC_ISSUER_URL}"
#   - name: oidc-client-id
#     value: "${OIDC_CLIENT_ID}"
#   - name: oidc-username-claim
#     value: "preferred_username"
#   - name: oidc-username-prefix
#     value: "oidc:"
#   - name: oidc-groups-claim
#     value: "groups"
#   - name: oidc-groups-prefix
#     value: "oidc:"
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: "${MASTER_IP}"
  bindPort: 6443
nodeRegistration:
  criSocket: "unix:///run/containerd/containerd.sock"
---
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
cgroupDriver: systemd
EOF

# Initialize cluster (skip kube-proxy for Cilium replacement)
sudo kubeadm init --config=/tmp/kubeadm-config.yaml --skip-phases=addon/kube-proxy

# Configure kubeconfig for vagrant user
mkdir -p /home/vagrant/.kube
sudo cp -i /etc/kubernetes/admin.conf /home/vagrant/.kube/config
sudo chown vagrant:vagrant /home/vagrant/.kube/config

# Configure kubeconfig for root
sudo mkdir -p /root/.kube
sudo cp -i /etc/kubernetes/admin.conf /root/.kube/config

# Save join command for workers
kubeadm token create --print-join-command > /home/vagrant/join-command.sh
chmod +x /home/vagrant/join-command.sh

echo "=== Cluster Initialization Done ==="
