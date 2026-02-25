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

# Create namespace with Istio ambient mesh label
kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace devtools istio.io/dataplane-mode=ambient --overwrite

# Install ArgoCD (server-side apply for large CRDs like applicationsets)
kubectl apply -n devtools -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml" \
  --server-side --force-conflicts

# Fix ClusterRoleBindings: ArgoCD is installed in devtools, not argocd namespace
# The upstream manifests always use 'argocd' as Subject namespace - must be patched
echo "Fixing ClusterRoleBinding Subject namespaces (argocd -> devtools)..."
for crb in argocd-application-controller argocd-applicationset-controller argocd-server; do
  kubectl patch clusterrolebinding "${crb}" --type='json' \
    -p='[{"op": "replace", "path": "/subjects/0/namespace", "value": "devtools"}]' 2>/dev/null || true
done

# Patch ArgoCD NetworkPolicies for Istio ambient mesh (HBONE port 15008)
echo "Patching ArgoCD NetworkPolicies for Istio ambient mesh (HBONE port 15008)..."
for np in $(kubectl get networkpolicy -n devtools -o name 2>/dev/null | grep argocd); do
  kubectl patch "$np" -n devtools --type='json' \
    -p='[{"op": "add", "path": "/spec/ingress/0/ports/-", "value": {"port": 15008, "protocol": "TCP"}}]' 2>/dev/null || true
done

# Fix Istio ambient mesh + kubelet health probe conflict
# In ambient mode, ztunnel intercepts ALL inbound pod traffic including kubelet probes.
# Kubelet sends plain HTTP probes; ztunnel expects mTLS -> probe times out -> CrashLoopBackOff.
# Fix: opt these pods out of ambient (they communicate via plaintext internally)
#   - argocd-repo-server: liveness/readiness on port 8084 (HTTP)
#   - argocd-notifications-controller: liveness probe on port 9001 (TCP)
#   - argocd-application-controller: readiness on port 8082 (HTTP)
echo "Opting ArgoCD pods out of Istio ambient mesh..."

# argocd-server: opt out of ambient (SSO cookie flow breaks under ztunnel interception)
kubectl patch deployment argocd-server -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'

# repo-server: opt out of ambient (istio.io/dataplane-mode=none on pod label)
kubectl patch deployment argocd-repo-server -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'

# notifications-controller: opt out of ambient
kubectl patch deployment argocd-notifications-controller -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'

# application-controller (StatefulSet): opt out of ambient
kubectl patch statefulset argocd-application-controller -n devtools --type='json' \
  -p='[{"op": "add", "path": "/spec/template/metadata/labels/istio.io~1dataplane-mode", "value": "none"}]'
# StatefulSet does not auto-rollout on label change; delete pod to trigger recreation
kubectl delete pod argocd-application-controller-0 -n devtools 2>/dev/null || true

# Wait for ArgoCD pods
echo "Waiting for ArgoCD pods..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n devtools --timeout=300s || true
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-repo-server -n devtools --timeout=300s || true
kubectl wait --for=condition=Ready pod/argocd-application-controller-0 -n devtools --timeout=180s || true

# Configure ArgoCD server params (v3.x reads from cmd-params-cm, not argocd-cm)
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cmd-params-cm
  namespace: devtools
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
  namespace: devtools
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
  namespace: devtools
  labels:
    app.kubernetes.io/name: argocd-rbac-cm
    app.kubernetes.io/part-of: argocd
data:
  policy.default: role:readonly
  policy.csv: |
    p, role:developer, applications, sync, */*, allow
    p, role:developer, applications, get, */*, allow
    p, role:developer, logs, get, */*, allow
    p, role:none, applications, get, */*, deny
    g, cluster-admin, role:admin
    g, developer, role:developer
    g, viewer, role:readonly
    g, guest, role:none
  scopes: '[groups]'
EOF

# Restart ArgoCD server to apply OIDC config
kubectl rollout restart deployment argocd-server -n devtools
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n devtools --timeout=300s || true

# Get initial admin password
ARGOCD_PASSWORD=$(kubectl -n devtools get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d 2>/dev/null || echo "unknown")

echo "=== ArgoCD Installation Done ==="

echo ""
echo "=========================================="
echo "ArgoCD Ready!"
echo "=========================================="
echo ""
echo "Access:"
echo "  kubectl port-forward svc/argocd-server -n devtools 8443:443"
echo "  URL: https://localhost:8443"
echo "  User: admin"
echo "  Password: ${ARGOCD_PASSWORD}"
echo ""
echo "OIDC Login: Use Keycloak credentials (admin/admin)"
echo ""
kubectl get pods -n devtools
