#!/bin/bash
# R283: 11-3's create_keycloak_client must fail, not print "", when the client secret read
# comes back empty. 2026-10-26: an empty read flowed into grafana-oidc-secret and
# devtools/gitea-oidc-secret, and Grafana login died with "Failed to get token from
# provider". Runs the real function body against a stubbed kc_exec (behavioural).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="${1:-${ROOT}/scripts/cluster/11-3-keycloak-clients.sh}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

sed -n '/^create_keycloak_client() {$/,/^}$/p' "${SRC}" > "${tmp}/fn.sh"
[ -s "${tmp}/fn.sh" ] || { echo "FAIL: create_keycloak_client not found in ${SRC}"; exit 1; }

run_case() {   # <secret-json> -> prints "rc=<n> out=<stdout>"
  local secret_json="$1"
  (
    set -euo pipefail
    export REALM=narwhal GROUPS_SCOPE_ID=g1   # read by the sourced function
    kc_exec() {
      case "$*" in
        "get clients -r narwhal -q clientId=grafana") echo '[{"clientId":"grafana","id":"c1"}]' ;;
        "get clients/c1/client-secret -r narwhal") echo "${secret_json}" ;;
        "get clients/c1/protocol-mappers/models -r narwhal") echo '[{"name":"grafana-audience","id":"m1"},{"name":"grafana-groups","id":"m2"}]' ;;
        *) : ;;
      esac
    }
    # shellcheck source=/dev/null
    source "${tmp}/fn.sh"
    out=$(create_keycloak_client grafana '["https://grafana.example/login/generic_oauth"]' 2>/dev/null) && rc=0 || rc=$?
    echo "rc=${rc} out=${out:-}"
  )
}

fail=0
got=$(run_case '{"type":"secret","value":"s3cr3t"}')
[ "${got}" = "rc=0 out=s3cr3t" ] || { echo "FAIL: readable secret -> '${got}', want 'rc=0 out=s3cr3t'"; fail=1; }
for empty in '{}' '{"value":""}' ''; do
  got=$(run_case "${empty}")
  case "${got}" in
    "rc=0"*) echo "FAIL: empty secret read '${empty}' returned success (${got})"; fail=1 ;;
  esac
done
[ "${fail}" = 0 ] && echo "OK: readable secret is returned; empty reads fail closed"
exit "${fail}"
