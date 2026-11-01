#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCH="${AIRGAP_ARCH_SUFFIX:-arm64}"
BUNDLE_DIR="${AIRGAP_BUNDLE_DIR:-${PROJECT_ROOT}/narwhal-airgap-bundle-${ARCH}}"
requirements="$(python3 "${SCRIPT_DIR}/lib/scan-bundle-requirements.py" "${PROJECT_ROOT}")"
missing=0

while IFS="$(printf '\t')" read -r kind name; do
  [ -n "${name}" ] || continue
  case "${kind}" in
    charts)
      found_chart=false
      for file in "${BUNDLE_DIR}/charts/${name}"-*.tgz; do
        [ -f "${file}" ] && found_chart=true && break
      done
      [ "${found_chart}" = true ] || { printf 'Missing chart: charts/%s-*.tgz\n' "${name}"; missing=1; }
      ;;
    manifests)
      [ -f "${BUNDLE_DIR}/manifests/${name}" ] || { printf 'Missing manifest: manifests/%s\n' "${name}"; missing=1; }
      ;;
    bin)
      [ -x "${BUNDLE_DIR}/bin/${name}" ] || { printf 'Missing binary: bin/%s\n' "${name}"; missing=1; }
      ;;
  esac
done <<EOF
${requirements}
EOF

# D-BUNDLE-PIN-VERSION: the glob above accepts any version, so a bundle from before a pin
# bump passed it and Phase 2 died ~40 min in (ArgoCD: chart "falco" version "9.2.1" not found
# in Gitea; 2026-11-02). 12-gitea.sh uploads exactly this directory (the one the Vagrantfile
# mounts), so every GitOps pin must be present as <chart>-<version>.tgz. Offline and fast;
# discovery is shared with 03-save-helm-charts.sh. Escape hatch: NARWHAL_SKIP_BUNDLE_PREFLIGHT=1
# skips only this version check (e.g. charts are served by a registry that already holds them).
if [ "${NARWHAL_SKIP_BUNDLE_PREFLIGHT:-0}" != "1" ]; then
  pins="$(python3 "${SCRIPT_DIR}/lib/discover-charts.py" "${PROJECT_ROOT}" "${SCRIPT_DIR}/lib/chart-upstream-sources.tsv" --gitops-only)"
  while IFS='|' read -r _ _ chart version _; do
    [ -n "${chart}" ] || continue
    [ -f "${BUNDLE_DIR}/charts/${chart}-${version}.tgz" ] || {
      printf 'Missing chart pin: charts/%s-%s.tgz\n' "${chart}" "${version}"; missing=1; }
  done <<EOF2
${pins}
EOF2
fi

if [ "${missing}" -ne 0 ]; then
  printf 'Create bundle contents with:\n  AIRGAP_ARCH=%s scripts/airgap/07-save-binaries.sh\n  AIRGAP_ARCH=linux/%s scripts/airgap/03-save-helm-charts.sh\n' "${ARCH}" "${ARCH}" >&2
  exit 1
fi
printf 'Bundle preflight passed: %s\n' "${BUNDLE_DIR}"
