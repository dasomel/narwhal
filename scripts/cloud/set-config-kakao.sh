#!/usr/bin/env bash
set -euo pipefail

#=========================================
# Narwhal on Kakao Cloud — local kubeconfig setup
#=========================================
# The cloud counterpart of scripts/common/set-config.sh. Same three auth methods
# (cert | oidc | token), same kubectl config layout — three things differ:
#
#   1. Addresses come from OpenTofu state, not hardcoded 192.168.56.x.
#   2. Nodes are private, so everything goes over the bastion. `vagrant ssh master-1`
#      becomes an ssh through the jump host.
#   3. The API server is NOT reachable at its VIP or the LB's public IP. The apiserver
#      certificate is issued for localhost / 127.0.0.1 / the master private IPs / the
#      private VIP — the LB's public address is not in it, so connecting there fails
#      TLS verification. This script opens an ssh tunnel and points kubeconfig at
#      127.0.0.1, which the certificate does cover. Disabling TLS verification instead
#      would be wrong; add the public IP to certSANs in 02-init-cluster.sh if you want
#      to reach it directly.
#
# The context is named narwhal-kakao* so it never collides with a local Vagrant one.
#
# Usage (from the repo root or anywhere):
#   scripts/cloud/set-config-kakao.sh            # cert (default)
#   scripts/cloud/set-config-kakao.sh oidc
#   scripts/cloud/set-config-kakao.sh token
#
#   PORT=7443 scripts/cloud/set-config-kakao.sh  # if 6443 is taken locally

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
CLUSTER_NAME="${CLUSTER_NAME:-narwhal-kakao}"
DOMAIN="${DOMAIN:-kakao.narwhal.internal}"
PORT="${PORT:-6443}"
AUTH_METHOD="${1:-cert}"

cd "$(dirname "$0")/../.."
[ -d "${TF_DIR}" ] || { echo "ERROR: ${TF_DIR} not found — run from the narwhal repo" >&2; exit 1; }

#=========================================
# Endpoints from OpenTofu state
#=========================================
BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
MASTER1=$(cd "${TF_DIR}" && tofu output -json master_private_ips \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0])')
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY} — has tofu apply finished?" >&2; exit 1; }

API_SERVER="https://127.0.0.1:${PORT}"

# ProxyCommand, not -J: `ssh -J` spawns its own process for the jump host and does not
# inherit the command line's -i, so the bastion hop fails with Permission denied.
ssh_master() {
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=25 \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${MASTER1}" "$@"
}

echo "=== Narwhal (Kakao Cloud) kubeconfig Setup ==="
echo "Cluster:     ${CLUSTER_NAME}"
echo "Bastion:     ${BASTION_IP}"
echo "master-1:    ${MASTER1}"
echo "API Server:  ${API_SERVER}  (ssh tunnel)"
echo "Auth Method: ${AUTH_METHOD}"
echo ""

#=========================================
# Tunnel — reuse if already listening
#=========================================
TUNNEL_PATTERN="${PORT}:127.0.0.1:6443"
if nc -z 127.0.0.1 "${PORT}" 2>/dev/null; then
  if pgrep -f "${TUNNEL_PATTERN}" >/dev/null 2>&1; then
    echo "Tunnel already open on ${PORT}, reusing."
  else
    echo "ERROR: port ${PORT} is in use by something that is not our tunnel." >&2
    echo "       Re-run with PORT=<free port>." >&2
    exit 1
  fi
else
  echo "Opening tunnel localhost:${PORT} -> ${MASTER1}:6443 via bastion..."

  # Replacing the instances gives every node a new host key, and `accept-new` takes a NEW
  # key but refuses a CHANGED one. Combined with `ssh -f ... >/dev/null 2>&1` below and
  # `set -e`, that produced a bare exit 255 with the warning swallowed — the one failure
  # here that is guaranteed to happen after a rebuild left no evidence at all.
  # The nodes are reachable only through the bastion on a private subnet, so dropping the
  # stale entries is the correct response rather than a shortcut.
  for _h in "${MASTER1}" "${BASTION_IP}"; do
    if ssh-keygen -F "${_h}" >/dev/null 2>&1; then
      if ! ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
           -i "${SSH_KEY}" "${SSH_USER}@${BASTION_IP}" true >/dev/null 2>&1 \
         || [ "${_h}" = "${MASTER1}" ]; then
        ssh-keygen -R "${_h}" >/dev/null 2>&1 || true
      fi
    fi
  done
  # >/dev/null 2>&1 is not cosmetic: `ssh -f` backgrounds itself but keeps whatever fds
  # it inherited, so with the script's output piped anywhere the reader never sees EOF
  # and the whole invocation appears to hang long after the work is done.
  ssh -f -N -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes \
    -L "${TUNNEL_PATTERN}" \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${MASTER1}" >/dev/null 2>"${TUNNEL_ERR:=/tmp/narwhal-tunnel.err}" || {
    echo "ERROR: could not open the tunnel. ssh said:" >&2
    sed 's/^/  /' "${TUNNEL_ERR}" >&2
    exit 1
  }
  for _ in $(seq 1 20); do
    nc -z 127.0.0.1 "${PORT}" 2>/dev/null && break
    sleep 1
  done
  nc -z 127.0.0.1 "${PORT}" 2>/dev/null || { echo "ERROR: tunnel did not come up on ${PORT}" >&2; exit 1; }
fi

# admin.conf is root-owned on the node, so read it under sudo rather than scp.
fetch_admin_conf() {
  ssh_master 'sudo cat /etc/kubernetes/admin.conf'
}

CA_FILE=$(mktemp); CRT_FILE=$(mktemp); KEY_FILE=$(mktemp)
cleanup() { rm -f "${CA_FILE}" "${CRT_FILE}" "${KEY_FILE}"; }
trap cleanup EXIT

case "${AUTH_METHOD}" in
  cert)
    #=========================================
    # Certificate-based Authentication (Admin)
    #=========================================
    echo "Setting up certificate-based authentication..."
    CONFIG=$(fetch_admin_conf)
    [ -n "${CONFIG}" ] || { echo "ERROR: could not read admin.conf from ${MASTER1}" >&2; exit 1; }

    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > "${CA_FILE}"
    echo "$CONFIG" | awk '/client-certificate-data:/ {print $2}'   | base64 -d > "${CRT_FILE}"
    echo "$CONFIG" | awk '/client-key-data:/ {print $2}'           | base64 -d > "${KEY_FILE}"

    kubectl config set-cluster "${CLUSTER_NAME}" \
      --server="${API_SERVER}" \
      --certificate-authority="${CA_FILE}" \
      --embed-certs=true

    kubectl config set-credentials "${CLUSTER_NAME}-admin" \
      --client-certificate="${CRT_FILE}" \
      --client-key="${KEY_FILE}" \
      --embed-certs=true

    kubectl config set-context "${CLUSTER_NAME}" \
      --cluster="${CLUSTER_NAME}" \
      --user="${CLUSTER_NAME}-admin"

    echo "Certificate-based authentication configured."
    ;;

  oidc)
    #=========================================
    # OIDC Authentication (Keycloak)
    #=========================================
    echo "Setting up OIDC authentication with Keycloak..."
    OIDC_ISSUER_URL="${OIDC_ISSUER_URL:-https://keycloak.${DOMAIN}/realms/narwhal}"
    OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-kubernetes}"

    CONFIG=$(fetch_admin_conf)
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > "${CA_FILE}"

    kubectl config set-cluster "${CLUSTER_NAME}" \
      --server="${API_SERVER}" \
      --certificate-authority="${CA_FILE}" \
      --embed-certs=true

    if ! command -v kubectl-oidc_login >/dev/null 2>&1; then
      echo ""
      echo "WARNING: kubectl-oidc_login not found."
      echo "Install it with: brew install int128/kubelogin/kubelogin"
      echo ""
    fi

    kubectl config set-credentials "${CLUSTER_NAME}-oidc" \
      --exec-api-version=client.authentication.k8s.io/v1beta1 \
      --exec-command=kubectl \
      --exec-arg=oidc-login \
      --exec-arg=get-token \
      --exec-arg="--oidc-issuer-url=${OIDC_ISSUER_URL}" \
      --exec-arg="--oidc-client-id=${OIDC_CLIENT_ID}"

    kubectl config set-context "${CLUSTER_NAME}-oidc" \
      --cluster="${CLUSTER_NAME}" \
      --user="${CLUSTER_NAME}-oidc"

    WORKER_LB=$(cd "${TF_DIR}" && tofu output -raw worker_lb_public_ip)
    echo ""
    echo "OIDC authentication configured."
    echo ""
    echo "The browser flow needs ${DOMAIN} to resolve. These names are served by the"
    echo "worker LB and have no public DNS, so add them to /etc/hosts first:"
    echo ""
    echo "  ${WORKER_LB}  keycloak.${DOMAIN}"
    echo ""
    echo "Keycloak realm users (passwords: scripts/test/show-credentials.sh):"
    echo "  admin (cluster-admin) · dev (developer) · view (viewer) · guest (web UI only)"
    ;;

  token)
    #=========================================
    # Service Account Token Authentication
    #=========================================
    echo "Setting up service account token authentication..."
    SA_NAME="${SA_NAME:-admin-user}"
    SA_NAMESPACE="${SA_NAMESPACE:-kube-system}"

    CONFIG=$(fetch_admin_conf)
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > "${CA_FILE}"

    # KUBECONFIG must be passed explicitly: sudo -E is ignored on these images
    # ("preserving the entire environment is not supported").
    TOKEN=$(ssh_master "sudo env KUBECONFIG=/home/vagrant/.kube/config-local sh -c '
      kubectl create serviceaccount ${SA_NAME} -n ${SA_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1
      kubectl create clusterrolebinding ${SA_NAME}-binding --clusterrole=cluster-admin --serviceaccount=${SA_NAMESPACE}:${SA_NAME} --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1
      kubectl create token ${SA_NAME} -n ${SA_NAMESPACE} --duration=8760h
    '" | tr -d '\r')
    [ -n "${TOKEN}" ] || { echo "ERROR: failed to mint a service account token" >&2; exit 1; }

    kubectl config set-cluster "${CLUSTER_NAME}" \
      --server="${API_SERVER}" \
      --certificate-authority="${CA_FILE}" \
      --embed-certs=true

    kubectl config set-credentials "${CLUSTER_NAME}-token" --token="${TOKEN}"

    kubectl config set-context "${CLUSTER_NAME}-token" \
      --cluster="${CLUSTER_NAME}" \
      --user="${CLUSTER_NAME}-token"

    echo "Token-based authentication configured. Token valid for 1 year."
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

# Keep in sync with the same block in scripts/common/set-config.sh — both write
# current-context into the shared ~/.kube/config, so either one silently
# redirects kubectl for every other shell on this machine. The two clusters'
# contexts differ only by a suffix (narwhal vs narwhal-kakao), which is how a
# `kubectl get nodes` on 2026-08-02 returned this cluster's nodes to someone
# reading them as the local Vagrant cluster's. Name both ends of the move.
case "${AUTH_METHOD}" in
  cert)  CONTEXT_NAME="${CLUSTER_NAME}" ;;
  oidc)  CONTEXT_NAME="${CLUSTER_NAME}-oidc" ;;
  token) CONTEXT_NAME="${CLUSTER_NAME}-token" ;;
esac
# Compare SERVERS, not context names: `narwhal-kakao` starts with `narwhal`, so
# a name-prefix test reads the one move that matters — local Vagrant to Kakao —
# as a routine change of auth method on the same cluster.
context_server() {
  local ctx="$1" cluster
  cluster=$(kubectl config view -o jsonpath="{.contexts[?(@.name==\"${ctx}\")].context.cluster}" 2>/dev/null || true)
  [[ -z "${cluster}" ]] && return 0
  kubectl config view -o jsonpath="{.clusters[?(@.name==\"${cluster}\")].cluster.server}" 2>/dev/null || true
}
PREV_CONTEXT="$(kubectl config current-context 2>/dev/null || true)"
PREV_SERVER="$(context_server "${PREV_CONTEXT}")"
kubectl config use-context "${CONTEXT_NAME}"
if [[ -n "${PREV_CONTEXT}" && "${PREV_CONTEXT}" != "${CONTEXT_NAME}" ]]; then
  echo "Context switched: ${PREV_CONTEXT} -> ${CONTEXT_NAME}"
  if [[ -n "${PREV_SERVER}" && "${PREV_SERVER}" != "${API_SERVER}" ]]; then
    echo "  NOTE: '${PREV_CONTEXT}' points at a DIFFERENT cluster (${PREV_SERVER}),"
    echo "        and this changed current-context for every shell using ~/.kube/config —"
    echo "        not just this one. If another session or terminal was working"
    echo "        '${PREV_CONTEXT}', its kubectl now points here instead."
    echo "        Pass --context explicitly to stop depending on this shared setting:"
    echo "          kubectl --context ${PREV_CONTEXT} get nodes"
  fi
fi

echo ""
echo "=== Configuration Complete ==="
echo "Current Context: $(kubectl config current-context)"
echo ""
echo "The tunnel holds this context together — kubectl stops working when it closes."
echo "  reopen: $0 ${AUTH_METHOD}"
echo "  close:  kill \$(pgrep -f 'ssh -f -N.*${TUNNEL_PATTERN}')"
echo ""
echo "Do not close it with pkill -f '${TUNNEL_PATTERN}' from inside a shell command that"
echo "also contains that string — pkill matches the surrounding command line and kills it."
echo ""
echo "Test connection:"
kubectl get nodes 2>/dev/null || echo "  (Connection test failed — is the cluster running?)"
