#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

CNI_PLUGIN="${CNI_PLUGIN:-cilium}"
CILIUM_VERSION="${CILIUM_VERSION:-1.19.4}"
CILIUM_CLI_VERSION="${CILIUM_CLI_VERSION:-v0.19.4}"
CALICO_VERSION="${CALICO_VERSION:-v3.31.3}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"

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
    # Install Helm — version pinned in scripts/airgap/07-save-binaries.sh, not here.
    install_bin helm

    # Install Cilium CLI
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi
    if [ "$ARCH" = "aarch64" ]; then ARCH="arm64"; fi

    install_bin cilium

    # Install Gateway API CRDs (used by APISIX Gateway Controller, Cilium provides network-level support)
    echo "Installing Gateway API CRDs..."
    kubectl apply -f "$(manifest gateway-api-gatewayclasses.yaml)"
    kubectl apply -f "$(manifest gateway-api-gateways.yaml)"
    kubectl apply -f "$(manifest gateway-api-httproutes.yaml)"
    kubectl apply -f "$(manifest gateway-api-referencegrants.yaml)"
    kubectl apply -f "$(manifest gateway-api-grpcroutes.yaml)"

    # Install Cilium with kube-proxy replacement, Hubble, and Gateway API CRD awareness
    # Note: APISIX is the actual Gateway Controller (GatewayClass: apisix)
    # D-CA-CILIUM-GW-OFF (owner decision 2026-11-28, CIS 5.1.2 KSV041): chart 1.19.4
    # templates/cilium-operator/clusterrole.yaml grants cluster-wide Secret read whenever
    # gatewayAPI.enabled is true. Cilium's Gateway API controller is unused (measured: 0
    # HTTPRoutes/GRPCRoutes, only istio-waypoint Gateways, no cilium-gateway Service), so it
    # is off; the Gateway API CRDs above stay installed. tls.secretSync (defaults on for >=1.17)
    # renders the same rule and is unused too (no CNP with TLS/secret refs, cilium-secrets empty).
    # Escape hatch: gatewayAPI.enabled=true / tls.secretSync.enabled=true.
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
        local live_pool
        live_pool=$(kubectl -n kube-system get configmap cilium-config \
          -o jsonpath='{.data.cluster-pool-ipv4-cidr}' 2>/dev/null || true)
        if [ "${live_pool}" != "${POD_NETWORK_CIDR}" ]; then
          echo "WARN: existing Cilium cluster pool '${live_pool:-<unset>}' differs from POD_NETWORK_CIDR '${POD_NETWORK_CIDR}'. The pool cannot be changed in place without re-IPing pods; leaving it unchanged." >&2
        fi
        return 0
      fi
      # --chart-directory, not the default --repository https://helm.cilium.io. The bundle
      # has carried cilium-${CILIUM_VERSION}.tgz all along and the CLI was fetching over
      # the internet anyway; on a real airgap that is not slow, it is impossible.
      local_chart=$(chart cilium) || return 1
      rm -rf /tmp/cilium-chart && mkdir -p /tmp/cilium-chart
      tar xzf "${local_chart}" -C /tmp/cilium-chart
      # D-CILIUM-BPFFS-SELINUX: fail closed if host bpffs is missing before disabling mount-bpf-fs.
      # Cost: depends on the systemd-mounted /sys/fs/bpf API file system; escape: set bpf.autoMount.enabled=true.
      if ! findmnt -rn -t bpf -T /sys/fs/bpf >/dev/null 2>&1; then
        echo "ERROR: /sys/fs/bpf is not mounted as type bpf; cannot install Cilium with bpf.autoMount.enabled=false" >&2
        return 1
      fi
      # D-SINGLE-NODE: Cilium operators require distinct hosts; one replica on one node.
      # Cost: no operator redundancy; escape hatch: HA keeps the chart defaults.
      local topology_args=()
      if [ "${NARWHAL_TOPOLOGY:-ha}" = "single" ]; then
        topology_args=(--set operator.replicas=1)
      fi
      # D-CILIUM-HARDENING: harden cilium, cilium-envoy, cilium-operator, and hubble-ui via chart 1.19.4 values.
      # Cost: envoy keeps explicit /sys/fs/bpf hostPath mount because upstream cilium-envoy/daemonset.yaml
      # gates bpf-maps on bpf.autoMount.enabled; envoy sets runAsUser=0 alongside runAsGroup=10100 because
      # containerd rejects runAsGroup without runAsUser. Escape: revert the securityContext/autoMount flags.
      cilium install --version "${CILIUM_VERSION}" \
      --chart-directory /tmp/cilium-chart/cilium \
      "${topology_args[@]+"${topology_args[@]}"}" \
      --set kubeProxyReplacement=true \
      --set k8sServiceHost="${K8S_API_SERVER}" \
      --set k8sServicePort=6443 \
      --set bpf.autoMount.enabled=false \
      --set podSecurityContext.seccompProfile.type=RuntimeDefault \
      --set securityContext.seLinuxOptions=null \
      --set envoy.podSecurityContext.seccompProfile.type=RuntimeDefault \
      --set envoy.podSecurityContext.runAsUser=0 \
      --set envoy.podSecurityContext.runAsGroup=10100 \
      --set envoy.securityContext.seLinuxOptions=null \
      --set "envoy.extraHostPathMounts[0].name=bpf-maps" \
      --set "envoy.extraHostPathMounts[0].mountPath=/sys/fs/bpf" \
      --set "envoy.extraHostPathMounts[0].hostPath=/sys/fs/bpf" \
      --set "envoy.extraHostPathMounts[0].hostPathType=DirectoryOrCreate" \
      --set "envoy.extraHostPathMounts[0].readOnly=false" \
      --set "envoy.extraHostPathMounts[0].mountPropagation=HostToContainer" \
      --set hubble.relay.enabled=true \
      --set hubble.ui.enabled=true \
      --set hubble.ui.securityContext.runAsNonRoot=true \
      --set hubble.ui.securityContext.seccompProfile.type=RuntimeDefault \
      --set "hubble.ui.frontend.securityContext.capabilities.drop[0]=ALL" \
      --set "hubble.ui.backend.securityContext.capabilities.drop[0]=ALL" \
      --set "ipam.operator.clusterPoolIPv4PodCIDRList[0]=${POD_NETWORK_CIDR}" \
      --set ipam.operator.clusterPoolIPv4MaskSize=24 \
      --set gatewayAPI.enabled=false \
      --set tls.secretSync.enabled=false \
      --set cni.exclusive=false \
      --set socketLB.hostNamespaceOnly=true \
      --set operator.resources.requests.cpu=50m \
      --set operator.resources.requests.memory=128Mi \
      --set operator.resources.limits.memory=512Mi \
      --set operator.podSecurityContext.runAsNonRoot=true \
      --set operator.podSecurityContext.runAsUser=10100 \
      --set operator.podSecurityContext.runAsGroup=10100 \
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
    # Captured before any install attempt: an in-run retry also sees the DaemonSet, but
    # only a cluster that already had Cilium can carry a legacy pool we must not re-IP.
    cilium_preexisting=false
    kubectl -n kube-system get daemonset cilium >/dev/null 2>&1 && cilium_preexisting=true
    retry install_cilium

    # D6: Wait for cilium-operator Ready before declaring Phase-1 CNI done.
    # The operator hitting Unauthorized on a slow apiserver SA-token issuance is the
    # known root cause of the Phase-2 Cilium bring-up flake.  A confirmed-healthy
    # operator here means Phase-2's cilium_health_gate will converge quickly.
    echo "Waiting for cilium-operator Deployment to become Available..."
    kubectl rollout status deployment/cilium-operator -n kube-system --timeout=120s \
      || echo "WARN: cilium-operator rollout status timed out; Phase-2 gate will retry"

    # Wait for core Cilium components (not hubble - it needs worker nodes)
    cilium status --wait --wait-duration 120s
    if [ "${cilium_preexisting}" = true ]; then
      # Existing clusters only get the WARN from install_cilium; failing here would make
      # every re-run of this script fatal on a pool that cannot be changed in place.
      /home/vagrant/scripts/verify/pod-cidr-consistency-check.sh \
        || echo "WARN: pre-existing Cilium pod CIDR mismatch (see above); not fatal on re-run" >&2
    else
      /home/vagrant/scripts/verify/pod-cidr-consistency-check.sh
    fi

    # Install Hubble CLI
    HUBBLE_CLI_VERSION="${HUBBLE_CLI_VERSION:-v1.19.4}"
    install_bin hubble

    echo "Hubble CLI installed: ${HUBBLE_CLI_VERSION}"
    ;;

  calico|flannel)
    # Nothing sets CNI_PLUGIN to either of these, and the airgap bundle carries only the
    # Cilium chart and images. Both branches applied a manifest straight off the internet,
    # which on a closed network fails after the cluster is already half-built. Refusing up
    # front is the honest behaviour; supporting them means bundling their artefacts too.
    echo "ERROR: CNI_PLUGIN=${CNI_PLUGIN} is not available in an airgap install." >&2
    echo "       Only cilium is bundled (chart, images and CLI). Supporting ${CNI_PLUGIN}" >&2
    echo "       means adding its chart/manifests to scripts/airgap/ first." >&2
    exit 1
    ;;

  *)
    echo "Unknown CNI plugin: ${CNI_PLUGIN}"
    exit 1
    ;;
esac

# Wait for nodes to be ready
echo "Waiting for node to be ready..."
kubectl wait --for=condition=Ready node --all --timeout=300s

serving_state=/var/lib/narwhal/kubelet-serving-tls
if [[ -f "${serving_state}/bootstrap-pending" ]]; then
  scripts="${STAGE_DIR:-/home/vagrant}/scripts"
  bash "${scripts}/cluster/install-kubelet-csr-approver.sh"
  yq -e 'select(.kind == "KubeletConfiguration").serverTLSBootstrap == true' /tmp/kubeadm-config.yaml >/dev/null
  bash "${scripts}/ops/apply-kubelet-serving-tls.sh" --kubeconfig "${KUBECONFIG}" --local "$(hostname)"
  # D-R416-TIMEOUT: static Pod restarts can wait on etcd under host IO; 600 s matches the live trial.
  bash "${scripts}/ops/apply-kubelet-serving-tls.sh" --kubeconfig "${KUBECONFIG}" --local "$(hostname)" --control-plane --timeout 600
  # D2: checkpoint before a join file becomes visible. An interrupted
  # publication can retry even if a second node has already joined.
  mv "${serving_state}/bootstrap-pending" "${serving_state}/join-pending"
fi
if [[ -f "${serving_state}/join-pending" ]]; then
  for join_file in /home/vagrant/join-command.sh /home/vagrant/join-control-plane.sh; do
    if [[ -f "${join_file}.pending" ]]; then
      mv "${join_file}.pending" "${join_file}"
    else
      test -s "${join_file}"
    fi
  done
  rm "${serving_state}/join-pending"
fi

# Harden node file permissions (CIS 1.1.9, 1.1.12, 4.1.1, 4.1.7, 4.1.9).
# Non-fatal: failures must not block provisioning. Runs after Cilium is ready
# so that /etc/cni/net.d conflist files are hardened to 600.
HARDEN_SCRIPT="${STAGE_DIR:-/home/vagrant}/scripts/cluster/harden-node-files.sh"
if [ ! -f "${HARDEN_SCRIPT}" ]; then
  HARDEN_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harden-node-files.sh"
fi
if [ -f "${HARDEN_SCRIPT}" ]; then
  bash "${HARDEN_SCRIPT}" || echo "WARN: harden-node-files.sh reported issues, continuing..."
fi

echo "=== CNI Plugin Installation Done ==="
