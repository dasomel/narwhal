#!/bin/bash
# nfs-quota-agent chart/image skew guard, sourced by 05-nfs-quota-agent.sh.
#
# The bundled chart is a source-tree snapshot (NFS_QUOTA_AGENT_COMMIT) and its DaemonSet passes
# flags that only newer images define. On the live Kakao cluster a v0.3.0 image ran with a chart
# that passes --state-dir: the binary exits with "flag provided but not defined: -state-dir" and
# the pod crash-looped ~14,000 times over 50 days while the install script reported success.
# D-nqa-skew: refuse the mismatch up front instead of rolling out a pod that cannot start.

# Oldest image whose CLI defines every flag the bundled chart passes (--state-dir landed in v0.4.0).
NQA_MIN_IMAGE_VERSION="${NQA_MIN_IMAGE_VERSION:-v0.4.0}"

# nqa_check_skew <chart-dir> <image-tag>
# rc 0 = compatible (or tag is not a semver, e.g. a digest/branch build: cannot judge, allowed),
# rc 1 = chart passes --state-dir but the tag is older than NQA_MIN_IMAGE_VERSION.
nqa_check_skew() {
  local chart_dir="$1" tag="$2" oldest
  if ! grep -rqs -e '--state-dir' "${chart_dir}/templates"; then
    return 0
  fi
  case "${tag}" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *) return 0 ;;
  esac
  oldest=$(printf '%s\n%s\n' "${NQA_MIN_IMAGE_VERSION}" "${tag}" | sort -V | head -n1)
  if [ "${oldest}" != "${NQA_MIN_IMAGE_VERSION}" ]; then
    echo "ERROR: nfs-quota-agent image ${tag} is older than ${NQA_MIN_IMAGE_VERSION}, but the bundled chart passes --state-dir." >&2
    echo "       The pod would exit 'flag provided but not defined: -state-dir' and crash-loop. Use NFS_QUOTA_AGENT_VERSION>=${NQA_MIN_IMAGE_VERSION}." >&2
    return 1
  fi
  return 0
}

# nqa_crashlooping <namespace> -- rc 0 when any pod in the namespace waits in CrashLoopBackOff.
nqa_crashlooping() {
  kubectl get pods -n "$1" \
    -o jsonpath='{range .items[*].status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}' 2>/dev/null |
    grep -qx 'CrashLoopBackOff'
}
