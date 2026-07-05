#!/bin/bash
set -euo pipefail

DOMAIN="${DOMAIN:-local.narwhal.internal}"

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
GITEA_ADMIN_PASSWORD="$(kubectl get secret gitea-admin -n devtools -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d)"
if [[ -z "${GITEA_ADMIN_PASSWORD}" ]]; then
  echo "ERROR: gitea-admin secret not found in devtools namespace. Ensure 12-gitea.sh ran successfully."
  exit 1
fi
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
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
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
kill "${PF_PID}" 2>/dev/null || true
trap - EXIT

#=========================================
# Push GitOps configs to repository
#=========================================
echo "Pushing GitOps configs..."

# Setup git
cd /tmp
rm -rf "${REPO_NAME}"

# Clone via port-forward
kubectl port-forward svc/gitea-http -n devtools 3000:3000 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 5

git clone "http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git" || true
cd "${REPO_NAME}"

# Copy gitops configs
cp -r /home/vagrant/configs/gitops/* . 2>/dev/null || true

# Ubuntu 26.04 (Resolute, kernel 7.0): falco 0.39.2 modern_ebpf probe fails scap_init
# (no kernel 7.0 support yet), so exclude falco from the GitOps apps on 26.04 until a
# falco release supports it. The app-of-apps then never creates the falco Application.
. /etc/os-release 2>/dev/null || true
if [ "${VERSION_ID:-}" = "26.04" ]; then
  echo "Ubuntu ${VERSION_ID} detected — excluding falco (no kernel 7.0 support yet)"
  rm -f charts/narwhal-apps/templates/falco.yaml
fi

# If configs weren't synced, warn (chart must be present for ArgoCD to sync)
if [ ! -d "charts/narwhal-apps" ]; then
  echo "WARN: charts/narwhal-apps not found in synced gitops — ArgoCD will fail to sync."
  echo "      Ensure gitops/ is synced from the narwhal repo before bootstrapping."
fi

# Commit and push
git config user.email "admin@local"
git config user.name "GitOps Bootstrap"
git add -A
git commit -m "Initial GitOps configuration" || true
git push origin main || true

kill "${PF_PID}" 2>/dev/null || true
trap - EXIT

#=========================================
# Ensure unified PostgreSQL cluster is ready
#=========================================
echo "Ensuring unified PostgreSQL cluster (narwhal-db) is ready..."

# Create database namespace first
kubectl create namespace database --dry-run=client -o yaml | kubectl apply -f -

# Apply unified narwhal-db resource
if [[ -f "/home/vagrant/configs/gitops/resources/narwhal-db.yaml" ]]; then
  kubectl apply -f /home/vagrant/configs/gitops/resources/narwhal-db.yaml
else
  echo "WARN: narwhal-db.yaml not found, skipping"
fi

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
    path: charts/narwhal-apps
    helm:
      valuesObject:
        baseDomain: ${DOMAIN}
        repoURL: http://gitea-http.devtools.svc.cluster.local:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git
  destination:
    server: https://kubernetes.default.svc
    namespace: devtools
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
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
echo "  - k8s-monitoring (Grafana Alloy)"
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
