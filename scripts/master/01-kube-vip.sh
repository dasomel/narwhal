#!/bin/bash
set -euo pipefail

# kube-vip is used for control plane HA with multiple masters.
# For single-master development setup, we skip kube-vip installation.
# The cluster will use MASTER_IP directly as the control plane endpoint.

echo "=== kube-vip Installation ==="
echo "Skipping kube-vip for single-master development setup."
echo "For multi-master HA, enable kube-vip after cluster initialization."
echo "=== kube-vip Installation Skipped ==="

# To enable kube-vip for multi-master HA, uncomment below after cluster init:
# VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
# KUBE_VIP_VERSION="${KUBE_VIP_VERSION:-v0.8.7}"
#
# # Wait for network interface
# for i in {1..30}; do
#   VIP_INTERFACE=$(ip -o addr show | grep "192.168.56" | awk '{print $2}' | head -1)
#   if [ -n "${VIP_INTERFACE}" ]; then break; fi
#   sleep 2
# done
# VIP_INTERFACE="${VIP_INTERFACE:-eth1}"
#
# sudo ctr image pull "ghcr.io/kube-vip/kube-vip:${KUBE_VIP_VERSION}"
#
# kubectl apply -f - << EOF
# apiVersion: v1
# kind: ServiceAccount
# metadata:
#   name: kube-vip
#   namespace: kube-system
# ---
# apiVersion: rbac.authorization.k8s.io/v1
# kind: ClusterRole
# metadata:
#   name: kube-vip
# rules:
#   - apiGroups: [""]
#     resources: ["nodes"]
#     verbs: ["get","list","watch"]
#   - apiGroups: ["coordination.k8s.io"]
#     resources: ["leases"]
#     verbs: ["get","create","update"]
# ---
# apiVersion: rbac.authorization.k8s.io/v1
# kind: ClusterRoleBinding
# metadata:
#   name: kube-vip
# roleRef:
#   apiGroup: rbac.authorization.k8s.io
#   kind: ClusterRole
#   name: kube-vip
# subjects:
#   - kind: ServiceAccount
#     name: kube-vip
#     namespace: kube-system
# EOF
