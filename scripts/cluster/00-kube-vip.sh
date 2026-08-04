#!/bin/bash
set -euo pipefail

KUBE_VIP_VERSION="${KUBE_VIP_VERSION:-v1.1.2}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
NODE_INDEX="${NODE_INDEX:-1}"

echo "=== kube-vip Installation (${KUBE_VIP_VERSION}) ==="
echo "VIP Address: ${VIP_ADDRESS}"
echo "Node Index: ${NODE_INDEX}"

# Cloud (Kakao) provides the control-plane VIP via a real Network LB, so kube-vip
# (L2/ARP-based, tied to the 192.168.56.x host-only network) is neither needed nor
# functional. Skip it entirely; VIP_ADDRESS points at the LB VIP instead.
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: skipping kube-vip (Kakao Network LB provides the control-plane VIP)"
  exit 0
fi

#=========================================
# Auto-detect network interface
#=========================================
echo "Detecting network interface for 192.168.56.x..."
VIP_INTERFACE=""
for i in {1..60}; do
  VIP_INTERFACE=$(ip -o addr show | grep "192\.168\.56\." | awk '{print $2}' | head -1)
  if [ -n "${VIP_INTERFACE}" ]; then
    echo "Found interface: ${VIP_INTERFACE}"
    break
  fi
  echo "Waiting for network interface... (${i}/60)"
  sleep 2
done

if [ -z "${VIP_INTERFACE}" ]; then
  echo "ERROR: Could not detect network interface for 192.168.56.x"
  exit 1
fi

#=========================================
# Pull kube-vip image
#=========================================
KUBE_VIP_IMAGE="ghcr.io/kube-vip/kube-vip:${KUBE_VIP_VERSION}"
echo "Pulling kube-vip image: ${KUBE_VIP_IMAGE}"
# --hosts-dir is not optional here. /etc/containerd/certs.d is read by containerd's CRI
# plugin, so kubelet and crictl pick up the airgap mirror automatically — but `ctr` is a
# direct client of the content store and resolves images itself, ignoring certs.d unless
# it is pointed at it. This is the one image pull in the provisioning path that does not
# go through CRI, so it was also the one that still reached the internet: on an isolated
# node it failed with `dial tcp 20.200.245.241:443: network is unreachable` while every
# other pull was already being served from the mirror.
sudo ctr image pull --hosts-dir /etc/containerd/certs.d "${KUBE_VIP_IMAGE}"

#=========================================
# Create static Pod manifest
#=========================================
sudo mkdir -p /etc/kubernetes/manifests

if [ "${NODE_INDEX}" -eq 1 ]; then
  #-----------------------------------------
  # First master: defer manifest creation to AFTER kubeadm init
  # kube-vip cannot run before API server exists (chicken-and-egg)
  # 02-init-cluster.sh will:
  #   1. Manually bind VIP to interface for kubeadm init
  #   2. Create full kube-vip manifest after init succeeds
  #-----------------------------------------
  echo "Bootstrap mode: deferring manifest to post-init"

  # Save config for 02-init-cluster.sh to create manifest later
  cat <<EOF | sudo tee /etc/kube-vip-bootstrap.env
KUBE_VIP_IMAGE=${KUBE_VIP_IMAGE}
VIP_INTERFACE=${VIP_INTERFACE}
VIP_ADDRESS=${VIP_ADDRESS}
EOF

else
  #-----------------------------------------
  # Joining masters: defer manifest to AFTER kubeadm join
  # admin.conf doesn't exist until join completes (chicken-and-egg)
  # 02-join-control-plane.sh will create the manifest after join
  #-----------------------------------------
  echo "Join mode: deferring manifest to post-join"

  # Save config for 02-join-control-plane.sh to create manifest later
  cat <<EOF | sudo tee /etc/kube-vip-bootstrap.env
KUBE_VIP_IMAGE=${KUBE_VIP_IMAGE}
VIP_INTERFACE=${VIP_INTERFACE}
VIP_ADDRESS=${VIP_ADDRESS}
EOF

fi

echo "=== kube-vip Installation Done ==="
echo "Bootstrap config saved to /etc/kube-vip-bootstrap.env"
echo "Manifest will be created after cluster init/join completes"
