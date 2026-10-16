#!/usr/bin/env bash
# Behavioral check for up.sh recover_dpkg (R258): extract the real function and run it against a
# fake `vagrant` — dirty VM -> `dpkg --configure -a` runs, clean VM -> it does not, ssh failure
# is never fatal.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/bin"
cat > "${tmp}/bin/vagrant" <<'F'
#!/usr/bin/env bash
# args: ssh <vm> -c <cmd>
cmd="$4"
echo "${cmd}" >> "${FAKE_LOG}"
case "${cmd}" in
  *"find /var/lib/dpkg/updates"*) [ "${FAKE_SSH_FAIL:-0}" = 1 ] && exit 255; printf '%s' "${FAKE_PENDING:-}" ;;
  *"dpkg --configure -a"*) exit "${FAKE_CONFIGURE_RC:-0}" ;;
esac
F
chmod +x "${tmp}/bin/vagrant"

fn=$(sed -n '/^recover_dpkg() {/,/^}/p' "${root}/scripts/up.sh")
[ -n "${fn}" ] || { echo "recover_dpkg not found in up.sh" >&2; exit 1; }
say() { :; }
eval "${fn}"
export PATH="${tmp}/bin:${PATH}" FAKE_LOG="${tmp}/log" FAKE_PENDING="" FAKE_SSH_FAIL=0 FAKE_CONFIGURE_RC=0

: > "${FAKE_LOG}"; export FAKE_PENDING=""
recover_dpkg master-1
! grep -Fq "dpkg --configure -a" "${FAKE_LOG}" || { echo "clean VM must not be touched" >&2; exit 1; }

: > "${FAKE_LOG}"; export FAKE_PENDING="/var/lib/dpkg/updates/0001"
recover_dpkg master-1
grep -Fq "dpkg --configure -a" "${FAKE_LOG}" || { echo "dirty VM must run dpkg --configure -a" >&2; exit 1; }

: > "${FAKE_LOG}"; export FAKE_PENDING="/var/lib/dpkg/updates/0001" FAKE_CONFIGURE_RC=1
recover_dpkg master-1 || { echo "configure failure must not be fatal" >&2; exit 1; }

: > "${FAKE_LOG}"; export FAKE_SSH_FAIL=1
recover_dpkg master-1 || { echo "ssh failure must not be fatal" >&2; exit 1; }
echo "recover_dpkg behavior OK"
