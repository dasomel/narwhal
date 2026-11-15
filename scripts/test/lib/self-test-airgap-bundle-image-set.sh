#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
compare="${ROOT_DIR}/scripts/test/lib/check-airgap-bundle-image-set.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/bundle/oci"
printf '%s\n' 'example.test/a:v1' 'example.test/b:v1' > "${tmp}/images.txt"
write_bundle() {
  printf '%s\n' "$@" > "${tmp}/bundle/manifest.txt"
  rm -rf "${tmp}/bundle/oci"
  mkdir -p "${tmp}/bundle/oci"
  local image safe
  for image in "$@"; do
    safe=$(printf '%s' "${image}" | tr '/:' '__')
    mkdir -p "${tmp}/bundle/oci/${safe}"
    : > "${tmp}/bundle/oci/${safe}/index.json"
  done
}

write_bundle 'example.test/a:v1' 'example.test/b:v1'
bash "${compare}" "${tmp}/images.txt" "${tmp}/bundle"
echo 'PASS identical sets'

write_bundle 'example.test/a:v1' 'example.test/c:v1'
if bash "${compare}" "${tmp}/images.txt" "${tmp}/bundle" >"${tmp}/swap.out" 2>&1; then
  echo 'FAIL equal-count swap unexpectedly passed' >&2
  exit 1
fi
grep -qF 'missing: example.test/b:v1' "${tmp}/swap.out"
grep -qF 'extra: example.test/c:v1' "${tmp}/swap.out"
# Failing-first evidence: the old count-only predicate accepts this equal-count swap.
old_want=$(grep -cvE '^\s*(#|$)' "${tmp}/images.txt")
old_have=$(grep -cvE '^\s*$' "${tmp}/bundle/manifest.txt")
[ "${old_want}" = "${old_have}" ]
echo 'PASS equal-count swap rejected with missing and extra names (old count-only predicate passes)'

write_bundle 'example.test/a:v1'
if bash "${compare}" "${tmp}/images.txt" "${tmp}/bundle" >"${tmp}/missing.out" 2>&1; then
  echo 'FAIL missing-only case unexpectedly passed' >&2
  exit 1
fi
grep -qF 'missing: example.test/b:v1' "${tmp}/missing.out"
echo 'PASS missing-only rejected'
