#!/bin/bash
set -euo pipefail

# =============================================================================
# 07-save-binaries.sh — collect the binaries and remote manifests the cluster
#                       install fetches from the internet
# =============================================================================
# The bundle covered container images and (since lib-charts.sh) Helm charts. Everything
# else the provisioning scripts needed still came from the public internet: the helm
# binary from get.helm.sh, cilium-cli and hubble from GitHub releases, yq from GitHub, and
# nine YAML manifests applied straight off raw.githubusercontent.com.
#
# On a network with an egress proxy that reads as flakiness — a fetch times out, a retry
# saves it. On a closed network it is a hard stop, which is what this closes.
#
# Out of scope by decision: apt packages. kubeadm/kubelet/kubectl come from pkgs.k8s.io and
# containerd/jq/chrony/nfs-common from the Ubuntu archive; mirroring those is a separate
# piece of work and the operator supplies them another way.
#
# Usage:
#   scripts/airgap/07-save-binaries.sh                 # both architectures
#   AIRGAP_ARCH=amd64 scripts/airgap/07-save-binaries.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

# Versions must track the scripts that consume them. Kept here rather than sourced from
# those scripts because they are spread across four files and several are literals.
HELM_VERSION="${HELM_VERSION:-v4.2.1}"
CILIUM_CLI_VERSION="${CILIUM_CLI_VERSION:-v0.19.4}"
HUBBLE_CLI_VERSION="${HUBBLE_CLI_VERSION:-v1.19.4}"
YQ_VERSION="${YQ_VERSION:-v4.44.6}"
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-v0.8.1}"
GATEWAY_API_VERSION="${GATEWAY_API_VERSION:-v1.5.1}"
ARGOCD_VERSION="${ARGOCD_VERSION:-v3.4.4}"
KEYCLOAK_VERSION="${KEYCLOAK_VERSION:-26.5.7}"

ARCHES="${AIRGAP_ARCH:-amd64 arm64}"
FAIL=0

fetch() {
  local url="$1" dest="$2"
  mkdir -p "$(dirname "${dest}")"
  if curl -fsSL --retry 3 --retry-delay 5 -o "${dest}.part" "${url}"; then
    mv "${dest}.part" "${dest}"
    printf '  [ok]   %-46s %s\n' "$(basename "${dest}")" "$(du -h "${dest}" | cut -f1)"
  else
    rm -f "${dest}.part"
    echo "  [FAIL] ${url}" >&2
    FAIL=$((FAIL + 1))
  fi
}

for arch in ${ARCHES}; do
  OUT="${AIRGAP_BUNDLE_DIR%-*}-${arch}/bin"
  MAN="${AIRGAP_BUNDLE_DIR%-*}-${arch}/manifests"
  CHARTS="${AIRGAP_BUNDLE_DIR%-*}-${arch}/charts"
  echo ""
  echo "=== ${arch}: binaries -> ${OUT} ==="
  mkdir -p "${OUT}"

  # helm ships as a tarball with the binary one level down; store the extracted binary so
  # the node side is a copy rather than an unpack.
  tmp=$(mktemp -d)
  if curl -fsSL --retry 3 -o "${tmp}/helm.tgz" \
      "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz"; then
    tar xzf "${tmp}/helm.tgz" -C "${tmp}"
    mv "${tmp}/linux-${arch}/helm" "${OUT}/helm"
    chmod +x "${OUT}/helm"
    printf '  [ok]   %-46s %s\n' "helm" "$(du -h "${OUT}/helm" | cut -f1)"
  else
    echo "  [FAIL] helm ${HELM_VERSION} ${arch}" >&2; FAIL=$((FAIL + 1))
  fi
  rm -rf "${tmp}"

  # cilium-cli and hubble are also tarballs, single binary at the root.
  for spec in "cilium-cli:${CILIUM_CLI_VERSION}:cilium-linux-${arch}.tar.gz:cilium" \
              "hubble:${HUBBLE_CLI_VERSION}:hubble-linux-${arch}.tar.gz:hubble"; do
    IFS=: read -r repo ver file bin <<<"${spec}"
    tmp=$(mktemp -d)
    if curl -fsSL --retry 3 -o "${tmp}/${file}" \
        "https://github.com/cilium/${repo}/releases/download/${ver}/${file}"; then
      tar xzf "${tmp}/${file}" -C "${tmp}"
      mv "${tmp}/${bin}" "${OUT}/${bin}"
      chmod +x "${OUT}/${bin}"
      printf '  [ok]   %-46s %s\n' "${bin}" "$(du -h "${OUT}/${bin}" | cut -f1)"
    else
      echo "  [FAIL] ${repo} ${ver} ${arch}" >&2; FAIL=$((FAIL + 1))
    fi
    rm -rf "${tmp}"
  done

  # yq is a bare binary.
  fetch "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${arch}" "${OUT}/yq"
  [ -f "${OUT}/yq" ] && chmod +x "${OUT}/yq"

  echo ""
  echo "=== ${arch}: manifests -> ${MAN} ==="
  # Applied with `kubectl apply -f <url>` today. Same content, fetched once.
  for crd in gatewayclasses gateways httproutes referencegrants grpcroutes; do
    fetch "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/${GATEWAY_API_VERSION}/config/crd/standard/gateway.networking.k8s.io_${crd}.yaml" \
      "${MAN}/gateway-api-${crd}.yaml"
  done
  fetch "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml" \
    "${MAN}/metrics-server.yaml"

  # ArgoCD and the Keycloak operator are applied straight off raw.githubusercontent.com in
  # Phase 2 — the same class as the CRDs above, just later in the run.
  fetch "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml" \
    "${MAN}/argocd-install.yaml"
  for kc in keycloaks.k8s.keycloak.org-v1.yml keycloakrealmimports.k8s.keycloak.org-v1.yml kubernetes.yml; do
    fetch "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/refs/tags/${KEYCLOAK_VERSION}/kubernetes/${kc}" \
      "${MAN}/keycloak-${kc}"
  done

  # nfs-quota-agent ships its chart inside a source tarball rather than a chart repo, so it
  # lands beside the other charts in the shape lib-charts.sh expects.
  echo ""
  echo "=== ${arch}: source charts -> ${CHARTS} ==="
  mkdir -p "${CHARTS}"
  tmp=$(mktemp -d)
  if curl -fsSL --retry 3 -o "${tmp}/nqa.tgz" \
      "https://github.com/dasomel/nfs-quota-agent/archive/refs/heads/main.tar.gz"; then
    tar xzf "${tmp}/nqa.tgz" -C "${tmp}"
    if [ -d "${tmp}/nfs-quota-agent-main/charts/nfs-quota-agent" ]; then
      tar czf "${CHARTS}/nfs-quota-agent-0.0.0-main.tgz" \
        -C "${tmp}/nfs-quota-agent-main/charts" nfs-quota-agent
      printf '  [ok]   %-46s %s\n' "nfs-quota-agent-0.0.0-main.tgz" \
        "$(du -h "${CHARTS}/nfs-quota-agent-0.0.0-main.tgz" | cut -f1)"
    else
      echo "  [FAIL] nfs-quota-agent: charts/nfs-quota-agent not in the tarball" >&2
      FAIL=$((FAIL + 1))
    fi
  else
    echo "  [FAIL] nfs-quota-agent source tarball" >&2; FAIL=$((FAIL + 1))
  fi
  rm -rf "${tmp}"
done

echo ""
if [ "${FAIL}" -gt 0 ]; then
  echo "=== ${FAIL} item(s) failed — the bundle is incomplete ===" >&2
  exit 1
fi
echo "=== Binaries and manifests saved ==="
for arch in ${ARCHES}; do
  d="${AIRGAP_BUNDLE_DIR%-*}-${arch}"
  echo "  ${arch}: $(ls "${d}/bin" 2>/dev/null | wc -l | tr -d ' ') binaries, $(ls "${d}/manifests" 2>/dev/null | wc -l | tr -d ' ') manifests, $(du -sh "${d}/bin" 2>/dev/null | cut -f1) + $(du -sh "${d}/manifests" 2>/dev/null | cut -f1)"
done
