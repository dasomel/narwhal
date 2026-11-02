#!/usr/bin/env bash
# Live-dev the IDP portal in the local Vagrant cluster (skaffold dev + Next HMR).
#
# D-PORTAL-DEV-FROM-CHART: the dev Deployment is NOT a hand-kept copy. It is the
# chart Deployment rendered exactly as ArgoCD would (narwhal-apps' valuesObject for
# narwhal-platform -> helm template narwhal-platform), with ONLY three container
# fields changed: image (skaffold tags it dev-*), resources (next dev needs ~3Gi, the
# chart's 512Mi OOMs) and a startupProbe (liveness waits for the first compile).
# Valkey, Service, secrets and RBAC stay ArgoCD-managed and are never applied here.
#
# D-PORTAL-DEV-FIELD-MANAGER: skaffold applies it server-side as field manager
# narwhal-portal-dev and argocd-cm ignores fields that manager owns (a jq guard on
# ":dev-" is asymmetric target-vs-live and selfHeal reverted it). Ownership is handed
# back to argocd-controller by --restore, which also runs on exit/Ctrl+C.
#
# Usage:
#   portal/scripts/dev-cluster.sh [skaffold dev args...]   dev session
#   portal/scripts/dev-cluster.sh --restore                git Deployment back, ArgoCD refresh
#   portal/scripts/dev-cluster.sh --render-only [FILE]     write the dev manifest and exit
# Env: KUBE_CONTEXT (default narwhal), NARWHAL_ROOT (repo root, default derived),
#      ARGOCD_RESTART_CONTROLLER=1 (restart application-controller after patching argocd-cm).
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${NARWHAL_ROOT:-$(cd "${SELF_DIR}/../.." && pwd)}"
PORTAL_DIR="${ROOT}/portal"
OUT="${PORTAL_DIR}/deploy/.generated/skaffold-dev-portal.yaml"
HARBOR="harbor.local.narwhal.internal"
DEV_IMAGE="${HARBOR}/library/narwhal-portal"
DEV_MANAGER="narwhal-portal-dev"
CM_KEY="resource.customizations.ignoreDifferences.apps_Deployment"
KUBE_CONTEXT="${KUBE_CONTEXT:-narwhal}"

mode=dev
case "${1:-}" in
  --restore) mode=restore; shift ;;
  --render-only)
    mode=render
    shift
    if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
      OUT="$1"
      shift
    fi
    ;;
esac

need=(helm yq)
[ "${mode}" = render ] || need+=(kubectl)
[ "${mode}" = dev ] && need+=(skaffold curl)
for tool in "${need[@]}"; do
  command -v "${tool}" >/dev/null 2>&1 || { echo "ERROR: ${tool} not found on PATH" >&2; exit 1; }
done

if [ "${mode}" != render ]; then
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
  echo "ERROR: baseDomain is '${base_domain}'; portal/skaffold.yaml pushes to ${HARBOR}" >&2
  exit 1
fi

render_chart() { # $1 = template file under templates/
  helm template narwhal-platform "${ROOT}/gitops/charts/narwhal-platform" \
    -f "${tmp}/values.yaml" --show-only "templates/$1"
}

# The git version of the Deployment (what ArgoCD wants).
render_chart narwhal-portal-k8s.yaml \
  | yq 'select(.kind == "Deployment" and .metadata.name == "narwhal-portal")' >"${tmp}/git-deployment.yaml"
if [ ! -s "${tmp}/git-deployment.yaml" ]; then
  echo "ERROR: chart has no Deployment narwhal-portal" >&2
  exit 1
fi

restore() {
  echo "Restoring ArgoCD ownership of Deployment narwhal-portal ..."
  # 1) argocd-controller re-takes every field the git version sets (image 1.1.5, resources).
  # Step 2 runs only if step 1 succeeded: releasing the dev manager's fields while argocd-controller
  # does not own image yet would leave the Deployment with no owner for it (restore() runs under
  # `restore ||`, so set -e is off here and each step must check its own exit code).
  if ! kubectl apply --server-side --force-conflicts --field-manager=argocd-controller -f "${tmp}/git-deployment.yaml"; then
    echo "ERROR: could not re-apply the git Deployment as argocd-controller; dev fields left as they are. Retry: bash scripts/dev-cluster.sh --restore" >&2
    return 1
  fi
  # 2) an empty apply as the dev manager releases what only it owned (startupProbe, skaffold labels).
  if ! printf 'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: narwhal-portal\n  namespace: devtools\n' \
    | kubectl apply --server-side --force-conflicts --field-manager="${DEV_MANAGER}" -f -; then
    echo "ERROR: could not release ${DEV_MANAGER}'s fields; retry: bash scripts/dev-cluster.sh --restore" >&2
    return 1
  fi
  kubectl -n devtools annotate application narwhal-platform argocd.argoproj.io/refresh=hard --overwrite >/dev/null \
    || echo "WARN: ArgoCD refresh request failed; it will notice on its next poll" >&2
  echo "Restored. Git changes to the portal Deployment are visible to ArgoCD again."
}

if [ "${mode}" = restore ]; then
  restore || exit 1
  exit 0
fi

# Dev Deployment = git Deployment with exactly image/resources/startupProbe changed.
mkdir -p "$(dirname "${OUT}")"
IMAGE="${DEV_IMAGE}" yq '
  (.spec.template.spec.containers[] | select(.name == "narwhal-portal")) |= (
      .image = strenv(IMAGE)
      | .resources.requests.memory = "1Gi"
      | .resources.limits.memory = "3Gi"
      | .resources.limits.cpu = "1"
      | .startupProbe = {"httpGet": {"path": .livenessProbe.httpGet.path, "port": .livenessProbe.httpGet.port}, "periodSeconds": 5, "failureThreshold": 60}
    )
' "${tmp}/git-deployment.yaml" >"${OUT}"
echo "Generated ${OUT}"
[ "${mode}" = render ] && exit 0

# Preflight 1: argocd-cm must carry the managedFieldsManagers ignore. The key sits in
# the ConfigMap self-ignore of argocd-config.yaml, so git edits never reach a running
# cluster and the live value has to be patched here.
want="$(render_chart argocd-config.yaml \
  | yq "select(.kind == \"ConfigMap\" and .metadata.name == \"argocd-cm\") | .data[\"${CM_KEY}\"]")"
have="$(kubectl -n devtools get configmap argocd-cm -o json | yq -p=json ".data[\"${CM_KEY}\"] // \"\"")"
if [ "${want}" != "${have}" ]; then
  echo "argocd-cm ${CM_KEY} differs from the chart; patching the live ConfigMap ..."
  patch="$(WANT="${want}" yq -n -o=json ".data[\"${CM_KEY}\"] = strenv(WANT)")"
  kubectl -n devtools patch configmap argocd-cm --type merge -p "${patch}"
  # The ArgoCD docs only call for a controller restart for resource exclusions and custom
  # labels, not ignoreDifferences (argo-cd.readthedocs.io declarative-setup, diffing), so
  # no restart by default. If ArgoCD still reverts the dev Deployment, re-run with
  # ARGOCD_RESTART_CONTROLLER=1.
  if [ "${ARGOCD_RESTART_CONTROLLER:-0}" = 1 ]; then
    kubectl -n devtools rollout restart statefulset/argocd-application-controller
    kubectl -n devtools rollout status statefulset/argocd-application-controller --timeout=120s
  fi
fi

# Preflight 2: skaffold's digest lookup runs on this Mac and uses the macOS keychain,
# while `docker push` trusts ~/.docker/certs.d/<harbor>/ca.crt. A stale cluster CA in the
# keychain fails x509 for skaffold only. Harbor answers 401 on /v2/ without auth, so
# only the curl exit code (TLS) matters, not the HTTP status.
extra=()
if ! curl -sS -o /dev/null "https://${HARBOR}/v2/" 2>"${tmp}/curl.err"; then
  echo "WARN: TLS to ${HARBOR} not trusted by system trust: $(cat "${tmp}/curl.err")" >&2
  echo "WARN: adding --insecure-registry=${HARBOR} to skaffold. This affects only skaffold's" >&2
  echo "      digest lookup; docker push still verifies via ~/.docker/certs.d. To fix properly:" >&2
  echo "      mkdir -p ~/.docker/certs.d/${HARBOR} && kubectl -n platform-system get secret narwhal-root-ca-secret \\" >&2
  echo "        -o jsonpath='{.data.ca\\.crt}' | base64 -d > ~/.docker/certs.d/${HARBOR}/ca.crt   # then restart Docker Desktop" >&2
  extra+=("--insecure-registry=${HARBOR}")
fi

on_exit() {
  status=$?
  trap - EXIT INT TERM
  restore || echo "WARN: restore failed; run: portal/scripts/dev-cluster.sh --restore" >&2
  rm -rf "${tmp}"
  exit "${status}"
}
trap on_exit EXIT
trap 'exit 130' INT TERM

cd "${PORTAL_DIR}"
skaffold dev -p dev --cleanup=false --status-check=true ${extra[@]+"${extra[@]}"} "$@"
