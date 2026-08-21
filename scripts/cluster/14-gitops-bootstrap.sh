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

# Create repository via API.
#
# PRIVATE. It was `"private": false`, which meant anyone who could reach Gitea —
# including anonymous readers — could clone the repository that describes the entire
# cluster: every manifest, every image reference, every namespace and policy. Verified
# on a scratch Gitea 1.26.2: with private=false an anonymous `git clone` succeeds and
# `GET /api/v1/repos/...` returns 200; with private=true the clone fails and the API
# returns 404.
#
# Flipping this ALONE breaks ArgoCD, which reads the repo with no credentials at all
# and only worked because it was public. The read-only account and the ArgoCD
# repository Secret further down are what make the flip survivable — do not separate
# them.
#
# The 201 is checked rather than `|| true`-ed: a swallowed failure here produces a
# cluster with no GitOps source, which then shows up much later as "ArgoCD syncs
# nothing" with no trace of the cause.
create_code=$(curl -s -o /tmp/repo-create.json -w '%{http_code}' -X POST "http://localhost:3000/api/v1/user/repos" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d '{
    "name": "'${REPO_NAME}'",
    "description": "Narwhal IDP GitOps configurations",
    "private": true,
    "auto_init": true
  }')
case "${create_code}" in
  201) echo "  repository created (private)" ;;
  409) echo "  repository already exists — reasserting visibility"
       curl -sf -X PATCH "http://localhost:3000/api/v1/repos/${GITEA_ADMIN_USER}/${REPO_NAME}" \
         -H "Content-Type: application/json" \
         -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
         -d '{"private": true}' >/dev/null \
         || { echo "ERROR: could not set ${REPO_NAME} private" >&2; exit 1; } ;;
  *)   echo "ERROR: repository creation failed (HTTP ${create_code})" >&2
       cat /tmp/repo-create.json >&2; exit 1 ;;
esac

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

#=========================================
# ArgoCD read-only access to the now-private repository
#=========================================
# The repository is private, so ArgoCD can no longer read it anonymously — which is
# how it read it before, with no Secret of any kind. This gives it its own identity
# instead of handing it the admin credential:
#
#   user `argocd`  ->  read collaborator on the repo  ->  read:repository token
#
# Verified on a scratch Gitea 1.26.2: that token clones the private repo and is
# REJECTED on push, so a leak of it cannot rewrite the cluster's desired state.
echo "Provisioning ArgoCD read-only access to ${REPO_NAME}..."
ARGOCD_GIT_USER="argocd"
ARGOCD_GIT_PASSWORD="$(openssl rand -base64 24)"

# 201 = created, 422 = already exists (password is then reset below so the token
# request below authenticates either way).
curl -s -o /dev/null -X POST "http://localhost:3000/api/v1/admin/users" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d "{\"username\":\"${ARGOCD_GIT_USER}\",\"email\":\"argocd@${DOMAIN}\",\"password\":\"${ARGOCD_GIT_PASSWORD}\",\"must_change_password\":false}"
curl -s -o /dev/null -X PATCH "http://localhost:3000/api/v1/admin/users/${ARGOCD_GIT_USER}" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d "{\"login_name\":\"${ARGOCD_GIT_USER}\",\"source_id\":0,\"password\":\"${ARGOCD_GIT_PASSWORD}\",\"must_change_password\":false}"

curl -sf -o /dev/null -X PUT \
  "http://localhost:3000/api/v1/repos/${GITEA_ADMIN_USER}/${REPO_NAME}/collaborators/${ARGOCD_GIT_USER}" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d '{"permission":"read"}' \
  || { echo "ERROR: could not grant ${ARGOCD_GIT_USER} read on ${REPO_NAME}" >&2; exit 1; }

# Tokens are scoped. read:repository is the whole permission set ArgoCD needs.
ARGOCD_GIT_TOKEN="$(curl -s -X POST "http://localhost:3000/api/v1/users/${ARGOCD_GIT_USER}/tokens" \
  -H "Content-Type: application/json" \
  -u "${ARGOCD_GIT_USER}:${ARGOCD_GIT_PASSWORD}" \
  -d '{"name":"argocd-gitops-read","scopes":["read:repository"]}' \
  | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)"
if [ -z "${ARGOCD_GIT_TOKEN}" ]; then
  echo "ERROR: could not mint a read:repository token for ${ARGOCD_GIT_USER}" >&2
  exit 1
fi

#=========================================
# Portal self-service identity
#=========================================
# The portal requests namespaces by opening a PULL REQUEST here, not by calling the
# Kubernetes API. Its ServiceAccount has `namespaces: [get, list, watch]` and keeps
# it that way — a namespace is a tenancy decision, and the approval on the pull
# request is where that decision gets made and recorded.
#
# This account is deliberately ABSENT from the push whitelist below. Write
# collaborator lets it push a BRANCH; the protection rule stops it reaching main.
# Verified on a scratch Gitea 1.26.2: branch push succeeds, `git push origin main`
# is rejected with "protected branch main", and merging its own PR returns 405
# "Does not have enough approvals". The review gate is therefore not advisory.
echo "Provisioning the portal self-service identity..."
PORTAL_GIT_USER="portal-gitops"
PORTAL_GIT_PASSWORD="$(openssl rand -base64 24)"

curl -s -o /dev/null -X POST "http://localhost:3000/api/v1/admin/users" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d "{\"username\":\"${PORTAL_GIT_USER}\",\"email\":\"portal-gitops@${DOMAIN}\",\"password\":\"${PORTAL_GIT_PASSWORD}\",\"must_change_password\":false}"
curl -s -o /dev/null -X PATCH "http://localhost:3000/api/v1/admin/users/${PORTAL_GIT_USER}" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d "{\"login_name\":\"${PORTAL_GIT_USER}\",\"source_id\":0,\"password\":\"${PORTAL_GIT_PASSWORD}\",\"must_change_password\":false}"

curl -sf -o /dev/null -X PUT \
  "http://localhost:3000/api/v1/repos/${GITEA_ADMIN_USER}/${REPO_NAME}/collaborators/${PORTAL_GIT_USER}" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d '{"permission":"write"}' \
  || { echo "ERROR: could not grant ${PORTAL_GIT_USER} write on ${REPO_NAME}" >&2; exit 1; }

PORTAL_GIT_TOKEN="$(curl -s -X POST "http://localhost:3000/api/v1/users/${PORTAL_GIT_USER}/tokens" \
  -H "Content-Type: application/json" \
  -u "${PORTAL_GIT_USER}:${PORTAL_GIT_PASSWORD}" \
  -d '{"name":"portal-selfservice","scopes":["write:repository"]}' \
  | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4)"
if [ -z "${PORTAL_GIT_TOKEN}" ]; then
  echo "ERROR: could not mint a write:repository token for ${PORTAL_GIT_USER}" >&2
  exit 1
fi

#=========================================
# Protect main
#=========================================
# AFTER the initial push, not before: the bootstrap itself is the first writer, and
# a rule applied earlier would reject it.
#
# enable_push stays TRUE with a whitelist rather than false. push-to-gitea.sh is the
# only durable way to change this cluster — ArgoCD runs selfHeal, so kubectl edits are
# reverted — and a hard `enable_push:false` would brick that path. Verified on a
# scratch Gitea: with the whitelist, gitea-admin pushes successfully while a
# collaborator holding WRITE is still rejected with "protected branch main". So the
# operator path survives and everyone else goes through a reviewed pull request.
echo "Protecting the ${REPO_NAME} main branch..."
curl -sf -o /dev/null -X POST \
  "http://localhost:3000/api/v1/repos/${GITEA_ADMIN_USER}/${REPO_NAME}/branch_protections" \
  -H "Content-Type: application/json" \
  -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
  -d '{"rule_name":"main","enable_push":true,"enable_push_whitelist":true,"push_whitelist_usernames":["'"${GITEA_ADMIN_USER}"'"],"required_approvals":1,"block_on_rejected_reviews":true,"block_on_outdated_branch":true}' \
  || echo "  main protection already present (or unchanged)"

kill "${PF_PID}" 2>/dev/null || true
trap - EXIT

#=========================================
# Register the private repo with ArgoCD
#=========================================
# Without this Secret ArgoCD reports "authentication required" on every Application
# the moment the repository stops being public. The URL must match app-of-apps.yaml
# exactly — ArgoCD matches credentials to repositories by URL prefix, and a trailing
# `.git` mismatch silently yields no credentials rather than an error.
kubectl create secret generic narwhal-gitops-repo -n devtools \
  --from-literal=name=narwhal-gitops \
  --from-literal=type=git \
  --from-literal=url="${GITEA_URL}/${GITEA_ADMIN_USER}/${REPO_NAME}.git" \
  --from-literal=username="${ARGOCD_GIT_USER}" \
  --from-literal=password="${ARGOCD_GIT_TOKEN}" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml \
  | kubectl apply -f -
echo "  ArgoCD repository credentials registered for ${REPO_NAME}"

# The portal reads these to open its pull requests. narwhal-portal-secrets is created
# by 13-2-narwhal-portal-bindings.sh, which runs first, so this adds keys to it rather
# than creating it — a `create --dry-run | apply` here would drop every other key.
if kubectl get secret narwhal-portal-secrets -n devtools >/dev/null 2>&1; then
  kubectl patch secret narwhal-portal-secrets -n devtools --type merge -p "$(cat <<PATCHEOF
{"stringData":{
  "GITEA_URL": "${GITEA_URL}",
  "GITEA_OWNER": "${GITEA_ADMIN_USER}",
  "GITEA_REPO": "${REPO_NAME}",
  "GITEA_TOKEN": "${PORTAL_GIT_TOKEN}"
}}
PATCHEOF
)" >/dev/null
  echo "  portal self-service credentials added to narwhal-portal-secrets"
else
  echo "WARN: narwhal-portal-secrets not found — run 13-2-narwhal-portal-bindings.sh," >&2
  echo "      then re-run this script, or the portal cannot open namespace requests." >&2
fi

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
  # `platform`, not `default`. The default project has sourceRepos *, destinations *
  # and no resource restrictions, so an Application accepted into the repo could
  # deploy anything from anywhere into any namespace — iam and database included.
  # 13-argocd.sh creates this project before we get here; an Application naming a
  # project that does not exist is rejected, so the ordering is load-bearing.
  project: platform
  source:
    repoURL: http://gitea-http.devtools.svc.cluster.local:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git
    targetRevision: HEAD
    path: charts/narwhal-apps
    helm:
      valuesObject:
        baseDomain: ${DOMAIN}
        repoURL: http://gitea-http.devtools.svc.cluster.local:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git
        # Hands the provisioning-time PROVIDER to ArgoCD. Without it the chart falls back
        # to vagrant and selfHeal undoes the cloud wiring 08-1-networking.sh applied:
        # MetalLB gets re-created and the APISIX gateway flips back to LoadBalancer.
        provider: ${PROVIDER:-vagrant}
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
