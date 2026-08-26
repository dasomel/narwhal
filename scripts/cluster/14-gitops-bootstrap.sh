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
if ! kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea -n devtools --timeout=300s; then
  echo "ERROR: Gitea did not become Ready within 300s." >&2
  echo "       Everything below needs it, so continuing would only move the failure" >&2
  echo "       somewhere less informative. Check: kubectl -n devtools describe pod -l app.kubernetes.io/name=gitea" >&2
  exit 1
fi
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

# Fail closed from here down. A swallowed failure in this block produces a cluster
# whose GitOps source is empty or stale, and the symptom — ArgoCD reconciling nothing
# — appears much later with no trace of the cause. That is the whole of issue #159.
#
# (This was already fatal by accident: a failed clone left no directory, so the `cd`
# below aborted under `set -e`. It aborted with "cd: no such file", which named the
# wrong thing.)
if ! git clone "http://${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}@localhost:3000/${GITEA_ADMIN_USER}/${REPO_NAME}.git"; then
  echo "ERROR: could not clone ${REPO_NAME}. The repository exists (created above), so" >&2
  echo "       this is the port-forward or Gitea auth, not the repo." >&2
  exit 1
fi
cd "${REPO_NAME}"

# Copy gitops configs. The source is the Vagrantfile's synced_folder for gitops/; if it
# is missing the repo would be committed EMPTY and ArgoCD would sync nothing forever.
if [ ! -d /home/vagrant/configs/gitops ]; then
  echo "ERROR: /home/vagrant/configs/gitops does not exist — nothing to publish." >&2
  echo "       The Vagrantfile syncs gitops/ there; check the synced_folder." >&2
  exit 1
fi
cp -r /home/vagrant/configs/gitops/* .

# Ubuntu 26.04 (Resolute, kernel 7.0): falco 0.39.2 modern_ebpf probe fails scap_init
# (no kernel 7.0 support yet), so exclude falco from the GitOps apps on 26.04 until a
# falco release supports it. The app-of-apps then never creates the falco Application.
. /etc/os-release 2>/dev/null || true
if [ "${VERSION_ID:-}" = "26.04" ]; then
  echo "Ubuntu ${VERSION_ID} detected — excluding falco (no kernel 7.0 support yet)"
  rm -f charts/narwhal-apps/templates/falco.yaml
fi

# The chart the root Application points at. A warning here used to be the only
# signal, and the install continued to create idp-apps against a tree that could not
# possibly sync.
for required in charts/narwhal-apps/Chart.yaml apps/app-of-apps.yaml resources/argocd-projects.yaml; do
  if [ ! -f "${required}" ]; then
    echo "ERROR: ${required} is missing from the staged gitops tree." >&2
    echo "       ArgoCD cannot sync without it; refusing to publish a tree that will not work." >&2
    exit 1
  fi
done

# Commit and push
git config user.email "admin@local"
git config user.name "GitOps Bootstrap"
git add -A

# "nothing to commit" is the expected outcome of a RERUN and is not a failure; any
# other non-zero exit is. Distinguishing them is the point — `|| true` treated a
# broken commit and an idempotent no-op as the same thing.
if git diff --cached --quiet; then
  echo "  no changes to publish (rerun with an identical tree)"
elif ! git commit -m "Initial GitOps configuration"; then
  echo "ERROR: git commit failed with staged changes present." >&2
  exit 1
fi

if ! git push origin main; then
  echo "ERROR: could not push to ${REPO_NAME} main." >&2
  echo "       If this says 'protected branch', the branch protection was applied before" >&2
  echo "       the initial push — it must come after; see the ordering note below." >&2
  exit 1
fi

GITOPS_COMMIT="$(git rev-parse HEAD)"

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

# Delete existing token if present to ensure token minting is idempotent across re-runs.
curl -s -o /dev/null -X DELETE \
  "http://localhost:3000/api/v1/users/${ARGOCD_GIT_USER}/tokens/argocd-gitops-read" \
  -u "${ARGOCD_GIT_USER}:${ARGOCD_GIT_PASSWORD}" || true

# Tokens are scoped. read:repository is the whole permission set ArgoCD needs.
argocd_token_resp="$(curl -s -X POST "http://localhost:3000/api/v1/users/${ARGOCD_GIT_USER}/tokens" \
  -H "Content-Type: application/json" \
  -u "${ARGOCD_GIT_USER}:${ARGOCD_GIT_PASSWORD}" \
  -d '{"name":"argocd-gitops-read","scopes":["read:repository"]}')"
ARGOCD_GIT_TOKEN="$(printf '%s' "${argocd_token_resp}" | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4 || true)"
if [ -z "${ARGOCD_GIT_TOKEN}" ]; then
  echo "ERROR: could not mint a read:repository token for ${ARGOCD_GIT_USER}" >&2
  echo "       Gitea response: ${argocd_token_resp}" >&2
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

# Delete existing token if present to ensure token minting is idempotent across re-runs.
curl -s -o /dev/null -X DELETE \
  "http://localhost:3000/api/v1/users/${PORTAL_GIT_USER}/tokens/portal-selfservice" \
  -u "${PORTAL_GIT_USER}:${PORTAL_GIT_PASSWORD}" || true

portal_token_resp="$(curl -s -X POST "http://localhost:3000/api/v1/users/${PORTAL_GIT_USER}/tokens" \
  -H "Content-Type: application/json" \
  -u "${PORTAL_GIT_USER}:${PORTAL_GIT_PASSWORD}" \
  -d '{"name":"portal-selfservice","scopes":["write:repository"]}')"
PORTAL_GIT_TOKEN="$(printf '%s' "${portal_token_resp}" | grep -o '"sha1":"[^"]*"' | cut -d'"' -f4 || true)"
if [ -z "${PORTAL_GIT_TOKEN}" ]; then
  echo "ERROR: could not mint a write:repository token for ${PORTAL_GIT_USER}" >&2
  echo "       Gitea response: ${portal_token_resp}" >&2
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

#=========================================
# Validate the published source before ArgoCD is pointed at it
#=========================================
# ArgoCD is about to be told "this repository is the desired state of the cluster".
# Everything above can succeed individually and still leave a source that cannot sync:
# a repo whose default branch is not main, a push that landed on a different branch, a
# tree missing the chart the root Application names. Each of those surfaces as "ArgoCD
# syncs nothing", days from the cause.
#
# So the claim is checked against the SERVER, not against local state — the local
# working copy proves what we intended, the API proves what arrived.
echo "Validating the published GitOps source..."
REPO_API="http://localhost:3000/api/v1/repos/${GITEA_ADMIN_USER}/${REPO_NAME}"

repo_json="$(curl -sf -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" "${REPO_API}")" || {
  echo "ERROR: ${REPO_NAME} is not readable through the API after publishing." >&2
  exit 1
}

default_branch="$(printf '%s' "${repo_json}" | grep -o '"default_branch":"[^"]*"' | cut -d'"' -f4 || true)"
if [ "${default_branch}" != "main" ]; then
  echo "ERROR: default branch is '${default_branch}', expected 'main'." >&2
  echo "       app-of-apps tracks HEAD, so a different default branch means ArgoCD" >&2
  echo "       would reconcile a branch nothing pushes to." >&2
  exit 1
fi

branch_json="$(curl -sf -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" "${REPO_API}/branches/main")" || {
  echo "ERROR: could not read main branch info from ${REPO_API}/branches/main" >&2
  exit 1
}
remote_commit="$(printf '%s' "${branch_json}" | grep -o '"id":"[a-f0-9]\{40\}"' | awk -F'"' 'NR==1 {print $4}')"
if [ "${remote_commit}" != "${GITOPS_COMMIT}" ]; then
  echo "ERROR: main is at ${remote_commit:-<unknown>}, but we pushed ${GITOPS_COMMIT}." >&2
  echo "       Something else wrote to the branch, or the push did not land." >&2
  exit 1
fi

# The paths the root Application and 13-argocd.sh actually name. Checked on the
# SERVER at the pushed revision, so a file that exists locally but was never committed
# (gitignored, or missed by `git add`) is caught here rather than at sync time.
for required in charts/narwhal-apps/Chart.yaml apps/app-of-apps.yaml resources/argocd-projects.yaml; do
  if ! curl -sf -o /dev/null -u "${GITEA_ADMIN_USER}:${GITEA_ADMIN_PASSWORD}" \
    "${REPO_API}/contents/${required}?ref=${GITOPS_COMMIT}"; then
    echo "ERROR: ${required} is not present in ${REPO_NAME} at ${GITOPS_COMMIT}." >&2
    exit 1
  fi
done

GITOPS_SOURCE_VALIDATED=1
echo "  source validated at ${GITOPS_COMMIT}"

# Machine-readable evidence. The next person debugging "why is the cluster like this"
# gets the exact revision ArgoCD was pointed at and when, without reconstructing it
# from shell history.
mkdir -p /home/vagrant/.narwhal
cat > /home/vagrant/.narwhal/gitops-bootstrap.json <<JSONEOF
{
  "repository": "${GITEA_ADMIN_USER}/${REPO_NAME}",
  "private": true,
  "default_branch": "main",
  "commit": "${GITOPS_COMMIT}",
  "validated_paths": [
    "charts/narwhal-apps/Chart.yaml",
    "apps/app-of-apps.yaml",
    "resources/argocd-projects.yaml"
  ],
  "validated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "argocd_project": "platform"
}
JSONEOF
echo "  evidence written to /home/vagrant/.narwhal/gitops-bootstrap.json"

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

# The gate. idp-apps is what turns the repository into the cluster's desired state, so
# it is created only after the source was verified to exist, be on main, be at the
# commit we pushed, and contain the paths it names. Without this the install could
# report success while ArgoCD reconciled an empty repo.
if [ "${GITOPS_SOURCE_VALIDATED:-0}" != "1" ]; then
  echo "ERROR: refusing to create idp-apps — the GitOps source was not validated." >&2
  exit 1
fi

cat <<EOF | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: idp-apps
  namespace: devtools
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  # platform, not default. The default project has sourceRepos *, destinations *
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
