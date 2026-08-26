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

# macOS attaches com.apple.provenance to files pulled off the network — the nfs-quota-agent
# source tarball below included — regardless of how they were fetched (curl or git both carry
# it; verified empirically, not download-specific). macOS's system tar (bsdtar) then writes an
# AppleDouble sidecar (._<name>) for every xattr-bearing file when re-packing them, and bsdtar's
# own `tar tzf` hides those sidecars from a plain listing, so the corruption is invisible on the
# machine that built the bundle. GNU tar on the Linux cluster nodes extracts them as literal
# files; `._quota.nfs.io_quotapolicies.yaml` then fails helm's YAML parse with "control
# characters are not allowed". This is packaging-time corruption of the tarball this script
# writes — a different bug from stage-kakao-nodes.sh's COPYFILE_DISABLE (transfer-time, its own
# tar-over-ssh) — but the same fix. `helm package` elsewhere in the airgap scripts is unaffected:
# it uses Go's archive/tar, which does not consult xattrs the way bsdtar does.
export COPYFILE_DISABLE=1

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
NFS_QUOTA_AGENT_COMMIT="${NFS_QUOTA_AGENT_COMMIT:-387b057eec6aab7ebf7e26757e47dbb93a944307}" # resolved 2026-08-23

CHECKSUM_FILE="${SCRIPT_DIR}/lib/binary-checksums.tsv"
if [ ! -f "${CHECKSUM_FILE}" ]; then
  echo "ERROR: checksum file missing: ${CHECKSUM_FILE}" >&2
  exit 1
fi

ARCHES="${AIRGAP_ARCH:-amd64 arm64}"
FAIL=0

calc_sha256() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file}" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file}" | cut -d' ' -f1
  else
    echo "ERROR: neither sha256sum nor shasum is available" >&2
    return 1
  fi
}

lookup_sha256() {
  local url="$1" name="$2" arch="$3"
  awk -v u="${url}" -v n="${name}" -v a="${arch}" '
    BEGIN { FS="\t" }
    /^#/ || /^$/ { next }
    $5 == u { print $4; exit }
    $1 == n && $2 == a { print $4; exit }
  ' "${CHECKSUM_FILE}"
}

fetch() {
  local url="$1" dest="$2" name="${3:-}" arch="${4:-}"
  [ -z "${name}" ] && name="$(basename "${dest}")"
  [ -z "${arch}" ] && arch="${AIRGAP_ARCH_CURRENT:--}"

  mkdir -p "$(dirname "${dest}")"
  if curl -fsSL --retry 3 --retry-delay 5 -o "${dest}.part" "${url}"; then
    local actual_sha expected_sha
    actual_sha="$(calc_sha256 "${dest}.part")"
    expected_sha="$(lookup_sha256 "${url}" "${name}" "${arch}")"

    if [ -z "${expected_sha}" ]; then
      rm -f "${dest}.part"
      echo "  [FAIL] ${name} (${arch}): checksum missing from binary-checksums.tsv" >&2
      FAIL=$((FAIL + 1))
      return 1
    fi

    if [ "${actual_sha}" != "${expected_sha}" ]; then
      rm -f "${dest}.part"
      echo "  [FAIL] ${name} (${arch}): checksum mismatch (expected ${expected_sha}, got ${actual_sha})" >&2
      FAIL=$((FAIL + 1))
      return 1
    fi

    mv "${dest}.part" "${dest}"
    printf '  [ok]   %-46s %s\n' "${name}" "$(du -h "${dest}" | cut -f1)"
    return 0
  else
    rm -f "${dest}.part"
    echo "  [FAIL] ${url}" >&2
    FAIL=$((FAIL + 1))
    return 1
  fi
}

for arch in ${ARCHES}; do
  AIRGAP_ARCH_CURRENT="${arch}"
  OUT="${AIRGAP_BUNDLE_DIR%-*}-${arch}/bin"
  MAN="${AIRGAP_BUNDLE_DIR%-*}-${arch}/manifests"
  CHARTS="${AIRGAP_BUNDLE_DIR%-*}-${arch}/charts"
  echo ""
  echo "=== ${arch}: binaries -> ${OUT} ==="
  mkdir -p "${OUT}"

  # helm ships as a tarball with the binary one level down; store the extracted binary so
  # the node side is a copy rather than an unpack.
  tmp=$(mktemp -d)
  helm_url="https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz"
  if fetch "${helm_url}" "${tmp}/helm.tgz" "helm" "${arch}"; then
    tar xzf "${tmp}/helm.tgz" -C "${tmp}"
    mv "${tmp}/linux-${arch}/helm" "${OUT}/helm"
    chmod +x "${OUT}/helm"
  fi
  rm -rf "${tmp}"

  # cilium-cli and hubble are also tarballs, single binary at the root.
  for spec in "cilium-cli:${CILIUM_CLI_VERSION}:cilium-linux-${arch}.tar.gz:cilium" \
              "hubble:${HUBBLE_CLI_VERSION}:hubble-linux-${arch}.tar.gz:hubble"; do
    IFS=: read -r repo ver file bin <<<"${spec}"
    tmp=$(mktemp -d)
    archive_url="https://github.com/cilium/${repo}/releases/download/${ver}/${file}"
    if fetch "${archive_url}" "${tmp}/${file}" "${bin}" "${arch}"; then
      tar xzf "${tmp}/${file}" -C "${tmp}"
      mv "${tmp}/${bin}" "${OUT}/${bin}"
      chmod +x "${OUT}/${bin}"
    fi
    rm -rf "${tmp}"
  done

  # yq is a bare binary.
  fetch "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${arch}" \
    "${OUT}/yq" "yq" "${arch}"
  # `[ -f ] && chmod` would abort the whole run under `set -e` when the fetch above
  # failed its checksum — the point of counting FAIL is to report every artifact.
  if [ -f "${OUT}/yq" ]; then
    chmod +x "${OUT}/yq"
  fi

  echo ""
  echo "=== ${arch}: manifests -> ${MAN} ==="
  # Applied with `kubectl apply -f <url>` today. Same content, fetched once.
  for crd in gatewayclasses gateways httproutes referencegrants grpcroutes; do
    fetch "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/${GATEWAY_API_VERSION}/config/crd/standard/gateway.networking.k8s.io_${crd}.yaml" \
      "${MAN}/gateway-api-${crd}.yaml" "gateway-api-${crd}.yaml" "-"
  done
  fetch "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml" \
    "${MAN}/metrics-server.yaml" "metrics-server.yaml" "-"

  # ArgoCD and the Keycloak operator are applied straight off raw.githubusercontent.com in
  # Phase 2 — the same class as the CRDs above, just later in the run.
  fetch "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml" \
    "${MAN}/argocd-install.yaml" "argocd-install.yaml" "-"
  for kc in keycloaks.k8s.keycloak.org-v1.yml keycloakrealmimports.k8s.keycloak.org-v1.yml kubernetes.yml; do
    fetch "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/refs/tags/${KEYCLOAK_VERSION}/kubernetes/${kc}" \
      "${MAN}/keycloak-${kc}" "keycloak-${kc}" "-"
  done

  # nfs-quota-agent ships its chart inside a source tarball rather than a chart repo, so it
  # lands beside the other charts in the shape lib-charts.sh expects.
  echo ""
  echo "=== ${arch}: source charts -> ${CHARTS} ==="
  mkdir -p "${CHARTS}"
  tmp=$(mktemp -d)
  nqa_url="https://github.com/dasomel/nfs-quota-agent/archive/${NFS_QUOTA_AGENT_COMMIT}.tar.gz"
  if fetch "${nqa_url}" "${tmp}/nqa.tgz" "nfs-quota-agent" "-"; then
    tar xzf "${tmp}/nqa.tgz" -C "${tmp}"
    nqa_dir=""
    for d in "${tmp}"/nfs-quota-agent-*; do
      if [ -d "${d}" ]; then
        nqa_dir="${d}"
        break
      fi
    done
    if [ -n "${nqa_dir}" ] && [ -d "${nqa_dir}/charts/nfs-quota-agent" ]; then
      tar czf "${CHARTS}/nfs-quota-agent-0.0.0-main.tgz" \
        -C "${nqa_dir}/charts" nfs-quota-agent
      printf '  [ok]   %-46s %s\n' "nfs-quota-agent-0.0.0-main.tgz" \
        "$(du -h "${CHARTS}/nfs-quota-agent-0.0.0-main.tgz" | cut -f1)"
    else
      echo "  [FAIL] nfs-quota-agent: charts/nfs-quota-agent not in the tarball" >&2
      FAIL=$((FAIL + 1))
    fi
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
