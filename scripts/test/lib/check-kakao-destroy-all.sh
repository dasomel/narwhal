#!/bin/bash
# R292: behavioural harness for csp/kakao-cloud/terraform/destroy-all.sh (D-KAKAO-TEARDOWN).
# Runs the real script in a temporary copy of the terraform directory (the three guarded .tf
# files copied from the repo, a dummy terraform.tfstate, a non-empty kakao_known_hosts) against
# a stub `tofu` on PATH. The stub records the destroy arguments and how many guards were
# released at the moment destroy ran, then succeeds, cancels, leaves the VPC behind (PENDING_DELETE
# or ACTIVE), sends SIGTERM or SIGQUIT to the script, or makes the next `state list` fail.
#   1. Success (data sources stay in state, as after a real destroy + refresh): every guard is
#      false while destroy runs, extra args reach `tofu destroy`, the .tf files are byte-identical
#      afterwards, no .bak is left, kakao_known_hosts is cleared.
#   2. Cancelled at the prompt: exit 1, files byte-identical, no .bak, known_hosts kept (the
#      VMs still exist).
#   3. VPC left in PENDING_DELETE: exit 1, the wait advice is printed, files restored,
#      known_hosts cleared (no VM left).
#   3b. VPC left ACTIVE (delete rejected): the retry advice is printed, not the wait advice.
#   3c. VPC delete landing late (gone after the refresh): reported as fully destroyed, no abort.
#   (2 also checks that a cancel prints no VPC advice.)
#   4. SIGTERM during destroy: exit 143 and the guards are still restored.
#   4b. SIGQUIT during destroy: exit 131 and the guards are still restored.
#   4c. `state list` failing after destroy: non-zero exit, nothing reported as destroyed,
#      known_hosts kept, guards restored.
#   5. A leftover .bak, a missing terraform.tfstate, an empty state or a state with only data
#      sources stops the script before destroy runs, with no file touched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SRC="${ROOT}/csp/kakao-cloud/terraform"
GUARDED=(bastion.tf modules/compute/main.tf modules/loadbalancer/main.tf)
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

STATE_FULL='data.kakaocloud_images.images
kakaocloud_instance.bastion
kakaocloud_public_ip.bastion_public
kakaocloud_security_group.bastion_sg
module.compute.data.kakaocloud_images.images_all
module.compute.kakaocloud_instance.master[0]
module.compute.kakaocloud_instance.worker[0]
module.compute.kakaocloud_volume.nfs_data[0]
module.loadbalancer.kakaocloud_load_balancer.master_lb
module.loadbalancer.kakaocloud_public_ip.master_lb_public
module.network.kakaocloud_vpc.vpc'

mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/tofu" <<'STUB'
#!/bin/bash
# Stub tofu: state lives in ${STUB_DIR}/state.txt; behaviour is chosen by ${STUB_MODE}.
set -u
case "$1 ${2:-}" in
  "state list") [ ! -e "${STUB_DIR}/listfail" ] || { echo "stub: state locked" >&2; exit 1; }
                cat "${STUB_DIR}/state.txt" ;;
  "state show") grep -q 'kakaocloud_vpc' "${STUB_DIR}/state.txt" || { echo "stub: no such resource" >&2; exit 1; }
                echo '    id                    = "vpc-1"'
                echo "    provisioning_status   = \"${STUB_VPC_STATUS:-PENDING_DELETE}\""
                echo '        provisioning_status = "ACTIVE"' ;;
  refresh*) if [ "${STUB_MODE}" = vpclate ]; then   # the VPC delete landed after destroy gave up
              grep -v 'kakaocloud_vpc' "${STUB_DIR}/state.txt" > "${STUB_DIR}/s" || true
              mv "${STUB_DIR}/s" "${STUB_DIR}/state.txt"
            fi
            exit 0 ;;
  destroy*)
    shift
    printf '%s\n' "$*" > "${STUB_DIR}/destroy-args"
    cat bastion.tf modules/compute/main.tf modules/loadbalancer/main.tf \
      | grep -c 'prevent_destroy = false' > "${STUB_DIR}/released" || true
    case "${STUB_MODE}" in
      ok) grep -E 'data\.' "${STUB_DIR}/state.txt" > "${STUB_DIR}/s" || true
          mv "${STUB_DIR}/s" "${STUB_DIR}/state.txt"; exit 0 ;;
      listfail) : > "${STUB_DIR}/state.txt"; touch "${STUB_DIR}/listfail"; exit 0 ;;
      cancel) echo "Destroy cancelled."; exit 1 ;;
      vpc|vpclate) grep -E 'data\.|kakaocloud_vpc\.' "${STUB_DIR}/state.txt" > "${STUB_DIR}/s" || true
           mv "${STUB_DIR}/s" "${STUB_DIR}/state.txt"; exit 1 ;;
      term) kill -TERM "${PPID}"; sleep 1; exit 1 ;;
      quit) kill -QUIT "${PPID}"; sleep 1; exit 1 ;;
    esac ;;
  *) echo "stub tofu: unexpected args: $*" >&2; exit 2 ;;
esac
STUB
chmod +x "${WORK}/bin/tofu"

guards_in_repo="$(cat "${GUARDED[@]/#/${SRC}/}" | grep -c 'prevent_destroy = true')"
[ "${guards_in_repo}" -gt 0 ] || fail "no prevent_destroy guards found in the repo .tf files"

# setup <case>: fresh terraform-dir copy under ${WORK}/<case>; sets TF and STUB_DIR.
setup() {
  TF="${WORK}/$1/tf"
  STUB_DIR="${WORK}/$1/stub"
  mkdir -p "${TF}/modules/compute" "${TF}/modules/loadbalancer" "${STUB_DIR}"
  cp "${SRC}/destroy-all.sh" "${TF}/"
  for f in "${GUARDED[@]}"; do cp "${SRC}/${f}" "${TF}/${f}"; done
  echo '{}' > "${TF}/terraform.tfstate"
  echo 'host-key-of-a-destroyed-vm' > "${TF}/kakao_known_hosts"
  printf '%s\n' "${STATE_FULL}" > "${STUB_DIR}/state.txt"
  export STUB_DIR
}

# run <mode> [args...]: runs the script; sets RC and OUT.
run() {
  local mode="$1"; shift
  RC=0
  OUT="$(STUB_MODE="${mode}" PATH="${WORK}/bin:${PATH}" bash "${TF}/destroy-all.sh" "$@" 2>&1)" || RC=$?
}

assert_restored() {
  local f
  for f in "${GUARDED[@]}"; do
    cmp -s "${SRC}/${f}" "${TF}/${f}" || fail "$1: ${f} differs from the repo copy after the run"
    [ ! -e "${TF}/${f}.bak" ] || fail "$1: ${f}.bak left behind"
  done
}

# 1. success
setup ok
run ok -auto-approve -lock-timeout=1s
[ "${RC}" -eq 0 ] || fail "ok: exit ${RC}: ${OUT}"
[ "$(cat "${STUB_DIR}/released")" -eq "${guards_in_repo}" ] \
  || fail "ok: ${guards_in_repo} guards expected false during destroy, saw $(cat "${STUB_DIR}/released")"
[ "$(cat "${STUB_DIR}/destroy-args")" = "-auto-approve -lock-timeout=1s" ] \
  || fail "ok: extra args not passed through: $(cat "${STUB_DIR}/destroy-args")"
assert_restored ok
[ ! -s "${TF}/kakao_known_hosts" ] || fail "ok: kakao_known_hosts not cleared"
grep -q 'All managed resources destroyed' <<<"${OUT}" || fail "ok: no completion line"
grep -q "guards restored: ${guards_in_repo} of ${guards_in_repo}" <<<"${OUT}" || fail "ok: restore line missing"

# 2. cancelled at the prompt
setup cancel
run cancel
[ "${RC}" -eq 1 ] || fail "cancel: expected exit 1, got ${RC}"
assert_restored cancel
[ -s "${TF}/kakao_known_hosts" ] || fail "cancel: kakao_known_hosts cleared although the VMs remain"
! grep -q 'VPC is' <<<"${OUT}" || fail "cancel: VPC advice printed after a cancel"

# 3. VPC stuck in PENDING_DELETE
setup vpc
run vpc
[ "${RC}" -eq 1 ] || fail "vpc: expected exit 1, got ${RC}"
assert_restored vpc
grep -q 'VPC is PENDING_DELETE' <<<"${OUT}" || fail "vpc: PENDING_DELETE advice not printed"
[ ! -s "${TF}/kakao_known_hosts" ] || fail "vpc: kakao_known_hosts kept although no VM is left"

# 3b. VPC still ACTIVE (delete rejected)
setup vpcactive
STUB_VPC_STATUS=ACTIVE run vpc
[ "${RC}" -eq 1 ] || fail "vpcactive: expected exit 1, got ${RC}"
assert_restored vpcactive
grep -q 'VPC is still ACTIVE' <<<"${OUT}" || fail "vpcactive: retry advice not printed"
! grep -q 'VPC is PENDING_DELETE' <<<"${OUT}" || fail "vpcactive: PENDING_DELETE advice printed for an ACTIVE VPC"

# 3c. VPC delete lands late: destroy fails, but the refresh no longer finds the VPC
setup vpclate
run vpclate
[ "${RC}" -eq 1 ] || fail "vpclate: expected tofu's exit 1, got ${RC}: ${OUT}"
assert_restored vpclate
grep -q 'All managed resources destroyed' <<<"${OUT}" || fail "vpclate: late VPC delete not reported as done"
[ ! -s "${TF}/kakao_known_hosts" ] || fail "vpclate: kakao_known_hosts not cleared"

# 4. SIGTERM while destroy runs
setup term
run term
[ "${RC}" -eq 143 ] || fail "term: expected exit 143, got ${RC}"
assert_restored term

# 4b. SIGQUIT while destroy runs
setup quit
run quit
[ "${RC}" -eq 131 ] || fail "quit: expected exit 131, got ${RC}"
assert_restored quit

# 4c. state list fails after destroy
setup listfail
run listfail
[ "${RC}" -ne 0 ] || fail "listfail: exit 0 although the state could not be read"
assert_restored listfail
grep -q 'could not read the state' <<<"${OUT}" || fail "listfail: no warning"
! grep -q 'All managed resources destroyed' <<<"${OUT}" || fail "listfail: reported success without reading the state"
[ -s "${TF}/kakao_known_hosts" ] || fail "listfail: kakao_known_hosts cleared without reading the state"

# 5a. leftover .bak
setup bak
echo 'operator edit' > "${TF}/bastion.tf.bak"
run ok
[ "${RC}" -ne 0 ] || fail "bak: a leftover .bak did not stop the run"
[ ! -e "${STUB_DIR}/destroy-args" ] || fail "bak: destroy ran despite the leftover .bak"
[ "$(cat "${TF}/bastion.tf.bak")" = "operator edit" ] || fail "bak: the leftover .bak was modified"
for f in "${GUARDED[@]}"; do cmp -s "${SRC}/${f}" "${TF}/${f}" || fail "bak: ${f} touched"; done

# 5b. no terraform.tfstate
setup nostate
rm "${TF}/terraform.tfstate"
run ok
[ "${RC}" -ne 0 ] || fail "nostate: ran without terraform.tfstate"
[ ! -e "${STUB_DIR}/destroy-args" ] || fail "nostate: destroy ran"

# 5c. empty state
setup emptystate
: > "${STUB_DIR}/state.txt"
run ok
[ "${RC}" -ne 0 ] || fail "emptystate: ran with an empty state"
[ ! -e "${STUB_DIR}/destroy-args" ] || fail "emptystate: destroy ran"
assert_restored emptystate

# 5d. only data sources in state
setup empty
grep 'data\.' <<<"${STATE_FULL}" > "${STUB_DIR}/state.txt"
run ok
[ "${RC}" -ne 0 ] || fail "empty: ran with nothing to destroy"
[ ! -e "${STUB_DIR}/destroy-args" ] || fail "empty: destroy ran"
assert_restored empty

echo "OK: destroy-all.sh harness, 12 cases (${guards_in_repo} guards)"
