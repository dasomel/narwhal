#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="${STAGE_DIR:-/home/vagrant}"
# shellcheck source=scripts/common/lib-charts.sh
source "${STAGE_DIR}/scripts/common/lib-charts.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
helm template narwhal-apps "${STAGE_DIR}/configs/gitops/charts/narwhal-apps" \
  --show-only templates/kubelet-csr-approver.yaml \
  --set-string "provider=${PROVIDER:-vagrant},kubeletCsrTopology=${NARWHAL_TOPOLOGY:-ha}" \
  --set-json "kubeletCsrNodes=${KUBELET_CSR_NODES:?missing provisioned node inventory}" > "$tmp/application"
yq -e '.spec.source.helm.valuesObject' "$tmp/application" > "$tmp/values"
revision="$(yq -er '.spec.source.targetRevision' "$tmp/application")"
package="$(chart kubelet-csr-approver)"
[[ "$(helm show chart "$package" | yq -er .version)" == "$revision" ]] || {
  echo "ERROR: expected kubelet-csr-approver version $revision (found $package)" >&2
  exit 1
}

# D3: apply the same Helm-rendered objects Argo CD will adopt without creating
# a separate Helm release. Cost: bootstrap cannot use Helm rollback. Escape
# hatch: revert the GitOps Application after rolling back serving TLS.
helm template kubelet-csr-approver "$package" --namespace kube-system \
  --skip-tests -f "$tmp/values" > "$tmp/objects"
kubectl apply --server-side --field-manager=narwhal-bootstrap -f "$tmp/objects"
kubectl -n kube-system rollout status deploy/kubelet-csr-approver --timeout=300s
