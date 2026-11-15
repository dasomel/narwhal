#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <images.txt> <bundle-dir>" >&2
  exit 2
}

[ "$#" = 2 ] || usage
images_file="$1"
bundle_dir="$2"

wanted_sorted=$(mktemp)
saved_sorted=$(mktemp)
trap 'rm -f "${wanted_sorted}" "${saved_sorted}"' EXIT

grep -vE '^[[:space:]]*(#|$)' "${images_file}" | sort -u > "${wanted_sorted}"
grep -vE '^[[:space:]]*$' "${bundle_dir}/manifest.txt" | sort -u > "${saved_sorted}"

missing=$(comm -23 "${wanted_sorted}" "${saved_sorted}")
extra=$(comm -13 "${wanted_sorted}" "${saved_sorted}")
if [ -n "${missing}" ] || [ -n "${extra}" ]; then
  printf 'image set mismatch (%s)\n' "${bundle_dir}" >&2
  [ -z "${missing}" ] || printf '%s\n' "${missing}" | head -10 | sed 's/^/missing: /' >&2
  [ -z "${extra}" ] || printf '%s\n' "${extra}" | head -10 | sed 's/^/extra: /' >&2
  exit 1
fi

wanted_count=$(wc -l < "${wanted_sorted}" | tr -d ' ')
layout_count=$(find "${bundle_dir}/oci" -maxdepth 4 -name index.json | wc -l | tr -d ' ')
if [ "${wanted_count}" != "${layout_count}" ]; then
  echo "OCI layout count mismatch: images=${wanted_count} oci=${layout_count} (${bundle_dir})" >&2
  exit 1
fi
