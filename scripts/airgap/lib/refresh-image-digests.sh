#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 dasomel
#=========================================================================
# refresh-image-digests.sh
# Re-resolve every image in images.txt against its registry and regenerate
# image-digests.tsv (narwhal#52 D1-A).
#
# WHY: a version tag is not immutable — a registry lets `v1.2.3` be re-pushed to
# point at different bytes. image-digests.tsv pins the digest each tag resolved to
# when it was last reviewed, so a re-push shows up as a diff here instead of
# silently changing what the next airgap bundle ships. See image-digests.tsv's own
# header for exactly what "index_digest" does and does not verify.
#
# Requires: crane (metadata-only `crane digest` calls — no image content is
# downloaded). Needs network access to every registry images.txt references.
#
# USAGE: scripts/airgap/lib/refresh-image-digests.sh [--check]
#   --check   exit 1 if any resolved digest differs from the committed table
#             (upstream tag-move detector), printing the diff (for CI)
#=========================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_LIST="${HERE}/../images.txt"
MAP="${HERE}/image-digests.tsv"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

if ! command -v crane >/dev/null 2>&1; then
  echo "ERROR: crane not installed. Install: brew install crane (mac) / go install github.com/google/go-containerregistry/cmd/crane@latest" >&2
  exit 1
fi

if [ ! -f "${IMAGES_LIST}" ]; then
  echo "ERROR: image list not found: ${IMAGES_LIST}" >&2
  exit 1
fi

# Kept in sync BY HAND with 01-generate-image-list.sh's INCLUSTER_BUILT_RE — the one
# image images.txt lists that is built in-cluster by Kaniko and never pulled from any
# registry, so it has no upstream digest to resolve.
INCLUSTER_BUILT_RE='harbor\.local\.narwhal\.internal/library/narwhal-portal'

resolve_digest() {
  # Metadata-only manifest HEAD, not a blob pull — cheap enough to run per image, but
  # still subject to Docker Hub's 100/6h anonymous rate limit, so retry with backoff
  # on transient failures (429 included) rather than failing on the first hiccup.
  local ref="$1" attempt digest
  for attempt in 1 2 3; do
    if digest="$(crane digest "${ref}" 2>/dev/null)"; then
      printf '%s' "${digest}"
      return 0
    fi
    sleep $((attempt * 3))
  done
  return 1
}

TMP_DIR="$(mktemp -d)"
TMP_ROWS="${TMP_DIR}/rows.tsv"
TMP_OUT="${TMP_DIR}/image-digests.tsv"
trap 'rm -rf "${TMP_DIR}"' EXIT
touch "${TMP_ROWS}"

resolved_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fail=0

while IFS= read -r img; do
  [ -z "${img}" ] && continue
  case "${img}" in \#*) continue ;; esac

  if [[ "${img}" =~ ${INCLUSTER_BUILT_RE} ]]; then
    printf '%s\tUNRESOLVED\t%s\tin-cluster build (Kaniko); never pulled from a registry, matches INCLUSTER_BUILT_RE\n' \
      "${img}" "${resolved_at}" >> "${TMP_ROWS}"
    continue
  fi

  if digest="$(resolve_digest "${img}")"; then
    printf '%s\t%s\t%s\tcrane digest\n' "${img}" "${digest}" "${resolved_at}" >> "${TMP_ROWS}"
  else
    echo "ERROR: failed to resolve digest for ${img} (not the in-cluster-built pattern — this must resolve)" >&2
    fail=1
  fi

  # Stay well under Docker Hub's anonymous rate limit across ~100 images in one run.
  sleep 0.3
done < <(grep -vE '^[[:space:]]*(#|$)' "${IMAGES_LIST}")

if [ "${fail}" -eq 1 ]; then
  echo "ERROR: one or more images could not be resolved; refusing to write a partial table." >&2
  exit 1
fi

cat << 'EOF' > "${TMP_OUT}"
# image_ref -> upstream index digest -> resolution timestamp -> source.
#
# WHY THIS FILE EXISTS: a container tag is a mutable pointer — the registry lets
# `v1.2.3` be re-pushed to point at different bytes with no visible change in
# images.txt. This table pins the digest each tag resolved to when last reviewed, so
# a re-push is a diff here (caught by `refresh-image-digests.sh --check`) instead of
# a silent change in what the next airgap bundle ships.
#
# index_digest is what the registry returns for `<image_ref>` with NO arch override
# (`crane digest <ref>`, equivalently `skopeo inspect --raw docker://<ref> | sha256sum`)
# — for a multi-arch image this is the manifest-list/index digest, and it is the
# value that changes on a tag re-push. IT IS NOT the digest that ends up on disk in
# an airgap bundle: 02-save-images.sh runs `skopeo copy --override-arch ... oci:...`,
# which selects one platform's manifest AND re-serializes it (and its config blob)
# from Docker schema2 media types into OCI media types — a real re-encoding that
# changes the digest even with zero tampering (verified while building this table:
# copying a plain docker.io/library/busybox:1.28 produced a local digest matching
# NEITHER the source index digest NOR the source per-arch manifest digest). So this
# column is the upstream drift detector, not a value 09-verify-bundle-completeness.sh
# can byte-compare against the local OCI layout — see that script's own comments for
# what it checks instead.
#
# UNRESOLVED rows: images.txt's in-cluster-built image (matches
# 01-generate-image-list.sh's INCLUSTER_BUILT_RE) is never pulled from any registry,
# so it has no upstream digest to resolve; the source column carries the reason
# instead of a digest.
#
# Refresh with:      scripts/airgap/lib/refresh-image-digests.sh
# Check for drift with: scripts/airgap/lib/refresh-image-digests.sh --check
#
# image_ref	index_digest	resolved_at	source
EOF

LC_ALL=C sort -t $'\t' -k1,1 "${TMP_ROWS}" >> "${TMP_OUT}"

if [ "${CHECK}" -eq 1 ]; then
  # Timestamps always differ between runs — compare everything except column 3.
  if diff -u <(cut -f1,2,4 "${MAP}") <(cut -f1,2,4 "${TMP_OUT}"); then
    echo "image-digests.tsv is current (no upstream tag movement detected)"
    exit 0
  fi
  echo "image-digests.tsv is stale or an upstream tag moved — run without --check and review the diff" >&2
  exit 1
fi

cp "${TMP_OUT}" "${MAP}"
echo "image-digests.tsv updated"
