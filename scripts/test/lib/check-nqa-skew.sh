#!/bin/bash
# R259: nfs-quota-agent chart/image skew guard (scripts/common/lib-nqa.sh) + 05 wiring.
# Run from the repo root.
set -euo pipefail
# shellcheck source=/dev/null
source scripts/common/lib-nqa.sh

T=$(mktemp -d); trap 'rm -rf "${T}"' EXIT
mkdir -p "${T}/new/templates" "${T}/old/templates"
echo '            - --state-dir={{ .Values.state.path }}' >"${T}/new/templates/daemonset.yaml"
echo '            - --sync-interval=30s' >"${T}/old/templates/daemonset.yaml"

fail=0
expect() { # <rc> <chart> <tag>
  local want="$1" rc=0
  nqa_check_skew "${T}/$2" "$3" 2>/dev/null || rc=$?
  if [ "${rc}" -ne "${want}" ]; then echo "FAIL: skew($2,$3) rc=${rc} want ${want}"; fail=1; fi
}
expect 1 new v0.3.0   # the live Kakao crash: --state-dir chart on a v0.3.0 image
expect 1 new v0.2.1
expect 0 new v0.4.0
expect 0 new v0.5.0
expect 0 new v0.10.0  # version sort, not string sort
expect 0 new main     # non-semver: cannot judge, allowed
expect 0 old v0.3.0   # a chart without --state-dir does not need v0.4.0

f=scripts/cluster/05-nfs-quota-agent.sh
grep -Fq 'source /home/vagrant/scripts/common/lib-nqa.sh' "${f}" || { echo "FAIL: 05 does not source lib-nqa.sh"; fail=1; }
a=$(grep -n 'nqa_check_skew "' "${f}" | head -1 | cut -d: -f1)
b=$(grep -n 'helm upgrade --install' "${f}" | head -1 | cut -d: -f1)
[ -n "${a}" ] && [ -n "${b}" ] && [ "${a}" -lt "${b}" ] || { echo "FAIL: skew guard must run before helm upgrade"; fail=1; }
grep -Fq 'rollout status daemonset/nfs-quota-agent' "${f}" || { echo "FAIL: 05 must wait on the DaemonSet"; fail=1; }
! grep -Fq 'rollout status deployment/nfs-quota-agent' "${f}" || { echo "FAIL: 05 still waits on a nonexistent Deployment"; fail=1; }
grep -Fq 'nqa_crashlooping' "${f}" || { echo "FAIL: 05 must fail on CrashLoopBackOff"; fail=1; }
exit "${fail}"
