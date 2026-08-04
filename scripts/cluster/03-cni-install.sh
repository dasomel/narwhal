#!/bin/bash
set -euo pipefail

CNI_PLUGIN="${CNI_PLUGIN:-cilium}"
CILIUM_VERSION="${CILIUM_VERSION:-1.19.4}"
CILIUM_CLI_VERSION="${CILIUM_CLI_VERSION:-v0.19.4}"
CALICO_VERSION="${CALICO_VERSION:-v3.31.3}"

# Cilium kube-proxy replacement (VIP for HA control plane)
K8S_API_SERVER="${MASTER_IP:-192.168.56.100}"

# Same shape as 03-k8s-install.sh's retry(). Every download below crosses the bastion
# proxy to a public CDN, and those fail transiently: a clean run died here on
# `curl: (35) SSL_ERROR_SYSCALL` fetching helm, and probing the same URL five times
# immediately afterwards gave 1 failure and 4 successes. One flaky fetch was taking the
# whole init stage — and with it kubeadm's work — down with it.
retry() {
  local n=1 max=5
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after ${max} attempts: $*" >&2
      return 1
    fi
    echo "  attempt ${n}/${max} failed, retrying in 15s..." >&2
    n=$((n + 1))
    sleep 15
  done
}

echo "=== CNI Plugin Installation: ${CNI_PLUGIN} ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

case "${CNI_PLUGIN}" in
  cilium)
    # Install Helm
    HELM_VERSION="v4.2.1"
    install_helm() {
      curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
        | DESIRED_VERSION="${HELM_VERSION}" bash
    }
    retry install_helm

    # Install Cilium CLI
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi
    if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi

    retry curl -L --fail --remote-name-all \
      "https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${ARCH}.tar.gz"
    sudo tar xzvfC "cilium-linux-${ARCH}.tar.gz" /usr/local/bin
    rm "cilium-linux-${ARCH}.tar.gz"

    # Install Gateway API CRDs (used by APISIX Gateway Controller, Cilium provides network-level support)
    echo "Installing Gateway API CRDs..."
    retry kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.5.1/config/crd/standard/gateway.networking.k8s.io_gatewayclasses.yaml
    retry kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.5.1/config/crd/standard/gateway.networking.k8s.io_gateways.yaml
    retry kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.5.1/config/crd/standard/gateway.networking.k8s.io_httproutes.yaml
    retry kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.5.1/config/crd/standard/gateway.networking.k8s.io_referencegrants.yaml
    retry kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.5.1/config/crd/standard/gateway.networking.k8s.io_grpcroutes.yaml

    # Install Cilium with kube-proxy replacement, Hubble, and Gateway API CRD awareness
    # Note: APISIX is the actual Gateway Controller (GatewayClass: apisix)
    # Cilium's gatewayAPI.enabled allows network-level integration with Gateway API resources
    echo "Installing Cilium ${CILIUM_VERSION} with Hubble and kube-proxy replacement..."
    # useDigest=false on every Cilium image: the chart pins images by digest, and a
    # digest can never resolve from the airgap mirror. 02-save-images.sh saves one
    # architecture (--override-arch), so what lands in the bundle is the per-arch
    # manifest, whose digest differs from the multi-arch index digest the chart pins.
    # containerd gets a 404 for the digest and falls back to `server = https://quay.io`,
    # which in a real airgap means the CNI never installs. Verified 2026-07-26 on
    # Kakao Cloud: quay.io/cilium/cilium tags/list had v1.19.4 and manifests/v1.19.4
    # answered 200, while manifests/sha256:2eb679... — the ref the chart actually
    # requested — was 404, and squid logged 37 quay.io CONNECTs.
    # Tags become authoritative here; the images.txt refs are already version-pinned.
    # operator.resources: the chart leaves the operator with no resources block, which puts
    # it in the BestEffort QoS class -> highest oom_score_adj -> the kernel kills it first
    # under node memory pressure. Observed on master-2: OOMKilled (exit 137) 14x in 9h while
    # the node sat at 89% real memory but only 14% requests, so the scheduler still read it
    # as empty. No CPU limit on purpose - throttling the CNI control plane during a reconcile
    # storm trades an OOM for a hang.
    install_cilium() {
      # Already installed means a previous attempt got past the fetch; re-running would
      # fail on "already exists" and turn a recoverable retry into a hard failure.
      if kubectl -n kube-system get daemonset cilium >/dev/null 2>&1; then
        echo "  Cilium DaemonSet already present — skipping install"
        return 0
      fi
      cilium install --version "${CILIUM_VERSION}" \
      --set kubeProxyReplacement=true \
      --set k8sServiceHost="${K8S_API_SERVER}" \
      --set k8sServicePort=6443 \
      --set hubble.relay.enabled=true \
      --set hubble.ui.enabled=true \
      --set gatewayAPI.enabled=true \
      --set cni.exclusive=false \
      --set socketLB.hostNamespaceOnly=true \
      --set operator.resources.requests.cpu=50m \
      --set operator.resources.requests.memory=128Mi \
      --set operator.resources.limits.memory=512Mi \
      --set image.useDigest=false \
      --set operator.image.useDigest=false \
      --set envoy.image.useDigest=false \
      --set hubble.relay.image.useDigest=false \
      --set hubble.ui.backend.image.useDigest=false \
      --set hubble.ui.frontend.image.useDigest=false \
      --set preflight.image.useDigest=false \
      --set clustermesh.apiserver.image.useDigest=false
    }
    # `cilium install` reaches helm.cilium.io for the chart, and that fetch failed with
    # `context deadline exceeded` on a clean run while curl to the same host answered 200
    # five times a minute later.
    retry install_cilium

    # D6: Wait for cilium-operator Ready before declaring Phase-1 CNI done.
    # The operator hitting Unauthorized on a slow apiserver SA-token issuance is the
    # known root cause of the Phase-2 Cilium bring-up flake.  A confirmed-healthy
    # operator here means Phase-2's cilium_health_gate will converge quickly.
    echo "Waiting for cilium-operator Deployment to become Available..."
    kubectl rollout status deployment/cilium-operator -n kube-system --timeout=120s \
      || echo "WARN: cilium-operator rollout status timed out; Phase-2 gate will retry"

    # Wait for core Cilium components (not hubble - it needs worker nodes)
    cilium status --wait --wait-duration 120s || echo "WARN: cilium status timed out (hubble may need worker nodes)"

    # Install Hubble CLI
    HUBBLE_CLI_VERSION="${HUBBLE_CLI_VERSION:-v1.19.4}"
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
