#!/bin/bash
set -euo pipefail

VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
MASTER_COUNT="${MASTER_COUNT:-3}"
MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"
SERVICE_CIDR="${SERVICE_CIDR:-10.96.0.0/12}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

# Compute master-1's real IP
MASTER1_IP="${MASTER_IP_BASE}0"

# D-authmig: OIDC Configuration (Keycloak — migrated from Authentik)
# K8s 1.35+ requires HTTPS for --oidc-issuer-url; HTTP causes API server crash
OIDC_ISSUER_URL="${OIDC_ISSUER_URL:-https://keycloak.${DOMAIN}/realms/narwhal}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-kubernetes}"

echo "=== Kubernetes Cluster Initialization ==="
echo "VIP Address: ${VIP_ADDRESS}"
echo "Master-1 IP: ${MASTER1_IP}"
echo "Master Count: ${MASTER_COUNT}"
echo "OIDC Issuer: ${OIDC_ISSUER_URL}"

# Build certSANs dynamically
CERT_SANS="
    - \"${VIP_ADDRESS}\"
    - \"narwhal-vip\"
    - \"narwhal-master\"
    - \"localhost\"
    - \"127.0.0.1\""

for i in $(seq 1 "${MASTER_COUNT}"); do
  MASTER_IP="${MASTER_IP_BASE}$((i - 1))"
  MASTER_HOSTNAME="narwhal-master-${i}"
  CERT_SANS="${CERT_SANS}
    - \"${MASTER_IP}\"
    - \"${MASTER_HOSTNAME}\""
done

# Generate kubeadm config for HA setup
cat <<EOF > /tmp/kubeadm-config.yaml
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: stable
controlPlaneEndpoint: "${VIP_ADDRESS}:6443"
networking:
  podSubnet: "${POD_NETWORK_CIDR}"
  serviceSubnet: "${SERVICE_CIDR}"
apiServer:
  certSANs:${CERT_SANS}
# OIDC configuration - DISABLED at init time.
# K8s 1.35+ requires HTTPS for --oidc-issuer-url. Enable after cert-manager
# provisions TLS certificates for Keycloak (see 11-keycloak.sh).
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
controllerManager:
  extraArgs:
    # bind-address 0.0.0.0 so Prometheus (running off the control-plane node) can scrape
    # /metrics. kubeadm defaults these to 127.0.0.1, which makes them unreachable and fires
    # KubeControllerManagerInstanceUnreachable / KubeSchedulerInstanceUnreachable + TargetDown.
    - name: bind-address
      value: "0.0.0.0"
scheduler:
  extraArgs:
    - name: bind-address
      value: "0.0.0.0"
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: "${MASTER1_IP}"
  bindPort: 6443
nodeRegistration:
  criSocket: "unix:///run/containerd/containerd.sock"
---
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
cgroupDriver: systemd
EOF

VIP_INTERFACE=$(ip -o addr show | grep "192\.168\.56\." | awk '{print $2}' | head -1)

# D17: idempotency guard — up.sh's node-readiness loop can call `vagrant
# provision master-1` again while Cilium/CNI is still converging (kubectl
# reporting a node NotReady doesn't mean provisioning failed). `kubeadm init`
# is not idempotent and hard-fails preflight ("Port 6443 is in use", "/etc/
# kubernetes/manifests/*.yaml already exists", "/var/lib/etcd is not empty")
# on an already-initialized node. Skip straight to regenerating the join
# commands (safe/idempotent — new tokens, same cert-key) rather than the
# init phase, since a fresh reprovision may legitimately want a refreshed
# join-command.sh even though the cluster itself is already up.
if [[ -f /etc/kubernetes/admin.conf ]]; then
  echo "Cluster already initialized (admin.conf present) — skipping kubeadm init"
else
  # Temporarily bind VIP to the interface so kubeadm can use it as controlPlaneEndpoint
  # kube-vip will take over VIP management after init
  if ! ip addr show "${VIP_INTERFACE}" | grep -q "${VIP_ADDRESS}"; then
    echo "Adding VIP ${VIP_ADDRESS} to ${VIP_INTERFACE} for bootstrap..."
    sudo ip addr add "${VIP_ADDRESS}/32" dev "${VIP_INTERFACE}"
  fi

  # Initialize cluster (skip kube-proxy for Cilium replacement, upload certs for HA)
  sudo kubeadm init --config=/tmp/kubeadm-config.yaml --skip-phases=addon/kube-proxy --upload-certs
fi

# Configure kubeconfig for vagrant user
mkdir -p /home/vagrant/.kube
sudo cp -i /etc/kubernetes/admin.conf /home/vagrant/.kube/config
sudo chown vagrant:vagrant /home/vagrant/.kube/config

# Create local kubeconfig for provisioning (uses local IP, not VIP)
# When master-2 joins, kube-vip leader election can briefly disrupt the VIP.
# Provisioning scripts on master-1 must continue working during VIP transitions.
sudo cp /etc/kubernetes/admin.conf /home/vagrant/.kube/config-local
sudo sed -i "s|${VIP_ADDRESS}:6443|${MASTER1_IP}:6443|" /home/vagrant/.kube/config-local
sudo chown vagrant:vagrant /home/vagrant/.kube/config-local

# Configure kubeconfig for root
sudo mkdir -p /root/.kube
sudo cp -i /etc/kubernetes/admin.conf /root/.kube/config

# Remove control-plane NoSchedule taint so platform apps can run on master
# During initial provisioning, no workers exist yet. All pods must schedule on master.
# Workers will handle the workload once they join; master keeps the taint removed for dev env.
echo "Removing control-plane NoSchedule taint for dev environment..."
kubectl taint nodes --all node-role.kubernetes.io/control-plane- 2>/dev/null || true

# Create kube-vip manifest (deferred from 00-kube-vip.sh to avoid chicken-and-egg)
# Now that kubeadm init succeeded, API server is running and admin.conf exists
echo "Creating kube-vip manifest with full HA mode..."

# Read config saved by 00-kube-vip.sh
# shellcheck source=/dev/null
source /etc/kube-vip-bootstrap.env

# Create kube-vip kubeconfig that uses local master IP (not VIP)
# Avoids circular dependency: kube-vip needs API to bind VIP, but API uses VIP
sudo cp /etc/kubernetes/admin.conf /etc/kubernetes/kube-vip.conf
sudo sed -i "s|${VIP_ADDRESS}:6443|${MASTER1_IP}:6443|" /etc/kubernetes/kube-vip.conf

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

# Wait for kube-vip to start and take over VIP management.
# kube-vip briefly deletes the bootstrap VIP before re-adding it under its own
# management; on Ubuntu 26.04 (kernel 7.0) this transition can exceed a fixed sleep,
# leaving the VIP momentarily unreachable when the following kubeadm token commands
# (which talk to the VIP via admin.conf) run. Poll the VIP API until it is reachable.
echo "Waiting for kube-vip to take over VIP..."
for i in $(seq 1 30); do
  if curl -sk --max-time 3 "https://${VIP_ADDRESS}:6443/healthz" >/dev/null 2>&1; then
    echo "kube-vip VIP API reachable (attempt ${i}/30)"
    break
  fi
  echo "  VIP not ready yet, retrying... (${i}/30)"
  sleep 5
done

# Save join command for workers.
# Use the local-IP kubeconfig (not the VIP): on Ubuntu 26.04 the VIP path adds enough
# latency that kubeadm's client rate limiter hits a context deadline during the heavier
# upload-certs API calls. The local endpoint is fast and avoids the kube-vip round-trip.
LOCAL_KUBECONFIG=/home/vagrant/.kube/config-local
kubeadm token create --print-join-command --kubeconfig "${LOCAL_KUBECONFIG}" > /home/vagrant/join-command.sh
chmod +x /home/vagrant/join-command.sh

# Generate join command for additional control plane nodes
CERT_KEY=$(sudo kubeadm init phase upload-certs --upload-certs --kubeconfig "${LOCAL_KUBECONFIG}" | tail -1)
JOIN_CMD=$(kubeadm token create --print-join-command --kubeconfig "${LOCAL_KUBECONFIG}")
echo "${JOIN_CMD} --control-plane --certificate-key ${CERT_KEY} --ignore-preflight-errors=all" > /home/vagrant/join-control-plane.sh
chmod +x /home/vagrant/join-control-plane.sh

echo "=== Cluster Initialization Done ==="
echo "Worker join command: /home/vagrant/join-command.sh"
echo "Control plane join command: /home/vagrant/join-control-plane.sh"
