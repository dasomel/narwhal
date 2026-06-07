#!/usr/bin/env bash
#=========================================================================
# push-to-gitea.sh
# Sync GitOps changes from the narwhal repo (gitops/) into the in-cluster
# Gitea repo (narwhal-gitops) that ArgoCD tracks, then trigger a sync.
#
# WHY THIS EXISTS:
#   ArgoCD watches the in-cluster Gitea repo
#     http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git
#   with syncPolicy.automated.selfHeal=true. Therefore:
#     - `kubectl apply` / `kubectl patch` to managed resources are REVERTED by selfHeal.
#     - Editing files in this repo's gitops/ + a LOCAL git commit is NOT enough;
#       ArgoCD never sees the local commit (it reads Gitea, not GitHub/local).
#   The ONLY durable path is: push the change INTO Gitea, then ArgoCD syncs.
#
# STRUCTURE MAPPING (important):
#   narwhal repo            gitea narwhal-gitops repo (ArgoCD root)
#   ---------------------   ---------------------------------------
#   gitops/apps/*       ->  apps/*          (app-of-apps, path=apps)
#   gitops/resources/*  ->  resources/*     (path=resources)
#   i.e. the CONTENTS of gitops/ are the ROOT of the gitea repo.
#
# USAGE:
#   scripts/gitops/push-to-gitea.sh "commit message" [path-under-gitops ...]
#
#   # Push everything under gitops/ (full mirror):
#   scripts/gitops/push-to-gitea.sh "fix(portal-rbac): grant metrics.k8s.io read"
#
#   # Push only specific files (recommended — minimal blast radius):
#   scripts/gitops/push-to-gitea.sh "fix(portal-rbac): metrics read" \
#       resources/narwhal-portal-k8s.yaml
#
# REQUIREMENTS: kubectl (cluster-admin ctx), git, curl. Run from repo root or anywhere.
#=========================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GITOPS_DIR="${REPO_ROOT}/gitops"

GITEA_NS="devtools"
GITEA_SVC="gitea-http"
GITEA_USER="gitea-admin"
GITEA_REPO="narwhal-gitops"
LOCAL_PORT="${GITEA_LOCAL_PORT:-13000}"
ARGOCD_NS="devtools"
ARGOCD_APP="${ARGOCD_APP:-narwhal-portal}"   # app to sync; override per change

COMMIT_MSG="${1:-chore: sync gitops from narwhal repo}"
shift || true
PATHS=("$@")   # optional list of paths relative to gitops/ ; empty = mirror all

log() { echo "[push-to-gitea] $*"; }

#--- resolve gitea admin password from cluster secret ---------------------
GITEA_PW="$(kubectl get secret gitea-admin -n "${GITEA_NS}" -o jsonpath='{.data.admin-password}' | base64 -d)"
[[ -n "${GITEA_PW}" ]] || { echo "ERROR: gitea-admin password not found in ${GITEA_NS}"; exit 1; }

#--- start port-forward (background), ensure cleanup ----------------------
log "port-forward svc/${GITEA_SVC} ${LOCAL_PORT}:3000 ..."
kubectl port-forward "svc/${GITEA_SVC}" -n "${GITEA_NS}" "${LOCAL_PORT}:3000" >/tmp/gitea-pf.log 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
# wait until gitea answers (no fixed sleep)
for _ in $(seq 1 30); do
  curl -s -o /dev/null -m 2 "http://localhost:${LOCAL_PORT}/" && break
  sleep 1
done

#--- clone the gitea repo fresh -------------------------------------------
WORK="$(mktemp -d)"
trap 'kill ${PF_PID} 2>/dev/null || true; rm -rf "${WORK}"' EXIT
log "cloning ${GITEA_REPO} ..."
git clone -q "http://${GITEA_USER}:${GITEA_PW}@localhost:${LOCAL_PORT}/${GITEA_USER}/${GITEA_REPO}.git" "${WORK}/repo"
cd "${WORK}/repo"

#--- copy changed file(s) from gitops/ into the gitea repo root ------------
if [[ ${#PATHS[@]} -eq 0 ]]; then
  log "mirroring ALL of gitops/ -> gitea repo root"
  cp -r "${GITOPS_DIR}/." .
else
  for rel in "${PATHS[@]}"; do
    src="${GITOPS_DIR}/${rel}"
    [[ -e "${src}" ]] || { echo "ERROR: ${src} does not exist"; exit 1; }
    mkdir -p "$(dirname "${rel}")"
    cp -r "${src}" "${rel}"
    log "copied gitops/${rel} -> ${rel}"
  done
fi

#--- commit + push --------------------------------------------------------
git config user.email "admin@local"
git config user.name "GitOps Sync"
git add -A
if git diff --cached --quiet; then
  log "no changes to push (gitea already up to date)"
else
  git commit -q -m "${COMMIT_MSG}"
  log "pushing to gitea ..."
  git push -q origin HEAD:main
  log "pushed: ${COMMIT_MSG}"
fi

#--- trigger ArgoCD sync (refresh + let automated sync pick it up) ---------
# Soft refresh annotation forces ArgoCD to re-read Gitea immediately.
kubectl -n "${ARGOCD_NS}" annotate application "${ARGOCD_APP}" \
  argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true
log "requested ArgoCD refresh for app/${ARGOCD_APP} (automated sync will apply)"
log "verify:  kubectl -n ${ARGOCD_NS} get application ${ARGOCD_APP} -o jsonpath='{.status.sync.status}'"
log "done."
