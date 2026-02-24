#!/bin/bash
set -euo pipefail

echo "=== Bootstrapping GitOps ==="

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

GITEA_URL="http://gitea-http.devtools.svc.cluster.local:3000"
GITEA_ADMIN_USER="gitea-admin"
GITEA_ADMIN_PASSWORD="gitea-admin"
REPO_NAME="narwhal-gitops"

#=========================================
# Wait for Gitea to be ready
#=========================================
echo "Waiting for Gitea..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea -n devtools --timeout=300s || true
sleep 30

#=========================================
# Create GitOps repository in Gitea
#=========================================
echo "Creating GitOps repository in Gitea..."

# Port-forward Gitea for API access
kubectl port-forward svc/gitea-http -n devtools 3000:3000 &
PF_PID=$!
sleep 5

# Create repository via API
curl -s -X POST "http://localhost:3000/api/v1/user/repos" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d '{
    "name": "'${REPO_NAME}'",
    "description": "Narwhal IDP GitOps configurations",
    "private": false,
    "auto_init": true
  }' || true

# Kill port-forward
kill ${PF_PID} 2>/dev/null || true

#=========================================
# Push GitOps configs to repository
#=========================================
echo "Pushing GitOps configs..."

# Setup git
cd /tmp
rm -rf ${REPO_NAME}

# Clone via port-forward
kubectl port-forward svc/gitea-http -n devtools 3000:3000 &
PF_PID=$!
sleep 5

git clone "http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git" || true
cd ${REPO_NAME}

# Copy gitops configs
cp -r /home/vagrant/configs/gitops/* . 2>/dev/null || true

# If configs weren't synced, create minimal structure
if [ ! -d "apps" ]; then
  mkdir -p apps resources

  # Create app-of-apps
  cat > apps/app-of-apps.yaml <<'APPOFAPPS'
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: idp-apps
  namespace: devtools
spec:
  project: default
  source:
    repoURL: http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git
    targetRevision: HEAD
    path: apps
  destination:
    server: https://kubernetes.default.svc
    namespace: devtools
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
APPOFAPPS
fi

# Commit and push
git config user.email "admin@local"
git config user.name "GitOps Bootstrap"
git add -A
git commit -m "Initial GitOps configuration" || true
git push origin main || true

kill ${PF_PID} 2>/dev/null || true

#=========================================
# Ensure unified PostgreSQL cluster is ready
#=========================================
echo "Ensuring unified PostgreSQL cluster (narwhal-db) is ready..."

# Create database namespace first
kubectl create namespace database --dry-run=client -o yaml | kubectl apply -f -

# Apply unified narwhal-db resource
kubectl apply -f /home/vagrant/configs/gitops/resources/narwhal-db.yaml 2>/dev/null || true

# Wait for unified database
echo "Waiting for narwhal-db cluster..."
kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true

#=========================================
# Create ArgoCD App-of-Apps
#=========================================
echo "Creating ArgoCD App-of-Apps..."

cat <<EOF | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: idp-apps
  namespace: devtools
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: http://gitea-http.devtools.svc.cluster.local:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git
    targetRevision: HEAD
    path: apps
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: devtools
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
EOF

echo "=== GitOps Bootstrap Done ==="

echo ""
echo "=========================================="
echo "GitOps Ready!"
echo "=========================================="
echo ""
echo "GitOps Repository: ${GITEA_URL}/${GITEA_ADMIN_USER}/${REPO_NAME}"
echo ""
echo "ArgoCD will now manage the following apps:"
echo "  - cert-manager"
echo "  - prometheus-stack (Prometheus, Grafana)"
echo "  - loki"
echo "  - promtail"
echo "  - tempo"
echo "  - gitea"
echo "  - harbor"
echo "  - openbao"
echo "  - kyverno"
echo "  - headlamp"
echo ""
echo "Check ArgoCD UI for sync status:"
echo "  kubectl port-forward svc/argocd-server -n devtools 8443:443"
echo ""
