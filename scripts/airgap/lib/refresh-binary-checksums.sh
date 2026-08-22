#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 dasomel
#=========================================================================
# refresh-binary-checksums.sh
# Re-download binaries, manifests, and source tarballs to regenerate
# binary-checksums.tsv.
#
# WHY: the checksum table ensures air-gap bundle builds do not pull
# unverified or tampered binaries from the public internet. When versions
# in 07-save-binaries.sh are bumped, this tool updates the digests so the
# change is visible as a single reviewable diff.
#
# Requires: curl, sha256sum (or shasum). Needs network access.
#
# USAGE: scripts/airgap/lib/refresh-binary-checksums.sh [--check]
#   --check   exit 1 if anything changed, printing the diff (for CI)
#=========================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP="${HERE}/binary-checksums.tsv"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

HELM_VERSION="${HELM_VERSION:-v4.2.1}"
CILIUM_CLI_VERSION="${CILIUM_CLI_VERSION:-v0.19.4}"
HUBBLE_CLI_VERSION="${HUBBLE_CLI_VERSION:-v1.19.4}"
YQ_VERSION="${YQ_VERSION:-v4.44.6}"
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-v0.8.1}"
GATEWAY_API_VERSION="${GATEWAY_API_VERSION:-v1.5.1}"
ARGOCD_VERSION="${ARGOCD_VERSION:-v3.4.4}"
KEYCLOAK_VERSION="${KEYCLOAK_VERSION:-26.5.7}"
NFS_QUOTA_AGENT_COMMIT="${NFS_QUOTA_AGENT_COMMIT:-387b057eec6aab7ebf7e26757e47dbb93a944307}"

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

artifacts=()

for arch in amd64 arm64; do
  artifacts+=(
    "helm	${arch}	${HELM_VERSION}	https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz"
    "cilium	${arch}	${CILIUM_CLI_VERSION}	https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-${arch}.tar.gz"
    "hubble	${arch}	${HUBBLE_CLI_VERSION}	https://github.com/cilium/hubble/releases/download/${HUBBLE_CLI_VERSION}/hubble-linux-${arch}.tar.gz"
    "yq	${arch}	${YQ_VERSION}	https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${arch}"
  )
done

for crd in gatewayclasses gateways httproutes referencegrants grpcroutes; do
  artifacts+=(
    "gateway-api-${crd}.yaml	-	${GATEWAY_API_VERSION}	https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/${GATEWAY_API_VERSION}/config/crd/standard/gateway.networking.k8s.io_${crd}.yaml"
  )
done

artifacts+=(
  "metrics-server.yaml	-	${METRICS_SERVER_VERSION}	https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml"
  "argocd-install.yaml	-	${ARGOCD_VERSION}	https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
)

for kc in keycloaks.k8s.keycloak.org-v1.yml keycloakrealmimports.k8s.keycloak.org-v1.yml kubernetes.yml; do
  artifacts+=(
    "keycloak-${kc}	-	${KEYCLOAK_VERSION}	https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/refs/tags/${KEYCLOAK_VERSION}/kubernetes/${kc}"
  )
done

artifacts+=(
  "nfs-quota-agent	-	${NFS_QUOTA_AGENT_COMMIT}	https://github.com/dasomel/nfs-quota-agent/archive/${NFS_QUOTA_AGENT_COMMIT}.tar.gz"
)

TMP_DIR="$(mktemp -d)"
TMP_ROWS="${TMP_DIR}/rows.tsv"
TMP_OUT="${TMP_DIR}/binary-checksums.tsv"
trap 'rm -rf "${TMP_DIR}"' EXIT

touch "${TMP_ROWS}"

for item in "${artifacts[@]}"; do
  IFS=$'\t' read -r name arch ver url <<< "${item}"
  dl_file="${TMP_DIR}/download.tmp"
  rm -f "${dl_file}"
  if curl -fsSL --retry 3 --retry-delay 5 -o "${dl_file}" "${url}"; then
    digest="$(calc_sha256 "${dl_file}")"
    printf '%s\t%s\t%s\t%s\t%s\n' "${name}" "${arch}" "${ver}" "${digest}" "${url}" >> "${TMP_ROWS}"
  else
    echo "WARN: failed to download ${name} (${arch}) from ${url}" >&2
  fi
done

cat << 'EOF' > "${TMP_OUT}"
# Artifact name -> architecture -> version -> SHA-256 digest -> source URL.
#
# WHY THIS FILE EXISTS: the air-gap bundle downloads CLI binaries, Kubernetes
# manifests, and source tarballs from public internet URLs at build time.
# Verifying SHA-256 digests against this committed table prevents tampered or
# corrupted downloads from entering the air-gapped installation payload.
#
# NOT GUESSED. Every digest was computed from the actual upstream release file
# fetched at the specified URL and reviewed before commit.
#
# Refresh with: scripts/airgap/lib/refresh-binary-checksums.sh
#
# name	arch	version	sha256	url
EOF

LC_ALL=C sort -t $'\t' -k1,1 -k2,2 "${TMP_ROWS}" >> "${TMP_OUT}"

if [ "${CHECK}" -eq 1 ]; then
  if diff -u "${MAP}" "${TMP_OUT}"; then
    echo "binary-checksums.tsv is current"
    exit 0
  fi
  echo "binary-checksums.tsv is stale — run without --check and review the diff" >&2
  exit 1
fi

cp "${TMP_OUT}" "${MAP}"
echo "binary-checksums.tsv updated"
