#!/bin/bash
set -euo pipefail

CNI_PLUGIN="${CNI_PLUGIN:-cilium}"
CILIUM_VERSION="${CILIUM_VERSION:-1.19.0}"
CILIUM_CLI_VERSION="${CILIUM_CLI_VERSION:-v0.19.0}"
CALICO_VERSION="${CALICO_VERSION:-v3.31.3}"

# Cilium kube-proxy replacement (use MASTER_IP for single-master setup)
K8S_API_SERVER="${MASTER_IP:-192.168.56.10}"

echo "=== CNI Plugin Installation: ${CNI_PLUGIN} ==="

export KUBECONFIG=/home/vagrant/.kube/config

case "${CNI_PLUGIN}" in
  cilium)
    # Install Helm
    HELM_VERSION="v4.1.0"
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | DESIRED_VERSION="${HELM_VERSION}" bash

    # Install Cilium CLI
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi
    if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi

    curl -L --fail --remote-name-all \
      "https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${ARCH}.tar.gz"
    sudo tar xzvfC "cilium-linux-${ARCH}.tar.gz" /usr/local/bin
    rm "cilium-linux-${ARCH}.tar.gz"

    # Install Gateway API CRDs (required for Cilium Gateway API)
    echo "Installing Gateway API CRDs..."
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_gatewayclasses.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_gateways.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_httproutes.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_referencegrants.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_grpcroutes.yaml

    # Install Cilium with kube-proxy replacement, Hubble, and Gateway API
    echo "Installing Cilium ${CILIUM_VERSION} with Hubble, kube-proxy replacement, and Gateway API..."
    cilium install --version "${CILIUM_VERSION}" \
      --set kubeProxyReplacement=true \
      --set k8sServiceHost="${K8S_API_SERVER}" \
      --set k8sServicePort=6443 \
      --set hubble.relay.enabled=true \
      --set hubble.ui.enabled=true \
      --set gatewayAPI.enabled=true

    cilium status --wait

    # Install Hubble CLI
    HUBBLE_CLI_VERSION="${HUBBLE_CLI_VERSION:-v1.18.5}"
    curl -L --fail --remote-name-all \
      "https://github.com/cilium/hubble/releases/download/${HUBBLE_CLI_VERSION}/hubble-linux-${ARCH}.tar.gz"
    sudo tar xzvfC "hubble-linux-${ARCH}.tar.gz" /usr/local/bin
    rm "hubble-linux-${ARCH}.tar.gz"

    echo "Hubble CLI installed: ${HUBBLE_CLI_VERSION}"
    ;;

  calico)
    echo "Installing Calico ${CALICO_VERSION}..."
    kubectl apply -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/calico.yaml"
    ;;

  flannel)
    echo "Installing Flannel..."
    kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
    ;;

  *)
    echo "Unknown CNI plugin: ${CNI_PLUGIN}"
    exit 1
    ;;
esac

# Wait for nodes to be ready
echo "Waiting for node to be ready..."
kubectl wait --for=condition=Ready node --all --timeout=300s

echo "=== CNI Plugin Installation Done ==="
