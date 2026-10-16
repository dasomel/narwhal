#!/usr/bin/env bash
# Behavioral check (R266) for nfs_share_root_verdict under set -euo pipefail, the way
# nfs-nonroot-write-check.sh runs it: root_squash+750 PASS, root_squash+777 FAIL, all clients
# no_root_squash WARN (exit 0), mixed clients require 750, no export line FAIL with its message.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
S=/srv/nfs/k8s

run() { # mode exports -> sets RC, ERR
  ERR="$(bash -c 'set -euo pipefail; source "$1"; nfs_share_root_verdict "$2" "$3" "$4"' _ \
    "${root}/scripts/verify/lib-nfs-squash.sh" "${S}" "$1" "$2" 2>&1 >/dev/null)" && RC=0 || RC=$?
}
expect() { # name rc grep-pattern(optional)
  [ "${RC}" -eq "$2" ] || { echo "FAIL ${1}: rc=${RC} want $2 (${ERR})" >&2; exit 1; }
  [ -z "${3:-}" ] || printf '%s' "${ERR}" | grep -q "$3" || { echo "FAIL ${1}: missing '${3}' in: ${ERR}" >&2; exit 1; }
}
RS="${S} 10.0.0.0/24(rw,sync,no_subtree_check,root_squash)"
NRS="${S} 10.0.0.0/24(rw,sync,no_root_squash) 10.1.0.0/16(rw,sync,no_root_squash)"
MIX="${S} 10.0.0.0/24(rw,no_root_squash) 10.1.0.0/16(rw,root_squash)"
DEF="${S} 10.0.0.0/24(rw,sync,no_subtree_check)"

run 750 "${RS}";  expect root_squash_750 0
run 777 "${RS}";  expect root_squash_777 1 "expected 750"
run 777 "${DEF}"; expect default_squash_777 1 "expected 750"
run 777 "${NRS}"; expect nrs_777 0 "^WARN"
run 750 "${NRS}"; expect nrs_750 0
run 777 "${MIX}"; expect mixed_777 1 "expected 750"
run 750 "${MIX}"; expect mixed_750 0
run 750 "/other 10.0.0.0/24(rw)"; expect no_line 1 "no export line"
run 750 "";       expect empty 1 "no export line"
echo OK
