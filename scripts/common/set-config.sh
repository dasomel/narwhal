#!/usr/bin/env bash
set -euo pipefail

#=========================================
# Narwhal Cluster kubeconfig Setup
#=========================================
# This script configures local kubeconfig to access the narwhal cluster
# Supports: Certificate-based (admin) and OIDC (user) authentication

CLUSTER_NAME="${CLUSTER_NAME:-narwhal}"
MASTER_IP="${MASTER_IP:-192.168.56.10}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
API_SERVER="https://${VIP_ADDRESS}:6443"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

# Auth method: "cert" (default) or "oidc"
AUTH_METHOD="${1:-cert}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }

#=========================================
# Run a command on master-1 and print its stdout.
#
# Ordered on purpose. Plain ssh to MASTER_IP rides the SAME host-only network
# kubectl will use, so a successful fetch proves the context this script writes
# is actually reachable. `vagrant ssh` instead rides the VMware NAT network,
# which has handed two guests the same DHCP lease (2026-07-29: master-1 and
# worker-2 both held 172.16.221.133) — it then authenticates against the wrong
# guest and fails, or succeeds while kubectl still cannot reach the API server.
# Keep it only as a fallback.
#
# stderr is captured separately, never merged: `vagrant ssh -c` writes
# "Connection to ... closed." to stderr, and folding that into the output would
# corrupt the kubeconfig this returns.
#=========================================
master_exec() {
  local remote_cmd="$1" key out err
  err=$(mktemp)

  key=$(ls "${REPO_ROOT}"/.vagrant/machines/master-1/*/private_key 2>/dev/null | head -1 || true)
  if [[ -n "${key}" ]]; then
    if out=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR \
               -i "${key}" "vagrant@${MASTER_IP}" "${remote_cmd}" 2>"${err}"); then
      rm -f "${err}"
      printf '%s\n' "${out}"
      return 0
    fi
    echo "WARN: ssh to ${MASTER_IP} failed: $(tr '\n' ' ' <"${err}")" >&2
    echo "WARN: falling back to 'vagrant ssh'" >&2
  fi

  if out=$(cd "${REPO_ROOT}" && vagrant ssh master-1 -c "${remote_cmd}" </dev/null 2>"${err}"); then
    rm -f "${err}"
    printf '%s\n' "${out}"
    return 0
  fi

  local reason; reason=$(tr '\n' ' ' <"${err}"); rm -f "${err}"
  die "could not run '${remote_cmd}' on master-1: ${reason}"
}

#=========================================
# Refuse to write a context that cannot work.
#
# Both failure modes below used to surface as this script printing its banner
# and exiting silently: the `vagrant ssh` call was suppressed with 2>/dev/null,
# and `set -e` killed the script before it reached `kubectl config use-context`,
# so a stale context stayed active and the run looked like it had succeeded.
#=========================================
preflight() {
  local state
  state=$(cd "${REPO_ROOT}" && vagrant status master-1 2>/dev/null | awk '$1=="master-1"{print $2}')
  [[ "${state}" == "running" ]] \
    || die "master-1 is '${state:-unknown}', not running. Start the cluster first: (cd ${REPO_ROOT} && vagrant up)"

  # kubectl reaches the API server over the host-only network, so verify that
  # path from THIS machine. curl returns 0 on any HTTP response; we only care
  # that TCP + TLS complete.
  if ! curl -sk --max-time 5 -o /dev/null "${API_SERVER}/livez"; then
    echo "ERROR: ${API_SERVER} is unreachable from this machine, but master-1 is running." >&2
    echo "       On macOS this is usually Local Network Privacy blocking the terminal app:" >&2
    echo "       System Settings > Privacy & Security > Local Network — enable the app running" >&2
    echo "       this script, then retry. Confirm with: ping ${MASTER_IP}" >&2
    exit 1
  fi
}

preflight

echo "=== Narwhal Cluster kubeconfig Setup ==="
echo "Cluster: ${CLUSTER_NAME}"
echo "API Server: ${API_SERVER}"
echo "Auth Method: ${AUTH_METHOD}"
echo ""

case "${AUTH_METHOD}" in
  cert)
    #=========================================
    # Certificate-based Authentication (Admin)
    #=========================================
    echo "Setting up certificate-based authentication..."

    # Get kubeconfig from master node
    CONFIG=$(master_exec "cat ~/.kube/config")

    # Extract certificates
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > /tmp/narwhal-ca.crt
    echo "$CONFIG" | awk '/client-certificate-data:/ {print $2}' | base64 -d > /tmp/narwhal-client.crt
    echo "$CONFIG" | awk '/client-key-data:/ {print $2}' | base64 -d > /tmp/narwhal-client.key

    # Set cluster
    kubectl config set-cluster "${CLUSTER_NAME}" \
      --server="${API_SERVER}" \
      --certificate-authority=/tmp/narwhal-ca.crt \
      --embed-certs=true

    # Set credentials (certificate-based)
    kubectl config set-credentials "${CLUSTER_NAME}-admin" \
      --client-certificate=/tmp/narwhal-client.crt \
      --client-key=/tmp/narwhal-client.key \
      --embed-certs=true

    # Set context
    kubectl config set-context "${CLUSTER_NAME}" \
      --cluster="${CLUSTER_NAME}" \
      --user="${CLUSTER_NAME}-admin"

    # Cleanup temp files
    rm -f /tmp/narwhal-ca.crt /tmp/narwhal-client.crt /tmp/narwhal-client.key

    echo "Certificate-based authentication configured."
    ;;

  oidc)
    #=========================================
    # OIDC Authentication (Keycloak)
    #=========================================
    echo "Setting up OIDC authentication with Keycloak..."

    # OIDC Configuration
    # K8s 1.35+ requires HTTPS for OIDC issuer URL
    OIDC_ISSUER_URL="${OIDC_ISSUER_URL:-https://keycloak.${DOMAIN}/realms/kubernetes}"
    OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-kubernetes}"
    OIDC_USERNAME="${OIDC_USERNAME:-}"
    OIDC_PASSWORD="${OIDC_PASSWORD:-}"

    # Get CA certificate from master
    CONFIG=$(master_exec "cat ~/.kube/config")
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > /tmp/narwhal-ca.crt

    # Set cluster
    kubectl config set-cluster "${CLUSTER_NAME}" \
      --server="${API_SERVER}" \
      --certificate-authority=/tmp/narwhal-ca.crt \
      --embed-certs=true

    # Check if kubelogin (oidc-login) is installed
    if ! command -v kubectl-oidc_login &> /dev/null; then
      echo ""
      echo "WARNING: kubectl-oidc_login not found."
      echo "Install it with: brew install int128/kubelogin/kubelogin"
      echo "Or: go install github.com/int128/kubelogin/cmd/kubelogin@latest"
      echo ""
    fi

    # Set credentials (OIDC)
    kubectl config set-credentials "${CLUSTER_NAME}-oidc" \
      --exec-api-version=client.authentication.k8s.io/v1beta1 \
      --exec-command=kubectl \
      --exec-arg=oidc-login \
      --exec-arg=get-token \
      --exec-arg="--oidc-issuer-url=${OIDC_ISSUER_URL}" \
      --exec-arg="--oidc-client-id=${OIDC_CLIENT_ID}"

    # Set context
    kubectl config set-context "${CLUSTER_NAME}-oidc" \
      --cluster="${CLUSTER_NAME}" \
      --user="${CLUSTER_NAME}-oidc"

    # Cleanup
    rm -f /tmp/narwhal-ca.crt

    echo ""
    echo "OIDC authentication configured."
    echo "Use 'kubectl config use-context ${CLUSTER_NAME}-oidc' to use OIDC auth."
    echo ""
    echo "Available users (Keycloak):"
    echo "  - admin / admin (cluster-admin)"
    echo "  - dev / dev (developer)"
    echo "  - view / view (viewer)"
    echo "  - guest / guest (guest - web UI only)"
    ;;

  token)
    #=========================================
    # Service Account Token Authentication
    #=========================================
    echo "Setting up service account token authentication..."

    # Get CA certificate from master
    CONFIG=$(master_exec "cat ~/.kube/config")
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > /tmp/narwhal-ca.crt

    # Get or create service account token
    SA_NAME="${SA_NAME:-admin-user}"
    SA_NAMESPACE="${SA_NAMESPACE:-kube-system}"

    # Create service account and get token
    TOKEN=$(master_exec "
      kubectl create serviceaccount ${SA_NAME} -n ${SA_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f - > /dev/null 2>&1
      kubectl create clusterrolebinding ${SA_NAME}-binding --clusterrole=cluster-admin --serviceaccount=${SA_NAMESPACE}:${SA_NAME} --dry-run=client -o yaml | kubectl apply -f - > /dev/null 2>&1
      kubectl create token ${SA_NAME} -n ${SA_NAMESPACE} --duration=8760h
    ")

    # Set cluster
    kubectl config set-cluster "${CLUSTER_NAME}" \
      --server="${API_SERVER}" \
      --certificate-authority=/tmp/narwhal-ca.crt \
      --embed-certs=true

    # Set credentials (token-based)
    kubectl config set-credentials "${CLUSTER_NAME}-token" \
      --token="${TOKEN}"

    # Set context
    kubectl config set-context "${CLUSTER_NAME}-token" \
      --cluster="${CLUSTER_NAME}" \
      --user="${CLUSTER_NAME}-token"

    # Cleanup
    rm -f /tmp/narwhal-ca.crt

    echo "Token-based authentication configured."
    echo "Token valid for 1 year."
    ;;

  *)
    echo "Unknown auth method: ${AUTH_METHOD}"
    echo "Usage: $0 [cert|oidc|token]"
    echo ""
    echo "  cert  - Certificate-based admin authentication (default)"
    echo "  oidc  - OIDC authentication via Keycloak"
    echo "  token - Service account token authentication"
    exit 1
    ;;
esac

# Activate context
case "${AUTH_METHOD}" in
  cert)  CONTEXT_NAME="${CLUSTER_NAME}" ;;
  oidc)  CONTEXT_NAME="${CLUSTER_NAME}-oidc" ;;
  token) CONTEXT_NAME="${CLUSTER_NAME}-token" ;;
esac
kubectl config use-context "${CONTEXT_NAME}"

# Flush DNS cache (macOS)
if [[ "$(uname)" == "Darwin" ]]; then
  sudo dscacheutil -flushcache 2>/dev/null || true
  sudo killall -HUP mDNSResponder 2>/dev/null || true
fi

echo ""
echo "=== Configuration Complete ==="
echo "Current Context: $(kubectl config current-context)"
echo ""
echo "Test connection:"
kubectl get nodes 2>/dev/null || echo "  (Connection test failed - is the cluster running?)"
