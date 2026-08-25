#!/bin/bash
# Export one running cluster's Portal registration metadata and scoped credentials.
#
# This script is operator-invoked and intentionally excluded from 06-phase2-start.sh:
# it needs a running target cluster and writes short-lived credentials outside Git.
set -euo pipefail

SA_NAMESPACE="devtools"
SA_NAME="narwhal-portal"
CLUSTER_ROLE_BINDING="narwhal-portal"
TOKEN_DURATION="24h"

usage() {
  cat <<'USAGE'
Usage:
  13-3-export-narwhal-portal-cluster-credentials.sh \
    --cluster-id <rfc1123-id> --cluster-name <display-name> \
    --environment <production|staging|development|sandbox> \
    --provider <kakao-cloud|aws|gcp|azure|on-prem|other> \
    --output-dir <secure-directory> [--region <region>]

Uses the active kubectl context. The output directory contains credentials; do not commit it.
USAGE
}

cluster_id=""
cluster_name=""
environment=""
provider=""
region=""
output_dir=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cluster-id|--cluster-name|--environment|--provider|--region|--output-dir)
      if [ "$#" -lt 2 ] || [ -z "${2}" ]; then
        echo "ERROR: $1 requires a non-empty value" >&2
        usage >&2
        exit 2
      fi
      case "$1" in
        --cluster-id) cluster_id="${2}" ;;
        --cluster-name) cluster_name="${2}" ;;
        --environment) environment="${2}" ;;
        --provider) provider="${2}" ;;
        --region) region="${2}" ;;
        --output-dir) output_dir="${2}" ;;
      esac
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "${cluster_id}" =~ ^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]]; then
  echo "ERROR: --cluster-id must be an RFC 1123 label" >&2
  exit 2
fi
if [ -z "${cluster_name}" ] || [ -z "${output_dir}" ]; then
  echo "ERROR: --cluster-name and --output-dir are required" >&2
  exit 2
fi
case "${environment}" in
  production|staging|development|sandbox) ;;
  *) echo "ERROR: --environment must be production, staging, development, or sandbox" >&2; exit 2 ;;
esac
case "${provider}" in
  kakao-cloud|aws|gcp|azure|on-prem|other) ;;
  *) echo "ERROR: unsupported --provider: ${provider}" >&2; exit 2 ;;
esac

umask 077
mkdir -p "${output_dir}"
chmod 700 "${output_dir}"

if ! kubectl get serviceaccount "${SA_NAME}" -n "${SA_NAMESPACE}" >/dev/null; then
  echo "ERROR: ${SA_NAMESPACE}/${SA_NAME} ServiceAccount is missing; apply the Portal GitOps RBAC first" >&2
  exit 1
fi

binding_json="$(kubectl get clusterrolebinding "${CLUSTER_ROLE_BINDING}" -o json)"
if ! BINDING_JSON="${binding_json}" python3 - <<'PYEOF'
import json
import os
import sys

binding = json.loads(os.environ["BINDING_JSON"])
role = binding.get("roleRef", {})
subjects = binding.get("subjects", [])
expected = {"kind": "ServiceAccount", "name": "narwhal-portal", "namespace": "devtools"}
if role.get("kind") != "ClusterRole" or role.get("name") != "narwhal-portal" or expected not in subjects:
    sys.exit(1)
PYEOF
then
  echo "ERROR: ClusterRoleBinding narwhal-portal does not bind devtools/narwhal-portal to ClusterRole narwhal-portal" >&2
  exit 1
fi

api_server="$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.server}')"
ca_bundle="$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
ca_path="$(kubectl config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority}')"
if [ -z "${api_server}" ] || { [ -z "${ca_bundle}" ] && [ -z "${ca_path}" ]; }; then
  echo "ERROR: active kubeconfig must contain an API server and CA data or certificate-authority path" >&2
  exit 1
fi

token="$(kubectl create token "${SA_NAME}" -n "${SA_NAMESPACE}" --duration="${TOKEN_DURATION}")"
if [ -z "${token}" ]; then
  echo "ERROR: TokenRequest returned an empty token" >&2
  exit 1
fi

ca_file="$(mktemp "${output_dir}/.ca.XXXXXX")"
trap 'rm -f "${ca_file}"; unset token' EXIT
if [ -n "${ca_bundle}" ]; then
  CA_BUNDLE="${ca_bundle}" CA_FILE="${ca_file}" python3 - <<'PYEOF'
import base64
import os

with open(os.environ["CA_FILE"], "wb") as output:
    output.write(base64.b64decode(os.environ["CA_BUNDLE"], validate=True))
PYEOF
else
  if [ ! -f "${ca_path}" ] || [ ! -r "${ca_path}" ]; then
    echo "ERROR: certificate-authority path is not a readable regular file: ${ca_path}" >&2
    exit 1
  fi
  cp "${ca_path}" "${ca_file}"
  ca_bundle="$(base64 < "${ca_file}" | tr -d '\n')"
fi

env_suffix="${cluster_id^^}"
env_suffix="${env_suffix//-/_}"
api_server_var="K8S_${env_suffix}_API_SERVER"
token_var="K8S_${env_suffix}_SA_TOKEN"
ca_bundle_var="K8S_${env_suffix}_CA_BUNDLE"

REGISTRATION_PATH="${output_dir}/registration.json" \
CLUSTER_ID="${cluster_id}" \
CLUSTER_NAME="${cluster_name}" \
ENVIRONMENT="${environment}" \
PROVIDER="${provider}" \
REGION="${region}" \
API_SERVER_VAR="${api_server_var}" \
TOKEN_VAR="${token_var}" \
python3 - <<'PYEOF'
import json
import os

region = os.environ["REGION"] or None
registration = {
    "id": os.environ["CLUSTER_ID"],
    "name": os.environ["CLUSTER_NAME"],
    "environment": os.environ["ENVIRONMENT"],
    "provider": os.environ["PROVIDER"],
    "region": region,
    "endpointHint": "API endpoint exported from the selected kubeconfig",
    "credentialRef": {
        "apiServerEnvVar": os.environ["API_SERVER_VAR"],
        "tokenEnvVar": os.environ["TOKEN_VAR"],
    },
    "capabilities": {},
}
with open(os.environ["REGISTRATION_PATH"], "w", encoding="utf-8") as output:
    json.dump(registration, output, indent=2)
    output.write("\n")
PYEOF
chmod 644 "${output_dir}/registration.json"

printf '%s=%q\n%s=%q\n%s=%q\n' \
  "${api_server_var}" "${api_server}" \
  "${token_var}" "${token}" \
  "${ca_bundle_var}" "${ca_bundle}" > "${output_dir}/credentials.env"
chmod 600 "${output_dir}/credentials.env"

kubectl config set-cluster "${cluster_id}" \
  --server="${api_server}" \
  --certificate-authority="${ca_file}" \
  --embed-certs=true \
  --kubeconfig="${output_dir}/kubeconfig" >/dev/null
kubectl config set-credentials "narwhal-portal" \
  --token="${token}" \
  --kubeconfig="${output_dir}/kubeconfig" >/dev/null
kubectl config set-context "${cluster_id}" \
  --cluster="${cluster_id}" \
  --user="narwhal-portal" \
  --kubeconfig="${output_dir}/kubeconfig" >/dev/null
kubectl config use-context "${cluster_id}" --kubeconfig="${output_dir}/kubeconfig" >/dev/null
chmod 600 "${output_dir}/kubeconfig"

echo "Exported non-secret registration metadata: ${output_dir}/registration.json"
echo "Exported protected credentials for cluster ID '${cluster_id}'."
echo "Deliver API-server/token values through the Portal secret store; rotate this 24h TokenRequest before expiry."
echo "Do not commit this directory."
