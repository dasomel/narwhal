#!/usr/bin/env bash
set -euo pipefail

#=========================================
# Narwhal Cluster kubeconfig Setup
#=========================================
# This script configures local kubeconfig to access the narwhal cluster
# Supports: Certificate-based (admin) and OIDC (user) authentication

CLUSTER_NAME="${CLUSTER_NAME:-narwhal}"
MASTER_IP="${MASTER_IP:-192.168.56.10}"
API_SERVER="https://${MASTER_IP}:6443"

# Auth method: "cert" (default) or "oidc"
AUTH_METHOD="${1:-cert}"

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
    CONFIG=$(vagrant ssh master -c "cat ~/.kube/config" 2>/dev/null)

    # Extract certificates
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > /tmp/narwhal-ca.crt
    echo "$CONFIG" | awk '/client-certificate-data:/ {print $2}' | base64 -d > /tmp/narwhal-client.crt
    echo "$CONFIG" | awk '/client-key-data:/ {print $2}' | base64 -d > /tmp/narwhal-client.key

    # Set cluster
    kubectl config set-cluster ${CLUSTER_NAME} \
      --server="${API_SERVER}" \
      --certificate-authority=/tmp/narwhal-ca.crt \
      --embed-certs=true

    # Set credentials (certificate-based)
    kubectl config set-credentials ${CLUSTER_NAME}-admin \
      --client-certificate=/tmp/narwhal-client.crt \
      --client-key=/tmp/narwhal-client.key \
      --embed-certs=true

    # Set context
    kubectl config set-context ${CLUSTER_NAME} \
      --cluster=${CLUSTER_NAME} \
      --user=${CLUSTER_NAME}-admin

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
    OIDC_ISSUER_URL="${OIDC_ISSUER_URL:-http://${MASTER_IP}:8080/realms/kubernetes}"
    OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-kubernetes}"
    OIDC_USERNAME="${OIDC_USERNAME:-}"
    OIDC_PASSWORD="${OIDC_PASSWORD:-}"

    # Get CA certificate from master
    CONFIG=$(vagrant ssh master -c "cat ~/.kube/config" 2>/dev/null)
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > /tmp/narwhal-ca.crt

    # Set cluster
    kubectl config set-cluster ${CLUSTER_NAME} \
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
    kubectl config set-credentials ${CLUSTER_NAME}-oidc \
      --exec-api-version=client.authentication.k8s.io/v1beta1 \
      --exec-command=kubectl \
      --exec-arg=oidc-login \
      --exec-arg=get-token \
      --exec-arg=--oidc-issuer-url=${OIDC_ISSUER_URL} \
      --exec-arg=--oidc-client-id=${OIDC_CLIENT_ID}

    # Set context
    kubectl config set-context ${CLUSTER_NAME}-oidc \
      --cluster=${CLUSTER_NAME} \
      --user=${CLUSTER_NAME}-oidc

    # Cleanup
    rm -f /tmp/narwhal-ca.crt

    echo ""
    echo "OIDC authentication configured."
    echo "Use 'kubectl config use-context ${CLUSTER_NAME}-oidc' to use OIDC auth."
    echo ""
    echo "Available users (Keycloak):"
    echo "  - k8s-admin / k8s-admin (cluster-admin)"
    echo "  - developer / developer (edit)"
    ;;

  token)
    #=========================================
    # Service Account Token Authentication
    #=========================================
    echo "Setting up service account token authentication..."

    # Get CA certificate from master
    CONFIG=$(vagrant ssh master -c "cat ~/.kube/config" 2>/dev/null)
    echo "$CONFIG" | awk '/certificate-authority-data:/ {print $2}' | base64 -d > /tmp/narwhal-ca.crt

    # Get or create service account token
    SA_NAME="${SA_NAME:-admin-user}"
    SA_NAMESPACE="${SA_NAMESPACE:-kube-system}"

    # Create service account and get token
    TOKEN=$(vagrant ssh master -c "
      kubectl create serviceaccount ${SA_NAME} -n ${SA_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f - > /dev/null 2>&1
      kubectl create clusterrolebinding ${SA_NAME}-binding --clusterrole=cluster-admin --serviceaccount=${SA_NAMESPACE}:${SA_NAME} --dry-run=client -o yaml | kubectl apply -f - > /dev/null 2>&1
      kubectl create token ${SA_NAME} -n ${SA_NAMESPACE} --duration=8760h
    " 2>/dev/null)

    # Set cluster
    kubectl config set-cluster ${CLUSTER_NAME} \
      --server="${API_SERVER}" \
      --certificate-authority=/tmp/narwhal-ca.crt \
      --embed-certs=true

    # Set credentials (token-based)
    kubectl config set-credentials ${CLUSTER_NAME}-token \
      --token="${TOKEN}"

    # Set context
    kubectl config set-context ${CLUSTER_NAME}-token \
      --cluster=${CLUSTER_NAME} \
      --user=${CLUSTER_NAME}-token

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
kubectl config use-context ${CLUSTER_NAME}

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
