#!/bin/bash
set -euo pipefail

# =============================================================================
# 09-verify-bundle-completeness.sh — 1:1 completeness gate for an airgap bundle
#                                     (narwhal#51)
#
# 02-save-images.sh already fails the run if any single `skopeo copy` fails, but
# nothing afterward re-checks that images.txt, manifest.txt (the append-only log of
# what 02 actually saved) and the oci/ layout on disk still agree — a resumed/partial
# 02 run, a hand-edited manifest, or a half-written OCI layout can all leave those three
# out of sync with nothing catching it before the bundle ships. This is the release gate
# for that: every image images.txt lists MUST have a manifest.txt line AND an
# oci/<name>/index.json, or the run fails closed. Run this as the last step before a
# bundle is treated as a release artifact — after 02/03/07/08, right before transfer.
#
# DIGEST CHECK (narwhal#52 D1-A): every image must also have a resolved-digest row in
# lib/image-digests.tsv, fail closed on a missing row or a malformed digest. This is
# a COMPLETENESS check, not a byte-comparison against the on-disk OCI layout: 02
# calls `skopeo copy --override-arch ... oci:...`, which re-serializes the selected
# manifest (and its config blob) from Docker schema2 into OCI media types, producing
# a digest that matches neither image-digests.tsv's index_digest NOR the source's raw
# per-arch manifest digest even with zero tampering — verified empirically while
# building image-digests.tsv (see that file's header). Byte-comparing against it here
# would fail every non-OCI-native image unconditionally, which is worse than not
# checking at all. What this DOES catch: an image added to images.txt without ever
# running refresh-image-digests.sh (missing row), or a hand-edited/corrupted table
# (a digest that isn't a well-formed sha256, for an image that isn't the one
# documented in-cluster-built exception).
#
# Usage:
#   scripts/airgap/09-verify-bundle-completeness.sh [--list images.txt] [--bundle DIR] [--digests image-digests.tsv]
#
# Defaults: --list scripts/airgap/images.txt, --bundle "${AIRGAP_BUNDLE_DIR}" (arch-suffixed),
#           --digests scripts/airgap/lib/image-digests.tsv.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

LIST_FILE="${SCRIPT_DIR}/images.txt"
BUNDLE_DIR="${AIRGAP_BUNDLE_DIR}"
DIGESTS_FILE="${SCRIPT_DIR}/lib/image-digests.tsv"

# Kept in sync BY HAND with 01-generate-image-list.sh's INCLUSTER_BUILT_RE (also
# duplicated in refresh-image-digests.sh) — the one image images.txt could list that
# is built in-cluster by Kaniko and never pulled from any registry, so it is the only
# image allowed to carry an UNRESOLVED digest row instead of a real sha256.
INCLUSTER_BUILT_RE='harbor\.local\.narwhal\.internal/library/narwhal-portal'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)     LIST_FILE="$2"; shift 2 ;;
    --bundle)   BUNDLE_DIR="$2"; shift 2 ;;
    --digests)  DIGESTS_FILE="$2"; shift 2 ;;
    *)          echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "${LIST_FILE}" ]]; then
  echo "ERROR: image list not found: ${LIST_FILE}" >&2
  exit 1
fi
if [[ ! -f "${BUNDLE_DIR}/manifest.txt" ]]; then
  echo "ERROR: ${BUNDLE_DIR}/manifest.txt missing. Run 02-save-images.sh --list ${LIST_FILE} first." >&2
  exit 1
fi
if [[ ! -d "${BUNDLE_DIR}/oci" ]]; then
  echo "ERROR: ${BUNDLE_DIR}/oci missing. Run 02-save-images.sh --list ${LIST_FILE} first." >&2
  exit 1
fi
if [[ ! -f "${DIGESTS_FILE}" ]]; then
  echo "ERROR: ${DIGESTS_FILE} missing. Run scripts/airgap/lib/refresh-image-digests.sh first." >&2
  exit 1
fi

# Line-oriented throughout (no bash arrays) — this repo's shells include bash 3.2
# (macOS default), where `"${arr[@]}"` on an empty array is an unbound-variable error
# under `set -u`. `comm` on two sorted files gets the same set-diff with no such trap.
wanted_sorted=$(mktemp)
saved_sorted=$(mktemp)
trap 'rm -f "${wanted_sorted}" "${saved_sorted}"' EXIT

grep -vE '^[[:space:]]*(#|$)' "${LIST_FILE}" | sort -u > "${wanted_sorted}"
grep -vE '^[[:space:]]*$' "${BUNDLE_DIR}/manifest.txt" | sort -u > "${saved_sorted}"

# In images.txt but never recorded as saved.
missing_from_manifest=$(comm -23 "${wanted_sorted}" "${saved_sorted}")
# Saved but no longer in images.txt — stale bundle, not a completeness failure by
# itself, but worth surfacing since it usually means the bundle predates the list.
extra_in_manifest=$(comm -13 "${wanted_sorted}" "${saved_sorted}")

# Every wanted image must also have an actual OCI layout on disk — manifest.txt is
# only proof skopeo reported success, not proof the layout survived on disk.
missing_oci_layout=""
while IFS= read -r img; do
  [[ -z "${img}" ]] && continue
  safe=$(echo "${img}" | tr '/:' '__')
  [[ -f "${BUNDLE_DIR}/oci/${safe}/index.json" ]] || missing_oci_layout="${missing_oci_layout}${img}
"
done < "${wanted_sorted}"

# Every wanted image must have a resolved-digest row in image-digests.tsv (join on
# image_ref, tab-delimited column 1) — a missing row means an image was added to
# images.txt without ever running refresh-image-digests.sh. Only the documented
# in-cluster-built exception may carry UNRESOLVED; every other image's index_digest
# must look like a real sha256, or the table has drifted from what it actually pins.
digests_sorted=$(mktemp)
trap 'rm -f "${wanted_sorted}" "${saved_sorted}" "${digests_sorted}"' EXIT
grep -vE '^[[:space:]]*(#|$)' "${DIGESTS_FILE}" | sort -t $'\t' -k1,1 > "${digests_sorted}"

missing_digest_row=""
malformed_digest=""
while IFS= read -r img; do
  [[ -z "${img}" ]] && continue
  row=$(awk -F'\t' -v ref="${img}" '$1 == ref { print; exit }' "${digests_sorted}")
  if [[ -z "${row}" ]]; then
    missing_digest_row="${missing_digest_row}${img}
"
    continue
  fi
  digest=$(printf '%s' "${row}" | awk -F'\t' '{ print $2 }')
  if [[ "${img}" =~ ${INCLUSTER_BUILT_RE} ]]; then
    continue
  fi
  if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    malformed_digest="${malformed_digest}${img} (index_digest='${digest}')
"
  fi
done < "${wanted_sorted}"

fail=0

if [[ -n "${missing_from_manifest}" ]]; then
  echo "ERROR: listed in images.txt but never recorded as saved in manifest.txt:" >&2
  echo "${missing_from_manifest}" | sed 's/^/  /' >&2
  fail=1
fi

if [[ -n "${missing_oci_layout}" ]]; then
  echo "ERROR: listed in images.txt but no OCI layout (oci/<name>/index.json) on disk:" >&2
  printf '%s' "${missing_oci_layout}" | sed 's/^/  /' >&2
  fail=1
fi

if [[ -n "${missing_digest_row}" ]]; then
  echo "ERROR: listed in images.txt but no row in ${DIGESTS_FILE}:" >&2
  printf '%s' "${missing_digest_row}" | sed 's/^/  /' >&2
  fail=1
fi

if [[ -n "${malformed_digest}" ]]; then
  echo "ERROR: ${DIGESTS_FILE} row is not a resolved sha256 digest (and is not the documented in-cluster-built exception):" >&2
  printf '%s' "${malformed_digest}" | sed 's/^/  /' >&2
  fail=1
fi

if [[ -n "${extra_in_manifest}" ]]; then
  echo "WARN: saved in this bundle but no longer in images.txt (stale bundle?):" >&2
  echo "${extra_in_manifest}" | sed 's/^/  /' >&2
fi

want_count=$(wc -l < "${wanted_sorted}" | tr -d ' ')

if [[ ${fail} -ne 0 ]]; then
  echo "" >&2
  echo "Bundle ${BUNDLE_DIR} is INCOMPLETE and must not be promoted as a release artifact." >&2
  exit 1
fi

echo "Bundle complete: ${want_count} images in images.txt, all present in manifest.txt, oci/ layout, and ${DIGESTS_FILE} (${BUNDLE_DIR})." >&2
exit 0
