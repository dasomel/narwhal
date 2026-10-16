#!/bin/bash
# R259: nfs-quota-agent chart/image skew guard (scripts/common/lib-nqa.sh) + 05 wiring.
# Run from the repo root. helm and kubectl are shell-function fakes (offline, deterministic).
set -euo pipefail
# shellcheck source=/dev/null
source scripts/common/lib-nqa.sh

T=$(mktemp -d); trap 'rm -rf "${T}"' EXIT
base='spec:
  containers:
    - name: a
      args:
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

# nqa_judge <before> <after> (name<TAB>ready<TAB>restarts<TAB>waiting): rc 0 = healthy.
judge() { # <rc> <label> <before> <after>
  local want="$1" rc=0
  nqa_judge "$(printf '%b' "$3")" "$(printf '%b' "$4")" >/dev/null || rc=$?
  if [ "${rc}" -ne "${want}" ]; then echo "FAIL: judge($2) rc=${rc} want ${want}"; fail=1; fi
}
judge 0 healthy 'a\ttrue\t0\t\n' 'a\ttrue\t0\t\n'
judge 0 old-restart-stable 'a\ttrue\t3\t\n' 'a\ttrue\t3\t\n'   # re-run against a healthy pod
judge 1 pod-replaced 'a\ttrue\t0\t\n' 'b\ttrue\t0\t\n'
judge 1 pod-added 'a\ttrue\t0\t\n' 'a\ttrue\t0\t\nb\ttrue\t0\t\n'
judge 1 restart-in-window 'a\ttrue\t0\t\n' 'a\ttrue\t1\t\n'
judge 1 not-ready 'a\ttrue\t0\t\n' 'a\tfalse\t0\t\n'
judge 1 crashloop 'a\ttrue\t2\t\n' 'a\ttrue\t2\tCrashLoopBackOff\n'
judge 1 waiting-error 'a\ttrue\t2\t\n' 'a\ttrue\t2\tError\n'
judge 1 no-pods 'a\ttrue\t0\t\n' ''

# Rendered-args parsing: block, quoted and flow style, with and without PyYAML.
q='spec:
  containers:
    - name: a
      args:
        - "--quoted-flag=1"
        - '"'--single-flag'"'
        - --block-flag
      other: ["--not-an-arg"]'
f1='spec:
  containers:
    - name: a
      args: ["--flow-a", "--flow-b=x"]'
want_q=$'block-flag\nquoted-flag\nsingle-flag'
want_f=$'flow-a\nflow-b'
[ "$(printf '%s\n' "${q}" | nqa_rendered_flags)" = "${want_q}" ] || { echo "FAIL: quoted/block args parse"; fail=1; }
[ "$(printf '%s\n' "${f1}" | nqa_rendered_flags)" = "${want_f}" ] || { echo "FAIL: flow args parse"; fail=1; }
# regex fallback: hide PyYAML by shadowing python3 with a failing stub.
# shellcheck disable=SC2329 # invoked by nqa_rendered_flags
python3() { return 1; }
[ "$(printf '%s\n' "${q}" | nqa_rendered_flags | grep -v not-an-arg)" = "${want_q}" ] || { echo "FAIL: fallback quoted/block parse"; fail=1; }
[ "$(printf '%s\n' "${f1}" | nqa_rendered_flags)" = "${want_f}" ] || { echo "FAIL: fallback flow parse"; fail=1; }
unset -f python3
printf '%s\n            - "--default-quota=1Gi"\n' "${base}" >"${T}/quoted"
expect 1 quoted v0.5.0
printf 'spec:\n  containers: [unclosed\n    - : bad\n' >"${T}/invalid"
expect 1 invalid v0.5.0   # unparsable render must fail closed, not approve an empty flag list
: >"${T}/empty"
expect 1 empty v0.5.0
printf 'spec:\n  containers:\n    - name: a\n      args:\n        - --state-dir=x\n      livenessProbe:\n        exec:\n          command: ["--probe-only"]\n' >"${T}/probe"
expect 0 probe v0.5.0     # args under a probe are not container flags

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
grep -Fq 'nqa_wait_stable' "${f}" || { echo "FAIL: 05 must fail on restarted/not-Ready pods"; fail=1; }
grep -Fq -- '--previous' "${f}" || { echo "FAIL: 05 must print logs --previous on failure"; fail=1; }
grep -Eq '\| *grep -q' "${f}" scripts/common/lib-nqa.sh && { echo "FAIL: no '| grep -q' under pipefail"; fail=1; }
{ grep -Fq 'findmnt -no OPTIONS' "${f}" && grep -Fq 'prjquota' "${f}"; } || { echo "FAIL: 05 must detect prjquota (D-nqa-prjquota)"; fail=1; }
exit "${fail}"
