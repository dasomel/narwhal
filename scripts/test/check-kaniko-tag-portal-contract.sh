#!/usr/bin/env bash
set -euo pipefail

#=========================================
# Kaniko/alpine-git tag <-> Portal build job contract check
#=========================================
# Cross-repo seam (narwhal#52 D3-A): scripts/airgap/images.txt pins the kaniko
# executor and alpine/git build-helper images to immutable version tags so the
# airgap bundle ships them; narwhal-portal's deploy/kaniko-build-job.yaml pins the
# SAME two images because that's the manifest that actually runs them. A comment on
# either side saying "keep these in sync" is not enough to keep them in sync — that
# is exactly how this pair drifted to :latest in the first place (#52 triage,
# 2026-09-06). This compares the tags, not just presence.
#
# Usage: scripts/test/check-kaniko-tag-portal-contract.sh

cd "$(dirname "$0")/../.."

IMAGES_TXT="${IMAGES_TXT:-scripts/airgap/images.txt}"
PORTAL_DIR="${PORTAL_DIR:-../narwhal-portal}"
KANIKO_JOB_FILE="${PORTAL_DIR}/deploy/kaniko-build-job.yaml"

# The portal repo is a sibling checkout, not a submodule of this one -- it may not
# exist in every environment this runs in (e.g. a narwhal-only CI job). Structured
# as an `if` block, not `[ -d ... ] && ...`: under `set -e` the latter aborts the
# whole script the instant the directory is absent, never reaching the skip message.
if [ ! -d "${PORTAL_DIR}" ]; then
  echo "SKIP: narwhal-portal sibling checkout not found at ${PORTAL_DIR} -- nothing to compare"
  exit 0
fi

if [ ! -f "${KANIKO_JOB_FILE}" ]; then
  echo "SKIP: ${KANIKO_JOB_FILE} not found -- nothing to compare"
  exit 0
fi

# Compare tags only, not the full ref -- the two files may use different registry
# prefixes for the same image (e.g. `alpine/git` vs `docker.io/alpine/git`).
images_kaniko_tag="$(grep -oE 'kaniko-project/executor:[^"'"'"'[:space:]]+' "${IMAGES_TXT}" | head -1 | cut -d: -f2)"
images_git_tag="$(grep -oE 'alpine/git:[^"'"'"'[:space:]]+' "${IMAGES_TXT}" | head -1 | cut -d: -f2)"
portal_kaniko_tag="$(grep -oE 'kaniko-project/executor:[^"'"'"'[:space:]]+' "${KANIKO_JOB_FILE}" | head -1 | cut -d: -f2)"
portal_git_tag="$(grep -oE 'alpine/git:[^"'"'"'[:space:]]+' "${KANIKO_JOB_FILE}" | head -1 | cut -d: -f2)"

if [ -z "${images_kaniko_tag}" ] || [ -z "${images_git_tag}" ]; then
  echo "FAIL: could not find a pinned kaniko-project/executor or alpine/git tag in ${IMAGES_TXT}" >&2
  exit 1
fi
if [ -z "${portal_kaniko_tag}" ] || [ -z "${portal_git_tag}" ]; then
  echo "FAIL: could not find a pinned kaniko-project/executor or alpine/git tag in ${KANIKO_JOB_FILE}" >&2
  exit 1
fi

fail=0
if [ "${images_kaniko_tag}" != "${portal_kaniko_tag}" ]; then
  echo "FAIL: kaniko-project/executor tag mismatch: ${IMAGES_TXT}=${images_kaniko_tag} vs ${KANIKO_JOB_FILE}=${portal_kaniko_tag}" >&2
  fail=1
fi
if [ "${images_git_tag}" != "${portal_git_tag}" ]; then
  echo "FAIL: alpine/git tag mismatch: ${IMAGES_TXT}=${images_git_tag} vs ${KANIKO_JOB_FILE}=${portal_git_tag}" >&2
  fail=1
fi

[ "${fail}" -eq 0 ] && echo "OK: kaniko/alpine-git tags match between ${IMAGES_TXT} and ${KANIKO_JOB_FILE}"
exit "${fail}"
