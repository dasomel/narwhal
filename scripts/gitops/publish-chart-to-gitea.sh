#!/usr/bin/env bash
#=========================================================================
# publish-chart-to-gitea.sh
# Publish one or more local Helm chart .tgz packages to the in-cluster
# Gitea Helm registry (devtools/gitea-http).
#
# USAGE:
#   scripts/gitops/publish-chart-to-gitea.sh <chart.tgz> [chart2.tgz ...]
#   scripts/gitops/publish-chart-to-gitea.sh --fetch <repoUrl> <chart> <version>
#
# OPTIONS:
#   --fetch <repoUrl> <chart> <version>
#       Download chart via 'helm pull' into a temporary directory first.
#   -h, --help
#       Show this usage message.
#
# EXIT STATUS:
#   0 on success (all charts returned HTTP 200, 201, or 409).
#   Non-zero on upload failure or missing prerequisites.
#=========================================================================
set -euo pipefail

GITEA_NS="devtools"
GITEA_SVC="gitea-http"
GITEA_USER="gitea-admin"
LOCAL_PORT="${GITEA_LOCAL_PORT:-13000}"
GITEA_HELM_URL="${GITEA_HELM_URL:-}"

PF_PID=""
CURL_CFG=""
FETCH_DIR=""

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  publish-chart-to-gitea.sh <chart.tgz> [chart2.tgz ...]
  publish-chart-to-gitea.sh --fetch <repoUrl> <chart> <version>

Publish Helm chart packages (.tgz) to the in-cluster Gitea Helm registry.
Prints only '<file name> <HTTP code>' per chart.
HTTP 200, 201, and 409 (already published) are treated as success.
EOF
}

cleanup() {
  [[ -n "${PF_PID}" ]] && kill "${PF_PID}" 2>/dev/null || true
  [[ -n "${CURL_CFG}" && -f "${CURL_CFG}" ]] && rm -f "${CURL_CFG}"
  [[ -n "${FETCH_DIR}" && -d "${FETCH_DIR}" ]] && rm -rf "${FETCH_DIR}"
  return 0
}
trap cleanup EXIT

#--- parse arguments -----------------------------------------------------
CHARTS=()
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ "${1:-}" == "--fetch" ]]; then
  if [[ $# -ne 4 ]]; then
    usage >&2
    die "--fetch requires <repoUrl> <chart> <version>"
  fi
  REPO_URL="$2"
  CHART_NAME="$3"
  CHART_VERSION="$4"
  FETCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/helm-fetch.XXXXXX")"
  helm pull --repo "${REPO_URL}" "${CHART_NAME}" --version "${CHART_VERSION}" -d "${FETCH_DIR}"
  for f in "${FETCH_DIR}"/*.tgz; do
    if [[ -f "${f}" ]]; then
      CHARTS+=("${f}")
    fi
  done
  [[ ${#CHARTS[@]} -gt 0 ]] || die "helm pull produced no .tgz files in ${FETCH_DIR}"
elif [[ $# -gt 0 ]]; then
  for arg in "$@"; do
    [[ -f "${arg}" ]] || die "chart file not found: ${arg}"
    CHARTS+=("${arg}")
  done
else
  usage >&2
  exit 1
fi

#--- resolve credentials & connection ------------------------------------
if [[ -n "${GITEA_HELM_URL}" ]]; then
  UPLOAD_URL="${GITEA_HELM_URL}"
  CURL_CFG="$(mktemp "${TMPDIR:-/tmp}/gitea-curl.XXXXXX")"
  chmod 600 "${CURL_CFG}"
  if [[ -n "${GITEA_AUTH:-}" ]]; then
    printf 'header = "Authorization: %s"\n' "${GITEA_AUTH}" > "${CURL_CFG}"
  else
    : > "${CURL_CFG}"
  fi
else
  if [[ -z "${KUBECONFIG:-}" && -f "/home/vagrant/.kube/config-local" ]]; then
    export KUBECONFIG="/home/vagrant/.kube/config-local"
  fi

  GITEA_PW="$(kubectl get secret gitea-admin -n "${GITEA_NS}" -o jsonpath='{.data.admin-password}' | base64 -d)"
  [[ -n "${GITEA_PW}" ]] || die "gitea-admin password not found in ${GITEA_NS}"

  CURL_CFG="$(mktemp "${TMPDIR:-/tmp}/gitea-curl.XXXXXX")"
  chmod 600 "${CURL_CFG}"
  AUTH_B64="$(printf '%s:%s' "${GITEA_USER}" "${GITEA_PW}" | base64 | tr -d '\n')"
  printf 'header = "Authorization: Basic %s"\n' "${AUTH_B64}" > "${CURL_CFG}"
  unset GITEA_PW AUTH_B64

  kubectl port-forward "svc/${GITEA_SVC}" -n "${GITEA_NS}" "${LOCAL_PORT}:3000" >/dev/null 2>&1 &
  PF_PID=$!

  READY=0
  for _ in $(seq 1 30); do
    if curl -s -o /dev/null -m 2 "http://localhost:${LOCAL_PORT}/"; then
      READY=1
      break
    fi
    sleep 1
  done
  [[ "${READY}" -eq 1 ]] || die "port-forward to svc/${GITEA_SVC} did not become ready within 30s"

  UPLOAD_URL="http://localhost:${LOCAL_PORT}/api/packages/${GITEA_USER}/helm/api/charts"
fi

#--- upload chart packages -----------------------------------------------
FAILED=0
for chart in "${CHARTS[@]}"; do
  fname="$(basename "${chart}")"
  code="000"
  for attempt in 1 2 3; do
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 120 --config "${CURL_CFG}" -X POST --upload-file "${chart}" "${UPLOAD_URL}" || echo "000")
    case "${code}" in
      5*|000)
        if [[ "${attempt}" -lt 3 ]]; then
          sleep 2
        fi
        ;;
      *) break ;;
    esac
  done
  echo "${fname} ${code}"
  case "${code}" in
    200|201|409) ;;
    *) FAILED=1 ;;
  esac
done

if [[ "${FAILED}" -ne 0 ]]; then
  exit 1
fi
