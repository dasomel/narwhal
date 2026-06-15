#!/bin/bash
set -euo pipefail

# =============================================================================
# 05-load-images.sh — Push bundle contents to airgap registry
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

BUNDLE="${AIRGAP_BUNDLE_DIR}"
REG="${AIRGAP_REGISTRY}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)   BUNDLE="$2"; shift 2 ;;
    --registry) REG="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -d "${BUNDLE}/oci" || ! -f "${BUNDLE}/manifest.txt" ]]; then
  echo "ERROR: bundle at ${BUNDLE} is not valid (missing oci/ or manifest.txt)" >&2
  exit 1
fi

if ! command -v skopeo >/dev/null 2>&1; then
  echo "ERROR: skopeo not installed" >&2
  exit 1
fi

TLS_OPT=""
if [[ "${AIRGAP_SKOPEO_DEST_TLS_VERIFY}" == "false" ]]; then
  TLS_OPT="--dest-tls-verify=false"
fi

ok=0; fail=0
while IFS= read -r img; do
  [[ -z "${img}" ]] && continue
  safe=$(echo "${img}" | tr '/:' '__')
  src="${BUNDLE}/oci/${safe}"
  if [[ ! -d "${src}" ]]; then
    echo "[skip] ${img} (not in bundle)"
    continue
  fi

  # Rewrite the image path: keep the full original path as subdirectory under the mirror
  # Example: registry.k8s.io/pause:3.10 → ${REG}/registry.k8s.io/pause:3.10
  dst="${REG}/${img}"

  echo "[load] ${img} → ${dst}"
  if skopeo copy ${TLS_OPT} --retry-times 3 --quiet \
       "oci:${src}:latest" "docker://${dst}"; then
    ok=$((ok+1))
  else
    echo "[FAIL] ${img}" >&2
    fail=$((fail+1))
  fi
done < "${BUNDLE}/manifest.txt"

echo ""
echo "Loaded: ${ok} | Failed: ${fail}"
[[ ${fail} -eq 0 ]]
