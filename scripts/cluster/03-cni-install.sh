#!/bin/bash
set -euo pipefail

CNI_PLUGIN="${CNI_PLUGIN:-cilium}"
CILIUM_VERSION="${CILIUM_VERSION:-1.19.0}"
CILIUM_CLI_VERSION="${CILIUM_CLI_VERSION:-v0.19.0}"
CALICO_VERSION="${CALICO_VERSION:-v3.31.3}"

# Cilium kube-proxy replacement (VIP for HA control plane)
K8S_API_SERVER="${MASTER_IP:-192.168.56.100}"

echo "=== CNI Plugin Installation: ${CNI_PLUGIN} ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

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

    # Install Gateway API CRDs (used by Traefik Gateway Controller, Cilium provides network-level support)
    echo "Installing Gateway API CRDs..."
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_gatewayclasses.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_gateways.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_httproutes.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_referencegrants.yaml
    kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.1/config/crd/standard/gateway.networking.k8s.io_grpcroutes.yaml

    # Install Cilium with kube-proxy replacement, Hubble, and Gateway API CRD awareness
    # Note: Traefik is the actual Gateway Controller (GatewayClass: traefik)
    # Cilium's gatewayAPI.enabled allows network-level integration with Gateway API resources
    echo "Installing Cilium ${CILIUM_VERSION} with Hubble and kube-proxy replacement..."
    cilium install --version "${CILIUM_VERSION}" \
      --set kubeProxyReplacement=true \
      --set k8sServiceHost="${K8S_API_SERVER}" \
      --set k8sServicePort=6443 \
      --set hubble.relay.enabled=true \
      --set hubble.ui.enabled=true \
      --set gatewayAPI.enabled=true \
      --set cni.exclusive=false \
      --set socketLB.hostNamespaceOnly=true

    # Wait for core Cilium components (not hubble - it needs worker nodes)
    cilium status --wait --wait-duration 120s || echo "WARN: cilium status timed out (hubble may need worker nodes)"

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
