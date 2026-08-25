#!/usr/bin/env bash
set -euo pipefail

# Capture GitOps rollback inputs before changing cert-manager's chart version or values.
# The checkpoint is evidence for a future, live-cluster rollback procedure; it does not
# capture Kubernetes runtime objects and it never attempts an automatic rollback.

MANIFEST="gitops/charts/narwhal-apps/templates/cert-manager.yaml"
OUTPUT_DIR=""

usage() {
  echo "usage: $0 --output <empty-directory> [--manifest <cert-manager-application.yaml>]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output)
      OUTPUT_DIR="${2:?--output requires a directory}"
      shift 2
      ;;
    --manifest)
      MANIFEST="${2:?--manifest requires a path}"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ -z "${OUTPUT_DIR}" ]; then
  usage
  exit 2
fi
if [ ! -f "${MANIFEST}" ]; then
  echo "ERROR: cert-manager Application manifest not found: ${MANIFEST}" >&2
  exit 2
fi
if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: yq is required to capture the Application values" >&2
  exit 2
fi
if [ -e "${OUTPUT_DIR}" ]; then
  echo "ERROR: checkpoint output must not already exist: ${OUTPUT_DIR}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"
trap 'rm -rf "${OUTPUT_DIR}"' ERR

git_commit="$(git rev-parse HEAD)"
if git diff --quiet -- "${MANIFEST}"; then
  git_dirty=false
else
  git_dirty=true
fi
chart_revision="$(yq -r '.spec.source.targetRevision' "${MANIFEST}")"
yq -o=json '.spec.source.helm.valuesObject' "${MANIFEST}" > "${OUTPUT_DIR}/values.json"
cp "${MANIFEST}" "${OUTPUT_DIR}/cert-manager-application.yaml"

manifest_sha256="$(shasum -a 256 "${OUTPUT_DIR}/cert-manager-application.yaml" | awk '{print $1}')"
values_sha256="$(shasum -a 256 "${OUTPUT_DIR}/values.json" | awk '{print $1}')"

cat > "${OUTPUT_DIR}/checkpoint.json" <<EOF
{
  "component": "cert-manager",
  "captured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "git_commit": "${git_commit}",
  "manifest_dirty": ${git_dirty},
  "chart_revision": "${chart_revision}",
  "manifest_sha256": "${manifest_sha256}",
  "values_sha256": "${values_sha256}",
  "live_state_captured": false
}
EOF

echo "cert-manager rollback checkpoint captured in ${OUTPUT_DIR}"
echo "Live Kubernetes state, Helm release history, and persisted certificate material are not captured by this static checkpoint."
