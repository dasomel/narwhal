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
# Vagrant fetches the join command node-to-node via the vagrant account.
# Cloud has no vagrant user/password; the operator pre-stages
# /tmp/join-control-plane.sh onto this node (copied off master-1 via the bastion).
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: expecting pre-staged /tmp/join-control-plane.sh (operator-supplied)"
else
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
fi

if [[ ! -f /tmp/join-control-plane.sh ]]; then
  echo "ERROR: Failed to get join command from master-1"
  exit 1
fi

#=========================================
# CIS 1.2.30 / 1.2.19-22: stage encryption + audit config BEFORE join.
# The apiserver manifest kubeadm renders from the shared ClusterConfiguration
# hostPath-mounts /etc/kubernetes/enc + /etc/kubernetes/audit; if the files are
# absent this node's apiserver crashloops after join. Fetch the SAME AES key
# that master-1 generated (encryption must be identical across all apiservers).
#=========================================
sudo mkdir -p /etc/kubernetes/enc /etc/kubernetes/audit /var/log/kubernetes/audit
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: expecting pre-staged /tmp/encryption-config.yaml (operator-supplied)"
  [[ -f /tmp/encryption-config.yaml ]] && sudo cp /tmp/encryption-config.yaml /etc/kubernetes/enc/encryption-config.yaml
else
  echo "Fetching encryption-config.yaml from master-1 (same AES key)..."
  for i in $(seq 1 $MAX_RETRIES); do
    if sshpass -p "vagrant" scp -o StrictHostKeyChecking=no \
      "vagrant@${MASTER1_IP}:/home/vagrant/encryption-config.yaml" /tmp/encryption-config.yaml 2>/dev/null; then
      sudo cp /tmp/encryption-config.yaml /etc/kubernetes/enc/encryption-config.yaml
      echo "encryption-config fetched"
      break
    fi
    echo "Waiting for master-1 encryption-config... (${i}/${MAX_RETRIES})"
    sleep $RETRY_INTERVAL
  done
fi
if [[ ! -f /etc/kubernetes/enc/encryption-config.yaml ]]; then
  echo "ERROR: encryption-config.yaml not available — apiserver would crashloop after join"
  exit 1
fi
sudo chmod 600 /etc/kubernetes/enc/encryption-config.yaml
cat <<'AUDEOF' | sudo tee /etc/kubernetes/audit/audit-policy.yaml >/dev/null
apiVersion: audit.k8s.io/v1
kind: Policy
omitStages: [RequestReceived]
rules:
  - level: None
    nonResourceURLs: ["/healthz*", "/livez*", "/readyz*", "/version", "/metrics", "/openapi*"]
  - level: Metadata
    resources:
      - group: ""
        resources: ["secrets", "configmaps", "serviceaccounts"]
      - group: "rbac.authorization.k8s.io"
        resources: ["roles", "rolebindings", "clusterroles", "clusterrolebindings"]
  - level: None
    verbs: ["get", "list", "watch"]
  - level: Metadata
AUDEOF

#=========================================
# Join control plane
#=========================================
# Detect local IP on the host-only network (192.168.56.x)
# Without this, kubeadm auto-detects the VMware NAT interface.
# Cloud: NODE_IP is supplied explicitly (the node's fixed 172.16.0.x address).
LOCAL_IP="${NODE_IP:-$(ip -o addr show | grep "192\.168\.56\." | awk '{print $4}' | cut -d/ -f1 | head -1)}"
echo "Local advertise address: ${LOCAL_IP}"

echo "Executing control plane join..."
# Append --apiserver-advertise-address to use correct network interface
sudo bash -c "$(cat /tmp/join-control-plane.sh) --apiserver-advertise-address=${LOCAL_IP}"

# Same heap bound master-1 applies in 02-init-cluster.sh: this node renders its apiserver
# from the shared ClusterConfiguration, which carries no memory request or ceiling.
# Absolute path, like every other cross-script reference here (see the lib.sh
# sources). Vagrant copies a provisioner script to /tmp/vagrant-shell and runs it
# from there, so BASH_SOURCE resolves to /tmp and the sibling is not beside it:
# clean installs died with "/tmp/patch-apiserver-memory.sh: No such file or
# directory" right after kubeadm init, leaving no kubeconfig and no join files.
/home/vagrant/scripts/cluster/patch-apiserver-memory.sh

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
# Cloud: skipped — the Kakao LB provides the control-plane VIP.
#=========================================
if [ "${PROVIDER:-vagrant}" != "kakao" ]; then
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
    # Keep in sync with the same block in 02-init-cluster.sh. Without resources kube-vip is
    # BestEffort and the kernel OOM-kills the holder of the control-plane VIP first.
    resources:
      requests:
        cpu: 25m
        memory: 64Mi
      limits:
        memory: 256Mi
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
fi  # end kube-vip block (Vagrant only)

echo "=== Control Plane Join Done ==="
kubectl get nodes
