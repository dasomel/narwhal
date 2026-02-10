#!/bin/bash
set -euo pipefail

ARGOCD_VERSION="${ARGOCD_VERSION:-v3.3.0}"

# Keycloak OIDC configuration
KEYCLOAK_URL="${KEYCLOAK_URL:-http://keycloak-service.keycloak.svc.cluster.local:8080}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-kubernetes}"

echo "=== Installing ArgoCD ${ARGOCD_VERSION} ==="

export KUBECONFIG=/home/vagrant/.kube/config

# Create namespace
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

# Install ArgoCD
kubectl apply -n argocd -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"

# Wait for ArgoCD pods
echo "Waiting for ArgoCD pods..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s

# Configure ArgoCD for Keycloak OIDC and insecure mode (HTTP)
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
  url: http://argocd.local.narwhal.io
  # Disable HTTPS redirect for HTTP access via Traefik
  server.insecure: "true"
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
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s

# Get initial admin password
ARGOCD_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)

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
