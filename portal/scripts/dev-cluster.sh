#!/usr/bin/env bash
# Live-dev the IDP portal in the local Vagrant cluster (skaffold dev + Next HMR).
#
# D-PORTAL-DEV-FROM-CHART: the dev Deployment is NOT a hand-kept copy. It is the
# chart Deployment rendered exactly as ArgoCD renders it (narwhal-apps' valuesObject
# for narwhal-platform -> helm template narwhal-platform), with ONLY three container
# fields changed: image (skaffold tags it dev-*), resources (next dev needs ~3Gi, the
# chart's 512Mi OOMs) and a startupProbe (liveness only starts after the first
# compile). argocd-config.yaml ignoreDifferences must list exactly these three
# (.image/.resources/.startupProbe, guarded by :dev-); R299 pins both sides.
# Valkey, Service, secrets and RBAC stay ArgoCD-managed and are never applied here.
#
# Usage: portal/scripts/dev-cluster.sh [--render-only [FILE]] [skaffold dev args...]
#   --render-only  write the manifest (default portal/deploy/.generated/...) and exit
# Env: KUBE_CONTEXT (default narwhal), NARWHAL_ROOT (repo root, default derived).
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${NARWHAL_ROOT:-$(cd "${SELF_DIR}/../.." && pwd)}"
PORTAL_DIR="${ROOT}/portal"
OUT="${PORTAL_DIR}/deploy/.generated/skaffold-dev-portal.yaml"
DEV_IMAGE="harbor.local.narwhal.internal/library/narwhal-portal"
KUBE_CONTEXT="${KUBE_CONTEXT:-narwhal}"

render_only=false
if [ "${1:-}" = "--render-only" ]; then
  render_only=true
  shift
  if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
    OUT="$1"
    shift
  fi
fi

need=(helm yq)
$render_only || need+=(skaffold kubectl)
for tool in "${need[@]}"; do
  command -v "${tool}" >/dev/null 2>&1 || { echo "ERROR: ${tool} not found on PATH" >&2; exit 1; }
done

if ! $render_only; then
  ctx="$(kubectl config current-context 2>/dev/null || true)"
  if [ "${ctx}" != "${KUBE_CONTEXT}" ]; then
    echo "ERROR: kube context is '${ctx}', expected the local cluster '${KUBE_CONTEXT}'." >&2
    echo "       Run: kubectl config use-context ${KUBE_CONTEXT}  (or set KUBE_CONTEXT)" >&2
    exit 1
  fi
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# Same values ArgoCD hands to the narwhal-platform Application.
helm template narwhal-apps "${ROOT}/gitops/charts/narwhal-apps" \
  --show-only templates/narwhal-platform.yaml \
  | yq 'select(.kind == "Application" and .metadata.name == "narwhal-platform") | .spec.source.helm.valuesObject' \
  >"${tmp}/values.yaml"

base_domain="$(yq '.baseDomain' "${tmp}/values.yaml")"
if [ "${base_domain}" != "local.narwhal.internal" ]; then
  echo "ERROR: baseDomain is '${base_domain}'; portal/skaffold.yaml pushes to harbor.local.narwhal.internal" >&2
  exit 1
fi

helm template narwhal-platform "${ROOT}/gitops/charts/narwhal-platform" \
  -f "${tmp}/values.yaml" --show-only templates/narwhal-portal-k8s.yaml \
  >"${tmp}/rendered.yaml"

mkdir -p "$(dirname "${OUT}")"
# Only the Deployment; the three fields below are the complete set of changes.
IMAGE="${DEV_IMAGE}" yq '
  select(.kind == "Deployment" and .metadata.name == "narwhal-portal")
  | (.spec.template.spec.containers[] | select(.name == "narwhal-portal")) |= (
      .image = strenv(IMAGE)
      | .resources.requests.memory = "1Gi"
      | .resources.limits.memory = "3Gi"
      | .resources.limits.cpu = "1"
      | .startupProbe = {"httpGet": {"path": .livenessProbe.httpGet.path, "port": .livenessProbe.httpGet.port}, "periodSeconds": 5, "failureThreshold": 60}
    )
' "${tmp}/rendered.yaml" >"${OUT}"

if [ ! -s "${OUT}" ]; then
  echo "ERROR: chart has no Deployment narwhal-portal; nothing generated" >&2
  exit 1
fi
echo "Generated ${OUT}"

$render_only && exit 0

cd "${PORTAL_DIR}"
exec skaffold dev -p dev --cleanup=false --status-check=true "$@"
