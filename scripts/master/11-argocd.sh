#!/bin/bash
set -euo pipefail

ARGOCD_VERSION="${ARGOCD_VERSION:-v3.3.0}"

# Keycloak OIDC configuration
# K8s 1.35+ requires HTTPS for OIDC issuer URL
KEYCLOAK_URL="${KEYCLOAK_URL:-https://keycloak.local.narwhal.io}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-kubernetes}"

echo "=== Installing ArgoCD ${ARGOCD_VERSION} ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Wait for API server to be reachable (may restart under memory pressure)
echo "Waiting for API server..."
for i in {1..30}; do
  if kubectl get nodes &>/dev/null; then
    break
  fi
  echo "API server not ready, retrying... (${i}/30)"
  sleep 10
done

# Create namespace
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

# Install ArgoCD (server-side apply for large CRDs like applicationsets)
kubectl apply -n argocd -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml" \
  --server-side --force-conflicts

# Wait for ArgoCD pods
echo "Waiting for ArgoCD pods..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s || true

# Configure ArgoCD server params (v3.x reads from cmd-params-cm, not argocd-cm)
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cmd-params-cm
  namespace: argocd
  labels:
    app.kubernetes.io/name: argocd-cmd-params-cm
    app.kubernetes.io/part-of: argocd
data:
  server.insecure: "true"
EOF

# Configure ArgoCD for Keycloak OIDC
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cm
  namespace: argocd
  labels:
    app.kubernetes.io/name: argocd-cm
    app.kubernetes.io/part-of: argocd
data:
  url: https://argocd.local.narwhal.io
  server.insecure: "true"
  oidc.tls.insecure.skip.verify: "true"
  oidc.config: |
    name: Keycloak
    issuer: ${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}
    clientID: argocd
    clientSecret: argocd-secret
    requestedScopes: ["openid", "profile", "email", "groups"]
EOF

# Configure RBAC for Keycloak groups
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-rbac-cm
  namespace: argocd
  labels:
    app.kubernetes.io/name: argocd-rbac-cm
    app.kubernetes.io/part-of: argocd
data:
  policy.default: role:readonly
  policy.csv: |
    g, cluster-admins, role:admin
    g, developers, role:developer
  scopes: '[groups]'
EOF

# Restart ArgoCD server to apply OIDC config
kubectl rollout restart deployment argocd-server -n argocd
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s || true

# Get initial admin password
ARGOCD_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d 2>/dev/null || echo "unknown")

echo "=== ArgoCD Installation Done ==="

echo ""
echo "=========================================="
echo "ArgoCD Ready!"
echo "=========================================="
echo ""
echo "Access:"
echo "  kubectl port-forward svc/argocd-server -n argocd 8443:443"
echo "  URL: https://localhost:8443"
echo "  User: admin"
echo "  Password: ${ARGOCD_PASSWORD}"
echo ""
echo "OIDC Login: Use Keycloak credentials (k8s-admin/k8s-admin)"
echo ""
kubectl get pods -n argocd
