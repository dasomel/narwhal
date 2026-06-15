#!/bin/bash
set -euo pipefail

# =============================================================================
# 02-save-images.sh — Save images to local bundle using skopeo
#
# Uses OCI layout (dir://) for portability. Each image → its own directory.
# Bundle layout:
#   <bundle>/oci/<normalized-ref>/...
#   <bundle>/manifest.txt  (list of saved images)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

LIST_FILE=""
OUT_DIR="${AIRGAP_BUNDLE_DIR}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)   LIST_FILE="$2"; shift 2 ;;
    --out)    OUT_DIR="$2"; shift 2 ;;
    *)        echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${LIST_FILE}" || ! -f "${LIST_FILE}" ]]; then
  echo "Usage: $0 --list <images.txt> [--out <bundle-dir>]" >&2
  exit 1
fi

if ! command -v skopeo >/dev/null 2>&1; then
  echo "ERROR: skopeo not installed. Install: brew install skopeo (mac) / apt install skopeo (ubuntu)" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}/oci"
: > "${OUT_DIR}/manifest.txt"

fail=0 ok=0
while IFS= read -r img; do
  [[ -z "${img}" || "${img}" =~ ^# ]] && continue
  # Normalize image ref for directory name: slashes + colons → underscore
  safe=$(echo "${img}" | tr '/:' '__')
  dest="${OUT_DIR}/oci/${safe}"

  echo "[save] ${img}"
  if skopeo copy --override-arch "${AIRGAP_ARCH##*/}" --override-os linux \
       --retry-times 3 --quiet \
       "docker://${img}" "oci:${dest}:latest" 2>&1 | tail -3; then
    echo "${img}" >> "${OUT_DIR}/manifest.txt"
    ok=$((ok+1))
  else
    echo "[FAIL] ${img}" >&2
    fail=$((fail+1))
  fi
done < "${LIST_FILE}"

echo ""
echo "Saved: ${ok} | Failed: ${fail}"
echo "Bundle: ${OUT_DIR}"
[[ ${fail} -eq 0 ]]
