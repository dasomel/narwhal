#!/bin/bash
set -euo pipefail

MASTER1_IP="${MASTER1_IP:-192.168.56.10}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"

echo "=== Joining Control Plane ==="
echo "Master-1 IP: ${MASTER1_IP}"
echo "VIP Address: ${VIP_ADDRESS}"

# Harden node file permissions (CIS 1.1.9, 1.1.12, 4.1.1, 4.1.7, 4.1.9).
# Non-fatal: failures must not block provisioning.
harden_joined_node() {
  # Cilium creates /etc/cni/net.d/*.conflist only after its agent pod starts on this node
  # (03-cni-install.sh runs on master-1 only). Wait boundedly so CIS 1.1.9 CNI file perms
  # are hardened on join; never fatal if Cilium is still converging.
  local cni_dir="${CNI_CONF_DIR:-/etc/cni/net.d}"
  local cni_wait_timeout="${CNI_WAIT_TIMEOUT:-180}"
  local cni_wait_interval="${CNI_WAIT_INTERVAL:-5}"
  case "${cni_wait_timeout}" in ''|*[!0-9]*) cni_wait_timeout=180 ;; esac
  case "${cni_wait_interval}" in ''|0|*[!0-9]*) cni_wait_interval=5 ;; esac
  local cni_elapsed=0
  local cni_ready=false
  while :; do
    if [ -d "${cni_dir}" ] && [ -n "$(find "${cni_dir}" -maxdepth 1 -type f -print -quit 2>/dev/null || true)" ]; then
      cni_ready=true
      break
    fi
    if [ "${cni_elapsed}" -ge "${cni_wait_timeout}" ]; then
      break
    fi
    sleep "${cni_wait_interval}"
    cni_elapsed=$((cni_elapsed + cni_wait_interval))
  done
  if [ "${cni_ready}" != true ]; then
    echo "WARN: no CNI config file appeared in ${cni_dir} within ${cni_wait_timeout}s; CNI file permissions (CIS 1.1.9) remain unhardened until re-run, continuing with remaining node file hardening..."
  fi

  local HARDEN_SCRIPT="${STAGE_DIR:-/home/vagrant}/scripts/cluster/harden-node-files.sh"
  if [ ! -f "${HARDEN_SCRIPT}" ]; then
    HARDEN_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harden-node-files.sh"
  fi
  if [ -f "${HARDEN_SCRIPT}" ]; then
    bash "${HARDEN_SCRIPT}" || echo "WARN: harden-node-files.sh reported issues, continuing..."
  fi
}

# D-HARBOR-NODE-CA (KISA-IMG-01): synchronize Harbor containerd CA trust for late-joining
# control-plane nodes via shared scripts/common/lib-harbor-ca.sh (Vagrant runs this script
# from /tmp/vagrant-shell, so resolve the guest /home/vagrant path first and fail loudly if missing).
HARBOR_CA_LIB="${STAGE_DIR:-/home/vagrant}/scripts/common/lib-harbor-ca.sh"
if [ ! -r "${HARBOR_CA_LIB}" ]; then
  HARBOR_CA_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../common" 2>/dev/null && pwd || true)/lib-harbor-ca.sh"
fi
if [ ! -r "${HARBOR_CA_LIB}" ]; then
  echo "ERROR: lib-harbor-ca.sh not readable at ${HARBOR_CA_LIB}" >&2
  exit 1
fi
# shellcheck source=scripts/common/lib-harbor-ca.sh
source "${HARBOR_CA_LIB}"

# D17: idempotency guard — see 02-join-worker.sh for the full rationale.
# `kubeadm join --control-plane` is not idempotent and hard-fails
# ("etcd data-dir not empty", ports in use, pki files already exist) on a
# node that already joined, which up.sh's node-readiness loop can trigger
# via a spurious `vagrant provision` while CNI is still converging.
if [[ -f /etc/kubernetes/kubelet.conf ]]; then
  echo "Node already joined (kubelet.conf present) — skipping kubeadm join"
  harden_joined_node
  sync_harbor_containerd_ca "${MASTER1_IP}"
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
sudo install -d -o root -g root -m 700 /etc/kubernetes/admission
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  [[ -f /tmp/admission-config.yaml ]] && sudo install -o root -g root -m 600 /tmp/admission-config.yaml /etc/kubernetes/admission/admission-config.yaml
else
  for i in $(seq 1 $MAX_RETRIES); do
    if (umask 077; sshpass -p "vagrant" scp -o StrictHostKeyChecking=no "vagrant@${MASTER1_IP}:/home/vagrant/admission-config.yaml" /tmp/admission-config.yaml 2>/dev/null); then
      sudo install -o root -g root -m 600 /tmp/admission-config.yaml /etc/kubernetes/admission/admission-config.yaml
      break
    fi
    sleep $RETRY_INTERVAL
  done
fi
[[ -f /etc/kubernetes/admission/admission-config.yaml ]] || { echo "ERROR: admission-config.yaml unavailable" >&2; exit 1; }
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: expecting pre-staged /tmp/encryption-config.yaml (operator-supplied)"
  [[ -f /tmp/encryption-config.yaml ]] && (umask 077; sudo install -o root -g root -m 600 /tmp/encryption-config.yaml /etc/kubernetes/enc/encryption-config.yaml)
else
  echo "Fetching encryption-config.yaml from master-1 (same AES key)..."
  for i in $(seq 1 $MAX_RETRIES); do
    if (umask 077; sshpass -p "vagrant" scp -o StrictHostKeyChecking=no \
      "vagrant@${MASTER1_IP}:/home/vagrant/encryption-config.yaml" /tmp/encryption-config.yaml 2>/dev/null); then
      (umask 077; sudo install -o root -g root -m 600 /tmp/encryption-config.yaml /etc/kubernetes/enc/encryption-config.yaml)
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
(
  umask 077
  sudo yq -r '.resources[] | select(.resources | contains(["secrets"])) | .providers[] | select(has("aescbc")) | .aescbc.keys[0].secret' \
    /etc/kubernetes/enc/encryption-config.yaml | sudo tee /etc/kubernetes/enc/encryption-key >/dev/null
)
sudo chown root:root /etc/kubernetes/enc/encryption-key
sudo chmod 600 /etc/kubernetes/enc/encryption-key
#=========================================
# D-KUBEADM-CONFIG-OIDC (#505): stage the OIDC CA BEFORE join.
# Once 11-4c put --oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt into the shared
# ClusterConfiguration, the apiserver kubeadm renders here refuses to start without that file
# (a missing CA file is a startup error), and kubeadm join copies only kubeadm's own certs.
# So: when the ClusterConfiguration references oidc-ca-file, fetch the CA from master-1 and
# refuse to join without it; before 11-4 ran the config does not reference it and nothing is
# needed. Cost: the join stops (exit 1, nothing joined) if master-1 is unreachable or the CA is
# unobtainable. Escape hatch: fix the cause and re-run; encryption/audit directories and the
# encryption config/key above are already installed and are re-runnable; join has not run.
# The flags themselves reach this node's manifest from the ClusterConfiguration.
# Kakao: no node-to-node SSH, so distribute-join-kakao.sh makes the same decision on the
# operator host and stages /tmp/oidc-ca.crt only when the config references it.
#=========================================
OIDC_CA_DEST="/etc/kubernetes/pki/oidc-ca.crt"
oidc_ca_required=false
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  [[ -f /tmp/oidc-ca.crt ]] && oidc_ca_required=true
else
  # D-JOIN-OIDC-CA-HASH (#522): this SSH channel is already the Vagrant trust root:
  # /home/vagrant/join-control-plane.sh (bootstrap token and --certificate-key) and
  # encryption config arrive via sshpass/StrictHostKeyChecking=no from master-1.
  # A MITM can redirect the whole join; a hash from that same channel adds no defence.
  # Cost: retain that existing trust boundary; a local kubeconfig requirement breaks
  # every initial join. The real fix is pinning master-1's SSH host key (out of scope).
  cluster_config=""
  for i in $(seq 1 $MAX_RETRIES); do
    if cluster_config="$(sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no "vagrant@${MASTER1_IP}" \
      "kubectl --kubeconfig=/home/vagrant/.kube/config-local -n kube-system get cm kubeadm-config -o jsonpath='{.data.ClusterConfiguration}'" 2>/dev/null)" \
      && [ -n "${cluster_config}" ]; then
      break
    fi
    cluster_config=""
    echo "Waiting for master-1 kubeadm-config... (${i}/${MAX_RETRIES})"
    sleep $RETRY_INTERVAL
  done
  if [ -z "${cluster_config}" ]; then
    echo "ERROR: cannot read kube-system/kubeadm-config from master-1; cannot tell whether the apiserver needs the OIDC CA — not joining" >&2
    exit 1
  fi
  if grep -q 'oidc-ca-file' <<<"${cluster_config}"; then
    oidc_ca_required=true
    echo "Fetching oidc-ca.crt from master-1 (kubeadm-config references oidc-ca-file)..."
    # Read via sudo cat on master-1 because CIS 1.1.20 (D-NODE-PKI-PERMS) tightens
    # /etc/kubernetes/pki/*.crt to 0600 root:root (matching distribute-join-kakao.sh).
    sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no "vagrant@${MASTER1_IP}" \
      "sudo cat /etc/kubernetes/pki/oidc-ca.crt" > /tmp/oidc-ca.crt 2>/dev/null || true
  fi
fi
if [ "${oidc_ca_required}" = "true" ]; then
  if [[ ! -s /tmp/oidc-ca.crt ]] || ! openssl x509 -in /tmp/oidc-ca.crt -noout -subject >/dev/null 2>&1; then
    echo "ERROR: kubeadm-config references oidc-ca-file but a valid /tmp/oidc-ca.crt could not be obtained — this node's apiserver would crashloop after join. Not joining" >&2
    exit 1
  fi
  sudo mkdir -p /etc/kubernetes/pki
  sudo install -o root -g root -m 0600 /tmp/oidc-ca.crt "${OIDC_CA_DEST}"
  echo "OIDC CA installed at ${OIDC_CA_DEST}"
fi

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
# `|| true` is load-bearing: under pipefail a non-matching grep aborts the script, and on
# a cloud node there is no 192.168.56.x address to match. That killed the join here with
# no message at all — the log simply stopped after the previous echo, which reads as a
# hang rather than a failure. Fail on an empty result instead, and say why.
LOCAL_IP="${NODE_IP:-$(ip -o addr show | grep "192\.168\.56\." | awk '{print $4}' | cut -d/ -f1 | head -1 || true)}"
if [ -z "${LOCAL_IP}" ]; then
  echo "ERROR: could not determine this node's advertise address." >&2
  echo "       Set NODE_IP explicitly — there is no 192.168.56.x interface to fall back to." >&2
  ip -o addr show >&2
  exit 1
fi
echo "Local advertise address: ${LOCAL_IP}"

#=========================================
# D-CP-STATIC-POD-SC / D-CP-ETCD-DEFER + D-KUBELET-KERNEL-DEFAULTS: stage kubeadm strategic patches
# (kube-apiserver, kube-controller-manager, kube-scheduler; etcd untouched) and enforce CIS 4.2.6
# kernel sysctls BEFORE kubeadm join starts kubelet and renders static Pods.
#=========================================
KUBEADM_PATCH_SRC="${STAGE_DIR:-/home/vagrant}/scripts/ops/lib/kubeadm-patches"
if [[ ! -d "${KUBEADM_PATCH_SRC}" ]]; then
  KUBEADM_PATCH_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../ops/lib/kubeadm-patches" 2>/dev/null && pwd || true)"
fi
sudo install -d -o root -g root -m 700 /etc/kubernetes/patches
for patch_file in \
  kube-apiserver+strategic.yaml \
  kube-controller-manager+strategic.yaml \
  kube-scheduler+strategic.yaml; do
  if [[ ! -r "${KUBEADM_PATCH_SRC}/${patch_file}" ]]; then
    echo "ERROR: missing kubeadm strategic patch: ${KUBEADM_PATCH_SRC}/${patch_file}" >&2
    exit 1
  fi
  sudo install -o root -g root -m 600 "${KUBEADM_PATCH_SRC}/${patch_file}" "/etc/kubernetes/patches/${patch_file}"
done

KUBELET_KERNEL_LIB="${STAGE_DIR:-/home/vagrant}/scripts/common/lib-kubelet-kernel-defaults.sh"
if [[ ! -r "${KUBELET_KERNEL_LIB}" ]]; then
  KUBELET_KERNEL_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../common" 2>/dev/null && pwd || true)/lib-kubelet-kernel-defaults.sh"
fi
if [[ ! -r "${KUBELET_KERNEL_LIB}" ]]; then
  echo "ERROR: lib-kubelet-kernel-defaults.sh not readable at ${KUBELET_KERNEL_LIB}" >&2
  exit 1
fi
# shellcheck source=scripts/common/lib-kubelet-kernel-defaults.sh
source "${KUBELET_KERNEL_LIB}"
ensure_kubelet_kernel_defaults

echo "Executing control plane join..."
# Append --apiserver-advertise-address to use correct network interface and --patches for
# control-plane static Pod securityContext hardening (D-CP-STATIC-POD-SC).
# LOCAL_IP is interpolated into a root shell command: accept only a dotted IPv4 address.
if [[ ! "${LOCAL_IP}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  echo "ERROR: refusing non-IPv4 LOCAL_IP for kubeadm join: ${LOCAL_IP}" >&2
  exit 1
fi
sudo bash -c "$(cat /tmp/join-control-plane.sh) --apiserver-advertise-address=${LOCAL_IP} --patches=/etc/kubernetes/patches"

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
# -f, not -i: these scripts run with no tty, so `cp -i` on an existing kubeconfig
# asks a question nobody can answer and the script dies. Both of these are
# re-runnable by design (the init guard above skips kubeadm and falls straight
# through to here), so overwriting from admin.conf is exactly what is wanted.
sudo cp -f /etc/kubernetes/admin.conf /home/vagrant/.kube/config
sudo chown vagrant:vagrant /home/vagrant/.kube/config

# Configure kubeconfig for root
sudo mkdir -p /root/.kube
sudo cp -f /etc/kubernetes/admin.conf /root/.kube/config

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
  securityContext:
    seccompProfile:
      type: RuntimeDefault
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
    # D-PSA-BATCH-C: keep in sync with the same block in 02-init-cluster.sh (R411).
    securityContext:
      allowPrivilegeEscalation: false
      # D1 (KSV021): UID 0 retains owner-read access to the 0600 kubeconfig.
      runAsUser: 0
      runAsGroup: 10100
      capabilities:
        drop:
        - ALL
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

# Harden node file permissions after join (re-uses harden_joined_node above).
harden_joined_node
sync_harbor_containerd_ca "${MASTER1_IP}"

echo "=== Control Plane Join Done ==="
# Informational only, so it must not decide the exit status — the already-joined branch
# above gets this right with `|| true` and this one did not. It reported failure on a node
# that had genuinely joined, patched its apiserver and written its kubeconfig.
#
# Explicit --kubeconfig rather than the inherited KUBECONFIG: the driver exports
# ~/.kube/config-local, which only master-1 has, so kubectl here fell back to
# localhost:8080 and "connection refused" looked like a broken control plane.
kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes || true
