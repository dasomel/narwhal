#!/usr/bin/env bash
# Share-root mode verdict for nfs-nonroot-write-check.sh, split out so it can be tested without a cluster.
# Usage: nfs_share_root_verdict <share_root> <mode> <exports_text>
# Prints PASS/WARN/FAIL text; returns 1 only on FAIL. Every grep ends in `|| true`: grep -o exits 1
# on no match (the normal root_squash case) and that must not abort a caller running under set -e.
nfs_share_root_verdict() {
  local share_root="$1" mode="$2" exports="$3" lines total nrs
  lines="$(printf '%s\n' "${exports}" | grep -E "^[[:space:]]*${share_root}[[:space:]]" || true)"
  total="$(printf '%s\n' "${lines}" | grep -oE '\([^)]*\)' | wc -l | tr -d ' ' || true)"
  nrs="$(printf '%s\n' "${lines}" | grep -oE '\([^)]*no_root_squash[^)]*\)' | wc -l | tr -d ' ' || true)"
  if [ -z "${lines}" ] || [ "${total:-0}" -eq 0 ]; then
    echo "FAIL: no export line for ${share_root} in /etc/exports, cannot judge the share root mode (${mode})" >&2
    return 1
  elif [ "${nrs}" -eq "${total}" ]; then
    if [ "${mode}" != "750" ]; then
      echo "WARN: ${share_root} is exported no_root_squash to every client and has mode ${mode} (hardened target: root_squash + 750; see the root_squash section of docs/runbooks/nfs-xfs-migration.md)" >&2
    fi
  elif [ "${mode}" != "750" ]; then
    echo "FAIL: ${share_root} has a root_squash client but mode is ${mode}, expected 750" >&2
    return 1
  fi
  return 0
}
