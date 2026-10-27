#!/bin/bash
# R285: 12-gitea.sh's seed-admin block (D-GITEA-ADMIN) promotes or creates the seeded
# cluster-admin user as a Gitea site admin. Runs the real block against a fake kubectl:
# 404 -> `gitea admin user create ... --admin`; 200 -> PATCH {admin: true} echoing the
# user's own source_id/login_name (empty login_name falls back to the username, which the
# API requires); any other lookup result -> warn, neither create nor PATCH.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="${1:-${ROOT}/scripts/cluster/12-gitea.sh}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

sed -n '/read -r -a seed_admins/,/^  done$/p' "${SRC}" > "${tmp}/block.sh"
[ -s "${tmp}/block.sh" ] || { echo "FAIL: seed-admin block not found in ${SRC}"; exit 1; }

run_case() {   # <lookup-code> <admin-search-json> -> calls log on stdout
  local code="$1" search="$2"
  : > "${tmp}/calls"
  (
    set -euo pipefail
    export GITEA_POD=gitea-0 GITEA_ADMIN_PASS=pw DOMAIN=example.test LOOKUP_CODE="${code}" SEARCH_JSON="${search}" CALLS="${tmp}/calls"
    kubectl() {
      local args="$*"
      case "${args}" in
        *"-w %{http_code}"*"/api/v1/users/admin"*) printf '%s' "${LOOKUP_CODE}" ;;
        *"/api/v1/admin/users?q=admin"*) printf '%s' "${SEARCH_JSON}" ;;
        *"-X PATCH"*) echo "PATCH ${args##*-d }" >> "${CALLS}" ;;
        *"admin user create"*) echo "CREATE ${args#*admin user create }" >> "${CALLS}" ;;
      esac
    }
    # shellcheck source=/dev/null
    source "${tmp}/block.sh" >/dev/null
  )
  cat "${tmp}/calls"
}

fail=0
expect() {   # <name> <got> <want-substring> [<must-not-contain>]
  if [[ "$2" != *"$3"* ]] || { [ -n "${4:-}" ] && [[ "$2" == *"$4"* ]]; }; then
    echo "FAIL: $1 -> '$2'"; fail=1
  fi
}

got=$(run_case 404 '[]')
expect "missing user is created as admin" "${got}" "--admin" "PATCH"
expect "created with the seeded username" "${got}" "--username admin"
got=$(run_case 200 '[{"login":"admin","source_id":1,"login_name":"admin-oidc"}]')
expect "existing user is promoted with its own source" "${got}" 'PATCH {"admin":true,"source_id":1,"login_name":"admin-oidc"}' "CREATE"
got=$(run_case 200 '[{"login":"admin","source_id":0,"login_name":""},{"login":"gitea-admin","source_id":0,"login_name":""}]')
expect "empty login_name falls back to the username" "${got}" 'PATCH {"admin":true,"source_id":0,"login_name":"admin"}'
# The record is searchable, so only the lookup code keeps this case from PATCHing or creating.
got=$(run_case 500 '[{"login":"admin","source_id":0,"login_name":"admin"}]')
[ -z "${got}" ] || { echo "FAIL: a failed lookup must neither create nor PATCH -> '${got}'"; fail=1; }

[ "${fail}" = 0 ] && echo "OK: seed admin is created (404), promoted with its own source (200), and left alone on a failed lookup"
exit "${fail}"
