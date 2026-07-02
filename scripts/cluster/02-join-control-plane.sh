#!/bin/bash
set -euo pipefail

MASTER1_IP="${MASTER1_IP:-192.168.56.10}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"

echo "=== Joining Control Plane ==="
echo "Master-1 IP: ${MASTER1_IP}"
echo "VIP Address: ${VIP_ADDRESS}"

# D17: idempotency guard — see 02-join-worker.sh for the full rationale.
# `kubeadm join --control-plane` is not idempotent and hard-fails
# ("etcd data-dir not empty", ports in use, pki files already exist) on a
# node that already joined, which up.sh's node-readiness loop can trigger
# via a spurious `vagrant provision` while CNI is still converging.
if [[ -f /etc/kubernetes/kubelet.conf ]]; then
  echo "Node already joined (kubelet.conf present) — skipping kubeadm join"
  echo "=== Control Plane Join Done (already joined) ==="
  kubectl get nodes 2>/dev/null || true
  exit 0
fi

MAX_RETRIES=60
RETRY_INTERVAL=10

#=========================================
# Fetch join command from master-1
#=========================================
echo "Fetching control plane join command from master-1..."
for i in $(seq 1 $MAX_RETRIES); do
  if sshpass -p "vagrant" scp -o StrictHostKeyChecking=no \
    "vagrant@${MASTER1_IP}:/home/vagrant/join-control-plane.sh" /tmp/join-control-plane.sh 2>/dev/null; then
    echo "Join command fetched successfully"
    break
  fi
  echo "Waiting for master-1 to be ready... (${i}/${MAX_RETRIES})"
  sleep $RETRY_INTERVAL
done

if [[ ! -f /tmp/join-control-plane.sh ]]; then
  echo "ERROR: Failed to get join command from master-1"
  exit 1
fi

#=========================================
# Join control plane
#=========================================
# Detect local IP on the host-only network (192.168.56.x)
# Without this, kubeadm auto-detects the VMware NAT interface
LOCAL_IP=$(ip -o addr show | grep "192\.168\.56\." | awk '{print $4}' | cut -d/ -f1 | head -1)
echo "Local advertise address: ${LOCAL_IP}"

echo "Executing control plane join..."
# Append --apiserver-advertise-address to use correct network interface
sudo bash -c "$(cat /tmp/join-control-plane.sh) --apiserver-advertise-address=${LOCAL_IP}"

#=========================================
# Configure kubeconfig for vagrant user
#=========================================
echo "Configuring kubeconfig..."
mkdir -p /home/vagrant/.kube
sudo cp -i /etc/kubernetes/admin.conf /home/vagrant/.kube/config
sudo chown vagrant:vagrant /home/vagrant/.kube/config

# Configure kubeconfig for root
sudo mkdir -p /root/.kube
sudo cp -i /etc/kubernetes/admin.conf /root/.kube/config

#=========================================
# Create kube-vip manifest (deferred from 00-kube-vip.sh)
# admin.conf now exists after successful join
#=========================================
echo "Creating kube-vip manifest..."

# Read config saved by 00-kube-vip.sh
# shellcheck source=/dev/null
source /etc/kube-vip-bootstrap.env

# Create kube-vip kubeconfig that uses local IP (not VIP)
# Avoids circular dependency: kube-vip needs API to participate in leader election
sudo cp /etc/kubernetes/admin.conf /etc/kubernetes/kube-vip.conf
sudo sed -i "s|${VIP_ADDRESS}:6443|${LOCAL_IP}:6443|" /etc/kubernetes/kube-vip.conf

cat <<EOF | sudo tee /etc/kubernetes/manifests/kube-vip.yaml
apiVersion: v1
kind: Pod
metadata:
  name: kube-vip
  namespace: kube-system
  labels:
    k8s-app: kube-vip
spec:
  hostNetwork: true
  containers:
  - name: kube-vip
    image: ${KUBE_VIP_IMAGE}
    imagePullPolicy: IfNotPresent
    args:
    - manager
    env:
    - name: vip_arp
      value: "true"
    - name: port
      value: "6443"
    - name: vip_interface
      value: "${VIP_INTERFACE}"
    - name: vip_subnet
      value: "32"
    - name: cp_enable
      value: "true"
    - name: cp_namespace
      value: kube-system
    - name: vip_ddns
      value: "false"
    - name: svc_enable
      value: "false"
    - name: svc_leasename
      value: plndr-svcs-lock
    - name: vip_leaderelectaliveduration
      value: "30"
    - name: vip_leaderelectrenewduration
      value: "20"
    - name: vip_leaderelectretryduration
      value: "5"
    - name: vip_leasename
      value: plndr-cp-lock
    - name: vip_leaderelection
      value: "true"
    - name: address
      value: "${VIP_ADDRESS}"
    - name: prometheus_server
      value: :2112
    securityContext:
      capabilities:
        add:
        - NET_ADMIN
        - NET_RAW
    volumeMounts:
    - name: kubeconfig
      mountPath: /.kube/config
      readOnly: true
  volumes:
  - name: kubeconfig
    hostPath:
      path: /etc/kubernetes/kube-vip.conf
      type: File
EOF

echo "Waiting for kube-vip to start..."
sleep 10

echo "=== Control Plane Join Done ==="
kubectl get nodes
