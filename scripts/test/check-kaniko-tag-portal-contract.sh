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
  # A missing checkout is a skip locally but a hard failure when the caller says the
  # portal is required (CI): a silent exit 0 here once let R117 report PASS from a
  # git worktree whose ../narwhal-portal did not exist.
  if [ "${NARWHAL_PORTAL_REQUIRED:-0}" = "1" ]; then
    echo "FAIL: NARWHAL_PORTAL_REQUIRED=1 but narwhal-portal checkout not found at ${PORTAL_DIR}" >&2
    exit 1
  fi
  echo "SKIP: narwhal-portal sibling checkout not found at ${PORTAL_DIR} -- nothing to compare"
  exit 0
fi

if [ ! -f "${KANIKO_JOB_FILE}" ]; then
  echo "SKIP: ${KANIKO_JOB_FILE} not found -- nothing to compare"
  exit 0
fi

# Compare tags only, not the full ref -- the two files may use different registry
# prefixes for the same image (e.g. `alpine/git` vs `docker.io/alpine/git`), and
# either side may additionally digest-pin as `tag@sha256:...` (narwhal-portal#23).
#
# Extraction hazards, both real (found re-running this check live after
# narwhal-portal#23 added digest pins):
#   1. `cut -d: -f2` alone grabs everything after the FIRST colon, including the
#      digest's own `sha256:<hex>` colon -- "v1.24.0@sha256:4e7a52dd..." instead
#      of "v1.24.0" -- so strip a trailing `@...` before extracting the tag.
#   2. Matching anywhere in the file (not just the actual pin) picks up a prose
#      comment mentioning the same tag with trailing punctuation -- e.g. "...from
#      gcr.io/kaniko-project/executor:v1.24.0." captures "v1.24.0." with the
#      sentence's full stop attached. IMAGES_TXT is a plain list (`#`-comments,
#      then bare `registry/image:tag` lines, no `image:` YAML key) -- restrict it
#      to non-comment lines. KANIKO_JOB_FILE is a K8s manifest -- restrict it to
#      actual `image:` lines. Either grep can legitimately match nothing (comment
#      text mentioning the tag falls outside both filters); `|| true` keeps that
#      from aborting the script under `set -e -o pipefail`, and the empty-value
#      checks below report it as a real FAIL rather than silently passing.
extract_tag() {
  local file="$1" name="$2" line_filter="$3"
  grep -E "${line_filter}" "${file}" 2>/dev/null \
    | grep -oE "${name}:[^\"'[:space:]]+" \
    | head -1 | cut -d@ -f1 | cut -d: -f2 || true
}

images_kaniko_tag="$(extract_tag "${IMAGES_TXT}" 'kaniko-project/executor' '^[^#]')"
images_git_tag="$(extract_tag "${IMAGES_TXT}" 'alpine/git' '^[^#]')"
portal_kaniko_tag="$(extract_tag "${KANIKO_JOB_FILE}" 'kaniko-project/executor' '^\s*image:')"
portal_git_tag="$(extract_tag "${KANIKO_JOB_FILE}" 'alpine/git' '^\s*image:')"

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
