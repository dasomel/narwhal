#!/usr/bin/env bash
#=========================================================================
# 08-generate-sbom.sh
# Emit a CycloneDX SBOM describing everything inside an airgap bundle.
#
# WHY THIS EXISTS:
#   The bundle IS the software supply chain for an air-gapped install: 105 container
#   images, 27 Helm charts, 4 binaries, 10 remote manifests and 149 OS packages are
#   handed over as one opaque directory. Without a machine-readable inventory the
#   receiving side cannot answer "what did we just install" — which is precisely the
#   question a regulated environment has to answer, and the reason those environments
#   are air-gapped in the first place.
#
#   The portal container already ships an SPDX SBOM and SLSA provenance from buildx
#   (narwhal-portal .github/workflows/docker-publish.yml). This covers the other half:
#   the bundle, which no build tool produces attestations for because nothing "builds"
#   it — it is assembled by 02/03/07-*.sh from sources that each have their own origin.
#
# SCOPE — read this before trusting the output:
#   This is a BUNDLE-LEVEL inventory: what is in the box, at which version, with which
#   digest. It is NOT a package-level SBOM of each container image's filesystem. If you
#   need "which libc is inside apisix", run syft against the OCI layouts under oci/ and
#   attach those as nested BOMs. Stating this in the document itself matters more than
#   the omission does — an SBOM that quietly under-reports is worse than none.
#
#   Image components carry an SPDX license from lib/component-licenses.tsv. Those are
#   the license of the software each image packages, resolved from that project's own
#   repository — not the license of every file in the image filesystem, which is the
#   same package-level gap as above. 83 of 99 are Apache-2.0; the rest are AGPL-3.0
#   (Grafana, Loki, Tempo), MPL-2.0 (OpenBao) and GPL-2.0 (BusyBox, FRR) — which is why
#   the map exists instead of a blanket assumption. Everything in the bundle is OSI open
#   source: the one component that was not, Redis 8 under its RSALv2/SSPLv1/AGPLv3
#   tri-license, is replaced by Valkey in 13-argocd.sh. See NOTICE at the repository root.
#
# USAGE:
#   scripts/airgap/08-generate-sbom.sh [--bundle ./narwhal-airgap-bundle-amd64]
#                                      [--output <path>]   # default: <bundle>/sbom.cdx.json
#=========================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUNDLE=""
OUTPUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --bundle) BUNDLE="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,28p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Default to whichever single bundle is present; refuse to guess when both are.
if [ -z "${BUNDLE}" ]; then
  mapfile -t _found < <(find "${REPO_ROOT}" -maxdepth 1 -type d -name 'narwhal-airgap-bundle-*' | sort)
  case "${#_found[@]}" in
    1) BUNDLE="${_found[0]}" ;;
    0) echo "ERROR: no narwhal-airgap-bundle-* directory found; pass --bundle" >&2; exit 1 ;;
    *) echo "ERROR: several bundles present, pass --bundle explicitly:" >&2
       printf '  %s\n' "${_found[@]}" >&2; exit 1 ;;
  esac
fi

[ -d "${BUNDLE}" ] || { echo "ERROR: ${BUNDLE} is not a directory" >&2; exit 1; }
[ -f "${BUNDLE}/manifest.txt" ] || { echo "ERROR: ${BUNDLE}/manifest.txt missing — is this a bundle?" >&2; exit 1; }
OUTPUT="${OUTPUT:-${BUNDLE}/sbom.cdx.json}"

# The bundle directory name carries the architecture, and it belongs in the document:
# an amd64 bundle and an arm64 bundle hold different digests for the same image refs.
ARCH="$(basename "${BUNDLE}")"; ARCH="${ARCH##*-}"

echo "=== Generating CycloneDX SBOM ==="
echo "  bundle: ${BUNDLE}"
echo "  arch:   ${ARCH}"

python3 "${REPO_ROOT}/scripts/airgap/lib/build_sbom.py" \
  --bundle "${BUNDLE}" --arch "${ARCH}" --output "${OUTPUT}"

echo "  wrote:  ${OUTPUT}"
echo ""
echo "Verify with any CycloneDX tool, e.g.:"
echo "  python3 -c 'import json;d=json.load(open(\"${OUTPUT}\"));print(len(d[\"components\"]),\"components\")'"
