#!/bin/bash
set -euo pipefail

# =============================================================================
# 04-bootstrap-registry.sh — Deploy a standalone registry:2 for airgap bootstrap
#
# Why: Harbor itself is one of the images we need. We cannot use cluster Harbor
# to boot the cluster. This script stands up a lightweight registry:2 container
# that hosts all images until Harbor is up, then Harbor takes over.
#
# Usage: ./04-bootstrap-registry.sh --addr registry.airgap.local:5000 [--data /srv/registry]
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

ADDR="${AIRGAP_REGISTRY}"
DATA_DIR="/srv/airgap-registry"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --addr) ADDR="$2"; shift 2 ;;
    --data) DATA_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

HOST="${ADDR%%:*}"
PORT="${ADDR##*:}"
[[ "${PORT}" == "${ADDR}" ]] && PORT=5000

echo "=== Bootstrap registry ==="
echo "  Address:  ${ADDR}"
echo "  Data dir: ${DATA_DIR}"

sudo mkdir -p "${DATA_DIR}"

# Use containerd (nerdctl) if available, fallback to docker
RUNTIME=""
if command -v nerdctl >/dev/null 2>&1; then
  RUNTIME="nerdctl"
elif command -v docker >/dev/null 2>&1; then
  RUNTIME="docker"
else
  echo "ERROR: neither nerdctl nor docker available" >&2
  exit 1
fi

# Stop existing if present
${RUNTIME} rm -f airgap-registry 2>/dev/null || true

${RUNTIME} run -d --name airgap-registry --restart=always \
  -p "${PORT}:5000" \
  -v "${DATA_DIR}:/var/lib/registry" \
  -e REGISTRY_STORAGE_DELETE_ENABLED=true \
  registry:2

sleep 2

# Verify
if curl -sf "http://localhost:${PORT}/v2/" >/dev/null; then
  echo "Registry UP at http://${ADDR}/v2/"
  echo "Add to /etc/hosts on cluster nodes:"
  echo "  <host-ip>   ${HOST}"
else
  echo "ERROR: registry not responding on :${PORT}" >&2
  exit 1
fi
