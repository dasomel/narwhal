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
#   # Cut an immutable release tag on top of the commit that was just published
#   # (accepts the same optional "commit message" [paths...] as the default form):
#   scripts/gitops/push-to-gitea.sh --tag v1.2.3
#
#   # Roll back to a previously tagged tree: publishes a NEW forward commit on
#   # main whose tree equals the tag's tree (never a reset/force-push, main's
#   # history is never rewritten). Refuses if this repo's gitops/ is dirty.
#   scripts/gitops/push-to-gitea.sh --rollback v1.2.3
#
# Issue #52 (D4-A): an immutable release is a tag that can never move, and a
# rollback is a forward commit that reproduces that tag's tree — not a branch
# rewrite (see docs/common/lessons-log.md GitOps/ArgoCD, 2026-09-06).
#
# REQUIREMENTS: kubectl (cluster-admin ctx), git, curl. Run from repo root or anywhere.
#
# TESTING OVERRIDE: set GITEA_REMOTE_URL to point at a throwaway repo (e.g. a
# local `git init --bare` or `file://` path) to exercise this script without a
# live cluster. When set, the kubectl secret lookup and port-forward are
# skipped entirely and no auth header is attached. Default behavior (in-cluster
# Gitea via port-forward) is unchanged when GITEA_REMOTE_URL is unset.
#=========================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Overridable because the source tree is not always beside this script. It is on the
# operator host (repo root), but on a node the Vagrant layout puts gitops/ under
# configs/ — so a cloud run, which has to execute where the kubeconfig lives, passes
# GITOPS_DIR=/home/vagrant/configs/gitops explicitly.
GITOPS_DIR="${GITOPS_DIR:-${REPO_ROOT}/gitops}"

GITEA_NS="devtools"
GITEA_SVC="gitea-http"
GITEA_USER="gitea-admin"
GITEA_REPO="narwhal-gitops"
LOCAL_PORT="${GITEA_LOCAL_PORT:-13000}"
ARGOCD_NS="devtools"
ARGOCD_APP="${ARGOCD_APP:-narwhal-portal}"   # app to sync; override per change
# Testing/dev override: skip kubectl secret + port-forward and clone/push straight
# to this URL instead (e.g. GITEA_REMOTE_URL=file:///tmp/fake-gitea.git). Unset in
# production — the default in-cluster Gitea path is untouched.
GITEA_REMOTE_URL="${GITEA_REMOTE_URL:-}"

log() { echo "[push-to-gitea] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

#--- mode parsing (default publish, or --tag / --rollback) ----------------
# vMAJOR.MINOR.PATCH, optionally with a -prerelease or +build suffix (SemVer-ish).
RELEASE_TAG_RE='^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.]+)?$'
validate_tag() {
  [[ "$1" =~ ${RELEASE_TAG_RE} ]] || die "invalid release tag '$1': expected vMAJOR.MINOR.PATCH, e.g. v1.2.3 (optionally -prerelease or +build)"
}

MODE="publish"
RELEASE_TAG=""
case "${1:-}" in
  --tag)
    [[ -n "${2:-}" ]] || die "--tag requires a version, e.g. --tag v1.2.3"
    validate_tag "$2"
    MODE="tag"
    RELEASE_TAG="$2"
    shift 2
    ;;
  --rollback)
    [[ -n "${2:-}" ]] || die "--rollback requires a version, e.g. --rollback v1.2.3"
    validate_tag "$2"
    MODE="rollback"
    RELEASE_TAG="$2"
    shift 2
    [[ $# -eq 0 ]] || die "--rollback takes no further arguments (got: $*)"
    ;;
esac

COMMIT_MSG="${1:-chore: sync gitops from narwhal repo}"
shift || true
PATHS=("$@")   # optional list of paths relative to gitops/ ; empty = mirror all

if [[ "${MODE}" != "rollback" ]]; then
  [ -d "${GITOPS_DIR}" ] || die "gitops source not found at ${GITOPS_DIR}. Set GITOPS_DIR to the gitops/ tree you want mirrored."
fi

#--- rollback safety gate: refuse a dirty local gitops/ before touching anything ---
# A rollback publishes the TAG's tree (from Gitea), not local gitops/ — but a dirty
# local tree sitting around during a rollback is a sign the operator meant to do
# something else first. Fail fast, before any kubectl/network call.
#
# This guard only applies when REPO_ROOT is actually a git checkout with GITOPS_DIR
# inside it. On a node, Vagrantfile rsyncs scripts/ alone to /home/vagrant/scripts
# (no .git there at all) — REPO_ROOT then resolves to /home/vagrant, `git -C
# "${REPO_ROOT}" ...` exits 128, and a guard that didn't account for that used to
# turn into a hard failure on precisely the disaster-recovery path
# (docs/vagrant/disaster-recovery.md step 5) that needs --rollback to work. A
# rollback only needs the remote tag, never the local tree, so when there is no
# checkout we just say so and continue.
if [[ "${MODE}" == "rollback" ]]; then
  GITOPS_IN_REPO=0
  REPO_TOPLEVEL="$(git -C "${REPO_ROOT}" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "${REPO_TOPLEVEL}" ]]; then
    # `git rev-parse --show-toplevel` resolves symlinks in the path; compare against
    # GITOPS_DIR's own resolved physical path too (`pwd -P`, no extra binary needed)
    # so e.g. macOS's /tmp -> /private/tmp doesn't produce a false "outside the repo".
    GITOPS_DIR_REAL="$(cd "${GITOPS_DIR}" 2>/dev/null && pwd -P || true)"
    case "${GITOPS_DIR_REAL:-${GITOPS_DIR}}/" in
      "${REPO_TOPLEVEL}/"*) GITOPS_IN_REPO=1 ;;
    esac
  fi
  if [[ "${GITOPS_IN_REPO}" -eq 1 ]]; then
    [[ -z "$(git -C "${REPO_ROOT}" status --porcelain -- "${GITOPS_DIR}")" ]] \
      || die "gitops/ has uncommitted changes; refusing to roll back with a dirty tree"
  else
    log "skipping local-dirty guard: ${REPO_ROOT} is not a git checkout containing ${GITOPS_DIR} (source is an rsync copy, e.g. Vagrant's scripts/ sync) — rollback only needs the remote tag"
  fi
fi

#--- resolve the clone URL (and, unless overridden, an auth header) -------
# AGENTS.md: never embed credentials in a remote URL — a URL with the password in
# it ends up in `.git/config`, in `git remote -v`, and in any git error message
# that echoes the remote (all three have bitten this repo before, see the
# 2026-07-10 lessons-log row). Use an HTTP Authorization header instead: it is
# never printed by git's own error paths and never shows in `git remote -v`.
#
# The header itself must never reach a `.git/config` file either: `git clone -c
# http.extraHeader=...` writes that `-c` straight into the new clone's own config
# (`extraheader = ...` under `[http]`), so anything reading that file (or the
# fixed `git clone ... -c ...` argv, ps-visible for the process lifetime) sees
# the credential. Pass it instead as GIT_CONFIG_COUNT/KEY/VALUE env vars scoped
# to each individual git invocation via gitr() below — nothing lands in any
# .git/config, only in that one short-lived process's own environment (still
# readable there via e.g. /proc/<pid>/environ by root or the same uid — that is
# the residual this leaves, not "never appears" anywhere).
AUTH_HEADER=""
PF_PID=""
if [[ -n "${GITEA_REMOTE_URL}" ]]; then
  CLONE_URL="${GITEA_REMOTE_URL}"
  log "using GITEA_REMOTE_URL override: ${CLONE_URL} (no port-forward, no auth header)"
else
  #--- resolve gitea admin password from cluster secret --------------------
  GITEA_PW="$(kubectl get secret gitea-admin -n "${GITEA_NS}" -o jsonpath='{.data.admin-password}' | base64 -d)"
  [[ -n "${GITEA_PW}" ]] || die "gitea-admin password not found in ${GITEA_NS}"

  #--- start port-forward (background) --------------------------------------
  log "port-forward svc/${GITEA_SVC} ${LOCAL_PORT}:3000 ..."
  kubectl port-forward "svc/${GITEA_SVC}" -n "${GITEA_NS}" "${LOCAL_PORT}:3000" >/tmp/gitea-pf.log 2>&1 &
  PF_PID=$!
  # wait until gitea answers (no fixed sleep)
  for _ in $(seq 1 30); do
    curl -s -o /dev/null -m 2 "http://localhost:${LOCAL_PORT}/" && break
    sleep 1
  done

  CLONE_URL="http://localhost:${LOCAL_PORT}/${GITEA_USER}/${GITEA_REPO}.git"
  AUTH_HEADER="Authorization: Basic $(printf '%s:%s' "${GITEA_USER}" "${GITEA_PW}" | base64 | tr -d '\n')"
fi

# gitr: run a git subcommand that talks to the Gitea remote, with the auth header
# (if any) injected only into that one process's environment — never written to
# a config file, never passed on argv. Local-only commands (checkout, read-tree,
# add, commit, rev-parse, config user.*) should use plain `git`, not this.
gitr() {
  if [[ -n "${AUTH_HEADER}" ]]; then
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="http.extraHeader" GIT_CONFIG_VALUE_0="${AUTH_HEADER}" git "$@"
  else
    git "$@"
  fi
}

#--- cleanup: kill the port-forward (if any) and the temp clone on exit ---
WORK=""
cleanup() {
  [[ -n "${PF_PID}" ]] && kill "${PF_PID}" 2>/dev/null
  [[ -n "${WORK}" ]] && rm -rf "${WORK}"
  return 0
}
trap cleanup EXIT

#--- clone the gitea repo fresh --------------------------------------------
WORK="$(mktemp -d)"
log "cloning ${GITEA_REPO} ..."
gitr clone -q "${CLONE_URL}" "${WORK}/repo"
cd "${WORK}/repo"
# Ensure we are on `main` regardless of the remote's default branch (also correct
# on a brand-new/empty bare repo).
git checkout -q -B main

if [[ "${MODE}" == "tag" ]]; then
  #--- verify the tag does NOT already exist, before publishing anything ----
  # Must run before any commit/push: checking after the publish step let an
  # already-existing tag both create a new `main` commit AND then exit 1.
  log "checking whether ${RELEASE_TAG} already exists on the remote ..."
  if gitr ls-remote --exit-code --tags origin "refs/tags/${RELEASE_TAG}" >/dev/null; then
    die "tag ${RELEASE_TAG} already exists on the remote — tags are immutable, refusing to move it"
  fi
fi

if [[ "${MODE}" == "rollback" ]]; then
  #--- verify the tag exists on the remote, then reproduce its tree ---------
  log "verifying tag ${RELEASE_TAG} exists on the remote ..."
  gitr ls-remote --exit-code --tags origin "refs/tags/${RELEASE_TAG}" >/dev/null \
    || die "tag ${RELEASE_TAG} not found on the remote — cannot roll back to a tag that was never published"
  gitr fetch -q origin "refs/tags/${RELEASE_TAG}:refs/tags/${RELEASE_TAG}"

  TAG_SHA="$(git rev-parse "refs/tags/${RELEASE_TAG}^{commit}")"
  log "reproducing tree of ${RELEASE_TAG} (${TAG_SHA}) as a new commit on main"
  # `read-tree --reset -u` makes the index AND working tree exactly match the
  # tag's tree (adds/removes files as needed) without touching main's history —
  # the commit that follows is a normal forward commit, never a reset/rewrite.
  git read-tree -u --reset "refs/tags/${RELEASE_TAG}^{tree}"
  git add -A
  COMMIT_MSG="chore(gitops): roll back to ${RELEASE_TAG}"
else
  #--- copy changed file(s) from gitops/ into the gitea repo root ------------
  if [[ ${#PATHS[@]} -eq 0 ]]; then
    log "mirroring ALL of gitops/ -> gitea repo root"
    cp -r "${GITOPS_DIR}/." .
  else
    for rel in "${PATHS[@]}"; do
      src="${GITOPS_DIR}/${rel}"
      [[ -e "${src}" ]] || die "${src} does not exist"
      mkdir -p "$(dirname "${rel}")"
      cp -r "${src}" "${rel}"
      log "copied gitops/${rel} -> ${rel}"
    done
  fi
  git add -A
fi

#--- commit + push --------------------------------------------------------
git config user.email "admin@local"
git config user.name "GitOps Sync"
if git diff --cached --quiet; then
  log "no changes to push (gitea already up to date)"
else
  git commit -q -m "${COMMIT_MSG}"
  log "pushing to gitea ..."
  gitr push -q origin HEAD:main
  log "pushed: ${COMMIT_MSG}"
fi
MAIN_SHA="$(git rev-parse HEAD)"

#--- --tag: create the immutable release tag on the just-published commit -
# Existence was already checked above, before this commit/push happened.
TAG_SHA_OUT=""
if [[ "${MODE}" == "tag" ]]; then
  log "tagging ${MAIN_SHA} as ${RELEASE_TAG}"
  git tag -a "${RELEASE_TAG}" -m "release ${RELEASE_TAG}" "${MAIN_SHA}"
  gitr push -q origin "refs/tags/${RELEASE_TAG}"
  TAG_SHA_OUT="$(git rev-parse "refs/tags/${RELEASE_TAG}^{commit}")"
  log "pushed tag ${RELEASE_TAG} -> ${TAG_SHA_OUT}"
elif [[ "${MODE}" == "rollback" ]]; then
  TAG_SHA_OUT="${TAG_SHA}"
  log "main is now at ${MAIN_SHA} (tree of ${RELEASE_TAG}, tag commit ${TAG_SHA_OUT})"
fi

#--- trigger ArgoCD sync (refresh + let automated sync pick it up) ---------
# Soft refresh annotation forces ArgoCD to re-read Gitea immediately.
kubectl -n "${ARGOCD_NS}" annotate application "${ARGOCD_APP}" \
  argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true
log "requested ArgoCD refresh for app/${ARGOCD_APP} (automated sync will apply)"
log "verify:  kubectl -n ${ARGOCD_NS} get application ${ARGOCD_APP} -o jsonpath='{.status.sync.status}'"

#--- machine-readable evidence for --tag / --rollback ----------------------
if [[ "${MODE}" != "publish" ]]; then
  tag_json="null"; [[ -n "${RELEASE_TAG}" ]] && tag_json="\"${RELEASE_TAG}\""
  tag_sha_json="null"; [[ -n "${TAG_SHA_OUT}" ]] && tag_sha_json="\"${TAG_SHA_OUT}\""
  EVIDENCE_JSON="{\"mode\":\"${MODE}\",\"tag\":${tag_json},\"tag_sha\":${tag_sha_json},\"main_sha\":\"${MAIN_SHA}\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
  echo "${EVIDENCE_JSON}"
  if [[ -d "${HOME}/.narwhal" ]]; then
    echo "${EVIDENCE_JSON}" > "${HOME}/.narwhal/gitops-release.json"
    log "evidence written to ${HOME}/.narwhal/gitops-release.json"
  fi
fi

log "done."
