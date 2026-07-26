#!/bin/bash
set -euo pipefail

# Bound kube-apiserver memory on this control-plane node.
#
# kubeadm renders the apiserver static pod with `requests.cpu: 250m` and nothing else, so
# the process has no memory request and no ceiling of any kind. Go only collects when the
# heap roughly doubles, which on a node with no limit means the apiserver grows until the
# node runs out: measured 2.7-4.1Gi RSS on 6GB masters carrying 167 CRDs. Everything else
# on the node is BestEffort, so the kernel culls kube-vip / metallb / cilium-operator long
# before it touches the apiserver, and the real cause stays invisible.
#
# GOMEMLIMIT is a SOFT limit: it makes Go collect harder as the heap approaches the value
# and never kills the process. A container memory limit would do the opposite - OOM-kill an
# apiserver and take a third of the control plane with it - so this is deliberately not a
# `limits.memory`. Measured effect: 2856Mi -> 914Mi immediately after restart, settling
# around 1.9Gi in steady state.
#
# requests.memory exists for the scheduler, not the kernel. Without it the node advertises
# ~14% memory requested while actually sitting at 89%, so the scheduler keeps assigning
# pods to a saturated master.
#
# Idempotent: safe to re-run, and a no-op once the env var is present.

MANIFEST="${MANIFEST:-/etc/kubernetes/manifests/kube-apiserver.yaml}"
GOMEMLIMIT_VALUE="${GOMEMLIMIT_VALUE:-2GiB}"
MEMORY_REQUEST="${MEMORY_REQUEST:-1Gi}"

if [ ! -f "${MANIFEST}" ]; then
  echo "ERROR: ${MANIFEST} not found — is this a control-plane node?" >&2
  exit 1
fi

if sudo yq '.spec.containers[] | select(.name=="kube-apiserver") | .env[]? | select(.name=="GOMEMLIMIT")' \
     "${MANIFEST}" 2>/dev/null | grep -q GOMEMLIMIT; then
  echo "kube-apiserver already has GOMEMLIMIT — nothing to do"
  exit 0
fi

echo "Bounding kube-apiserver memory (GOMEMLIMIT=${GOMEMLIMIT_VALUE}, requests.memory=${MEMORY_REQUEST})..."

# Keep one backup of the kubeadm-rendered original; --update=none so re-runs never clobber it.
sudo cp --update=none "${MANIFEST}" /root/kube-apiserver.yaml.bak

WORK=$(mktemp)
sudo cp "${MANIFEST}" "${WORK}"
sudo chown "$(id -u):$(id -g)" "${WORK}"

yq -i "(.spec.containers[] | select(.name==\"kube-apiserver\")).env += [{\"name\":\"GOMEMLIMIT\",\"value\":\"${GOMEMLIMIT_VALUE}\"}]" "${WORK}"
yq -i "(.spec.containers[] | select(.name==\"kube-apiserver\")).resources.requests.memory = \"${MEMORY_REQUEST}\"" "${WORK}"

# Sanity-check before it goes anywhere near the manifests directory: a malformed static pod
# manifest is silently ignored by kubelet, which would leave this node with no apiserver.
yq -e '.spec.containers[] | select(.name=="kube-apiserver") | .env[] | select(.name=="GOMEMLIMIT")' \
  "${WORK}" >/dev/null

# Stage inside the manifests directory and rename, so the move is atomic on the same
# filesystem. Copying straight over the live file lets kubelet observe a half-written pod.
STAGE="$(dirname "${MANIFEST}")/.kube-apiserver.yaml.tmp"
sudo cp "${WORK}" "${STAGE}"
sudo chown root:root "${STAGE}"
sudo chmod 600 "${STAGE}"
sudo mv "${STAGE}" "${MANIFEST}"
rm -f "${WORK}"

echo "Patched. kubelet will restart kube-apiserver on this node shortly."
