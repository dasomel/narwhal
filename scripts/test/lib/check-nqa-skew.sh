#!/bin/bash
# R259: nfs-quota-agent chart/image skew guard (scripts/common/lib-nqa.sh) + 05 wiring.
# Run from the repo root. helm and kubectl are shell-function fakes (offline, deterministic).
set -euo pipefail
# shellcheck source=/dev/null
source scripts/common/lib-nqa.sh

T=$(mktemp -d); trap 'rm -rf "${T}"' EXIT
base='          args:
            - --nfs-base-path=/export
            - --state-dir=/var/lib/nfs-quota-agent
            - --enable-policy'
printf '%s\n' "${base}" >"${T}/good"                              # flags of the v0.5.0 chart
printf '%s\n            - --default-quota=1Gi\n' "${base}" >"${T}/stale"  # the live Kakao bundle (387b057)
printf '%s\n            - --enable-events\n' "${base}" >"${T}/events"
printf '%s\n' "${base}" | grep -v state-dir >"${T}/nostate"

# helm fake: `helm template <rel> <chart-file> ...` prints the fixture; chart "broken" fails.
# shellcheck disable=SC2329 # invoked by nqa_check_skew
helm() {
  [ "$1" = template ] || return 99
  [ -f "$3" ] || { echo "Error: path not found" >&2; return 1; }
  cat "$3"
}

fail=0
expect() { # <rc> <chart> <tag>
  local want="$1" rc=0
  nqa_check_skew "${T}/$2" "$3" --set a=b 2>/dev/null || rc=$?
  if [ "${rc}" -ne "${want}" ]; then echo "FAIL: skew($2,$3) rc=${rc} want ${want}"; fail=1; fi
}
expect 0 good v0.5.0
expect 0 good v0.4.0
expect 0 nostate v0.4.0
expect 1 stale v0.5.0    # the live Kakao crash: --default-quota is in no release
expect 1 stale v0.4.3
expect 1 events v0.4.3   # --enable-events is v0.5.0+
expect 0 events v0.5.0
expect 1 good v0.3.0     # not in the table: fail closed
expect 1 good v0.10.0
expect 1 good main
expect 1 missing v0.5.0  # chart does not render

# Every pinned flag set must really be the table's content (default-quota/enforce-max-quota never come back).
case " $(nqa_known_flags v0.5.0) " in *" default-quota "*) echo "FAIL: default-quota in table"; fail=1 ;; esac

# kubectl fake for nqa_unhealthy_pods: prints $NQA_FAKE_PODS (name<TAB>ready<TAB>restarts<TAB>lastReason).
# shellcheck disable=SC2329 # invoked by nqa_unhealthy_pods
kubectl() { printf '%b' "${NQA_FAKE_PODS}"; }
pods() { # <rc> <label> <pods>
  local want="$1" rc=0
  NQA_FAKE_PODS="$3" nqa_unhealthy_pods ns >/dev/null || rc=$?
  if [ "${rc}" -ne "${want}" ]; then echo "FAIL: pods($2) rc=${rc} want ${want} (0 = unhealthy)"; fail=1; fi
}
pods 1 healthy 'a\ttrue\t0\t\n'
pods 0 restarting-not-ready 'a\tfalse\t5\tError\n'
pods 0 ready-but-restarted 'a\ttrue\t1\t\n'
pods 0 lastState-error 'a\ttrue\t0\tError\n'
pods 0 no-pods ''

f=scripts/cluster/05-nfs-quota-agent.sh
grep -Fq 'source /home/vagrant/scripts/common/lib-nqa.sh' "${f}" || { echo "FAIL: 05 does not source lib-nqa.sh"; fail=1; }
a=$(grep -n 'nqa_check_skew "' "${f}" | head -1 | cut -d: -f1)
b=$(grep -n 'helm upgrade --install' "${f}" | head -1 | cut -d: -f1)
[ -n "${a}" ] && [ -n "${b}" ] && [ "${a}" -lt "${b}" ] || { echo "FAIL: skew guard must run before helm upgrade"; fail=1; }
# shellcheck disable=SC2016 # literal match
grep -Fq '"${HELM_ARGS[@]}"' "${f}" || { echo "FAIL: guard and install must share HELM_ARGS"; fail=1; }
grep -Fq 'rollout status daemonset/nfs-quota-agent' "${f}" || { echo "FAIL: 05 must wait on the DaemonSet"; fail=1; }
! grep -Fq 'rollout status deployment/nfs-quota-agent' "${f}" || { echo "FAIL: 05 still waits on a nonexistent Deployment"; fail=1; }
! grep -Fq 'not ready yet' "${f}" || { echo "FAIL: 05 must not downgrade a failed rollout to a WARN"; fail=1; }
grep -Fq 'nqa_unhealthy_pods' "${f}" || { echo "FAIL: 05 must fail on restarted/not-Ready pods"; fail=1; }
grep -Fq -- '--previous' "${f}" || { echo "FAIL: 05 must print logs --previous on failure"; fail=1; }
grep -Eq '\| *grep -q' "${f}" scripts/common/lib-nqa.sh && { echo "FAIL: no '| grep -q' under pipefail"; fail=1; }
{ grep -Fq 'findmnt -no OPTIONS' "${f}" && grep -Fq 'prjquota' "${f}"; } || { echo "FAIL: 05 must detect prjquota (D-nqa-prjquota)"; fail=1; }
exit "${fail}"
