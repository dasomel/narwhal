#!/bin/bash
# R287b: gitea_upsert_keycloak_source (scripts/common/lib.sh, D-GITEA-OAUTH, #491) updates the
# existing 'keycloak' OAuth2 source in place when present, adds it when absent, warns and returns
# non-zero without add/update when `gitea admin auth list` fails, and replaces direct
# delete/add-oauth calls in 12-gitea.sh and 11-3-keycloak-clients.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LIB="${1:-${ROOT}/scripts/common/lib.sh}"
GITEA_SH="${2:-${ROOT}/scripts/cluster/12-gitea.sh}"
KC_SH="${3:-${ROOT}/scripts/cluster/11-3-keycloak-clients.sh}"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

sed -n '/^gitea_upsert_keycloak_source()/,/^}$/p' "${LIB}" > "${tmp}/helper.sh"
[ -s "${tmp}/helper.sh" ] || { echo "FAIL: gitea_upsert_keycloak_source not found in ${LIB}"; exit 1; }

run_case() {   # <helper_file> <list_rc> <list_stdout> -> sets RUN_RC, RUN_OUT, RUN_CALLS
  local helper="$1" list_rc="$2" list_stdout="$3"
  : > "${tmp}/calls"
  : > "${tmp}/out"
  set +e
  (
    set -euo pipefail
    export LIST_RC="${list_rc}" LIST_STDOUT="${list_stdout}" CALLS="${tmp}/calls"
    kubectl() {
      local args="$*"
      case "${args}" in
        "exec -n devtools gitea-0 -- /app/gitea/gitea admin auth list")
          [ "${LIST_RC}" -eq 0 ] || return "${LIST_RC}"
          printf '%s\n' "${LIST_STDOUT}"
          ;;
        "exec -n devtools gitea-0 -- /app/gitea/gitea admin auth update-oauth "*)
          echo "UPDATE ${args#*admin auth update-oauth }" >> "${CALLS}"
          ;;
        "exec -n devtools gitea-0 -- /app/gitea/gitea admin auth add-oauth "*)
          echo "ADD ${args#*admin auth add-oauth }" >> "${CALLS}"
          ;;
        *)
          echo "UNEXPECTED ${args}" >> "${CALLS}"
          return 1
          ;;
      esac
    }
    # shellcheck source=/dev/null
    source "${helper}"
    gitea_upsert_keycloak_source "gitea-0" "s3cr3t-kc!" "https://keycloak.example.test/realms/narwhal/.well-known/openid-configuration"
  ) >"${tmp}/out" 2>&1
  RUN_RC=$?
  set -e
  RUN_OUT="$(cat "${tmp}/out")"
  RUN_CALLS="$(cat "${tmp}/calls")"
}

REQUIRED_FLAGS=(
  "--provider openidConnect"
  "--key gitea"
  "--secret s3cr3t-kc!"
  "--auto-discover-url https://keycloak.example.test/realms/narwhal/.well-known/openid-configuration"
  "--group-claim-name groups"
  "--admin-group cluster-admin"
  "--restricted-group guest"
  '--group-team-map {"developer":{"narwhal":["Developers"]},"viewer":{"narwhal":["Viewers"]}}'
  "--group-team-map-removal"
  "--skip-local-2fa"
  "--scopes openid profile email groups"
)

verify_suite() {   # <helper_file> <12-gitea.sh> <11-3-keycloak-clients.sh>
  local helper="$1" f12="$2" f113="$3" flag

  # (1) list shows keycloak with ID 7 -> exactly one update-oauth --id 7 with all flags, no add-oauth.
  local list_with_kc=$'ID\tName\tType\tEnabled\n2\tkeycloak2\tOAuth2\ttrue\n7\tkeycloak\tOAuth2\ttrue'
  run_case "${helper}" 0 "${list_with_kc}"
  [ "${RUN_RC}" -eq 0 ] || { echo "case 1: expected rc=0, got ${RUN_RC}"; return 1; }
  [[ "${RUN_OUT}" != *"s3cr3t-kc!"* ]] || { echo "case 1: secret leaked to output"; return 1; }
  [ "$(grep -c . <<< "${RUN_CALLS}" || true)" -eq 1 ] || { echo "case 1: expected 1 call, got '${RUN_CALLS}'"; return 1; }
  [[ "${RUN_CALLS}" == "UPDATE --id 7 "* ]] || { echo "case 1: expected UPDATE --id 7, got '${RUN_CALLS}'"; return 1; }
  [[ "${RUN_CALLS}" != *"ADD "* ]] || { echo "case 1: unexpected ADD in '${RUN_CALLS}'"; return 1; }
  for flag in "${REQUIRED_FLAGS[@]}"; do
    [[ "${RUN_CALLS}" == *"${flag}"* ]] || { echo "case 1: missing flag '${flag}' in '${RUN_CALLS}'"; return 1; }
  done

  # (2) list without keycloak (only keycloak2) -> exactly one add-oauth --name keycloak, no update-oauth.
  local list_only_kc2=$'ID\tName\tType\tEnabled\n2\tkeycloak2\tOAuth2\ttrue'
  run_case "${helper}" 0 "${list_only_kc2}"
  [ "${RUN_RC}" -eq 0 ] || { echo "case 2: expected rc=0, got ${RUN_RC}"; return 1; }
  [[ "${RUN_OUT}" != *"s3cr3t-kc!"* ]] || { echo "case 2: secret leaked to output"; return 1; }
  [ "$(grep -c . <<< "${RUN_CALLS}" || true)" -eq 1 ] || { echo "case 2: expected 1 call, got '${RUN_CALLS}'"; return 1; }
  [[ "${RUN_CALLS}" == "ADD --name keycloak "* ]] || { echo "case 2: expected ADD --name keycloak, got '${RUN_CALLS}'"; return 1; }
  [[ "${RUN_CALLS}" != *"UPDATE "* ]] || { echo "case 2: unexpected UPDATE in '${RUN_CALLS}'"; return 1; }
  for flag in "${REQUIRED_FLAGS[@]}"; do
    [[ "${RUN_CALLS}" == *"${flag}"* ]] || { echo "case 2: missing flag '${flag}' in '${RUN_CALLS}'"; return 1; }
  done

  # (3) list exec fails -> non-zero + WARN, neither add nor update.
  run_case "${helper}" 1 ""
  [ "${RUN_RC}" -ne 0 ] || { echo "case 3: expected non-zero rc when list fails"; return 1; }
  [[ "${RUN_OUT}" == *"WARN"* ]] || { echo "case 3: expected WARN on list failure, got '${RUN_OUT}'"; return 1; }
  [ -z "${RUN_CALLS}" ] || { echo "case 3: expected no add/update on list failure, got '${RUN_CALLS}'"; return 1; }

  # (4) neither 12-gitea.sh nor 11-3 contains auth delete --name or add-oauth/update-oauth outside the helper.
  local src code
  for src in "${f12}" "${f113}"; do
    code="$(grep -v '^[[:space:]]*#' "${src}")"
    grep -Fq "gitea_upsert_keycloak_source" <<< "${code}" || { echo "case 4: ${src} does not call gitea_upsert_keycloak_source"; return 1; }
    if grep -Eq "auth delete[[:space:]]+--name|admin auth (add-oauth|update-oauth)" <<< "${code}"; then
      echo "case 4: ${src} still contains direct auth delete --name or add-oauth/update-oauth"
      return 1
    fi
  done
  return 0
}

if ! verify_suite "${tmp}/helper.sh" "${GITEA_SH}" "${KC_SH}"; then
  echo "FAIL: gitea_upsert_keycloak_source verification failed"
  exit 1
fi

# Prove each case can fail against a mutated copy.
sed 's/--skip-local-2fa//' "${tmp}/helper.sh" > "${tmp}/m1.sh"
! diff -q "${tmp}/helper.sh" "${tmp}/m1.sh" >/dev/null || { echo "FAIL: m1 anchor drifted"; exit 1; }
if verify_suite "${tmp}/m1.sh" "${GITEA_SH}" "${KC_SH}" >/dev/null 2>&1; then
  echo "FAIL: mutant 1 (missing --skip-local-2fa) was not caught"
  exit 1
fi

sed 's/\$2 == "keycloak"/\$2 ~ \/\^keycloak\//' "${tmp}/helper.sh" > "${tmp}/m2.sh"
! diff -q "${tmp}/helper.sh" "${tmp}/m2.sh" >/dev/null || { echo "FAIL: m2 anchor drifted"; exit 1; }
if verify_suite "${tmp}/m2.sh" "${GITEA_SH}" "${KC_SH}" >/dev/null 2>&1; then
  echo "FAIL: mutant 2 (prefix match accepting keycloak2) was not caught"
  exit 1
fi

sed 's/return 1/:/' "${tmp}/helper.sh" > "${tmp}/m3.sh"
! diff -q "${tmp}/helper.sh" "${tmp}/m3.sh" >/dev/null || { echo "FAIL: m3 anchor drifted"; exit 1; }
if verify_suite "${tmp}/m3.sh" "${GITEA_SH}" "${KC_SH}" >/dev/null 2>&1; then
  echo "FAIL: mutant 3 (list failure continuing to add/update) was not caught"
  exit 1
fi

sed 's/--group-team-map-removal//' "${tmp}/helper.sh" > "${tmp}/m5.sh"
! diff -q "${tmp}/helper.sh" "${tmp}/m5.sh" >/dev/null || { echo "FAIL: m5 anchor drifted"; exit 1; }
if verify_suite "${tmp}/m5.sh" "${GITEA_SH}" "${KC_SH}" >/dev/null 2>&1; then
  echo "FAIL: mutant 5 (missing --group-team-map-removal: a demoted user keeps the old team) was not caught"
  exit 1
fi

cp "${GITEA_SH}" "${tmp}/m4-12.sh"
echo 'kubectl exec -n devtools "${GITEA_POD}" -- /app/gitea/gitea admin auth delete --name keycloak' >> "${tmp}/m4-12.sh"
if verify_suite "${tmp}/helper.sh" "${tmp}/m4-12.sh" "${KC_SH}" >/dev/null 2>&1; then
  echo "FAIL: mutant 4 (reintroduced auth delete --name in 12-gitea.sh) was not caught"
  exit 1
fi

echo "OK: gitea_upsert_keycloak_source updates id=7 in place with team-map removal, adds when absent (ignoring keycloak2), fails closed on list error, and is the sole OAuth source writer in 11-3 and 12 (5 mutations caught)"
