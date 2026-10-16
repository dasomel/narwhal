#!/bin/bash
# nfs-quota-agent chart/image skew guard, sourced by 05-nfs-quota-agent.sh.
#
# The chart is a source-tree snapshot (NFS_QUOTA_AGENT_COMMIT, bundled as nfs-quota-agent-0.0.0-main.tgz
# whose file name never changes) and its DaemonSet passes CLI flags the image must define. A mismatch
# does not fail the Helm release: the pod just exits "flag provided but not defined" and crash-loops.
# Live Kakao, 2026-10-16: (1) a v0.3.0 image under a chart passing --state-dir (v0.4.0+), ~14,000
# restarts in 50 days; (2) after moving to v0.5.0 the bundle on master-1 was still the 2026-08-23
# snapshot, whose policy.enabled renders --default-quota (removed in v0.4.0) -> same crash loop.
# D-nqa-skew: render the chart exactly as 05 will install it and require every flag it passes to be
# in a pinned per-version table; unknown versions fail closed (add a row when bumping the image).

# Flags defined by `nfs-quota-agent run` (cmd/nfs-quota-agent/main.go runAgent), per release tag.
# --default-quota / --enforce-max-quota do not exist in any release here (removed in v0.4.0).
NQA_FLAGS_V040="audit-log-path cleanup-dry-run cleanup-interval enable-audit enable-auto-cleanup enable-history enable-policy enable-quota-policy enable-ui ha-active-file history-interval history-path history-retention kubeconfig metrics-addr nfs-base-path nfs-server-path orphan-grace-period process-all-nfs provisioner-name quota-policy-single-writer state-dir sync-interval ui-addr"
NQA_FLAGS_V050="${NQA_FLAGS_V040} enable-events"

# nqa_known_flags <image-tag> -- prints the supported flags, rc 1 for a tag not in the table.
nqa_known_flags() {
  case "$1" in
    v0.4.0 | v0.4.1 | v0.4.2 | v0.4.3) echo "${NQA_FLAGS_V040}" ;;
    v0.5.0) echo "${NQA_FLAGS_V050}" ;;
    *) return 1 ;;
  esac
}

# nqa_rendered_flags -- stdin: rendered manifests; prints the distinct --flag names (no dashes) that
# container args/command pass. python3+PyYAML reads block, quoted and flow-style lists alike; the regex
# fallback covers `- --x`, `- "--x"` and `args: ["--x", ...]` lines.
nqa_rendered_flags() {
  local doc
  doc=$(cat)
  if printf '' | python3 -c 'import yaml' 2>/dev/null; then
    printf '%s\n' "${doc}" | python3 -c '
import sys, yaml
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("args", "command") and isinstance(v, list):
                for a in v:
                    if isinstance(a, str) and a.startswith("--"):
                        print(a[2:].split("=", 1)[0])
            else:
                walk(v)
    elif isinstance(o, list):
        for i in o:
            walk(i)
for d in yaml.safe_load_all(sys.stdin):
    walk(d)
' | sort -u
  else
    printf '%s\n' "${doc}" | grep -E "^[[:space:]]*-[[:space:]]+[\"']?--|(args|command):[[:space:]]*\[" |
      grep -oE "(^|[-\[,])[[:space:]]*[\"']?--[a-z][a-z0-9-]*" | sed -E "s/^.*--//" | sort -u
  fi
}

# nqa_check_skew <chart-dir> <image-tag> [helm args...]
# Renders the chart with the install's own helm args and rc 1 if it passes a flag the image lacks,
# the tag is not in the table, or the chart does not render.
nqa_check_skew() {
  local chart_dir="$1" tag="$2" known rendered flag bad=""
  shift 2
  if ! known=$(nqa_known_flags "${tag}"); then
    echo "ERROR: nfs-quota-agent image ${tag} has no known-flags row in scripts/common/lib-nqa.sh; refusing to deploy unverified." >&2
    echo "       Add the row from the release's cmd/nfs-quota-agent/main.go runAgent flags (D-nqa-skew)." >&2
    return 1
  fi
  if ! rendered=$(helm template nfs-quota-agent "${chart_dir}" "$@" 2>&1); then
    echo "ERROR: helm template of the nfs-quota-agent chart failed:" >&2
    echo "${rendered}" >&2
    return 1
  fi
  for flag in $(printf '%s\n' "${rendered}" | nqa_rendered_flags); do
    case " ${known} " in
      *" ${flag} "*) ;;
      *) bad="${bad} --${flag}" ;;
    esac
  done
  if [ -n "${bad}" ]; then
    echo "ERROR: the bundled nfs-quota-agent chart passes flag(s)${bad} that image ${tag} does not define." >&2
    echo "       The pod would exit 'flag provided but not defined' and crash-loop. The chart in the airgap bundle is stale:" >&2
    echo "       rebuild nfs-quota-agent-0.0.0-main.tgz (scripts/airgap/07-save-binaries.sh, pin NFS_QUOTA_AGENT_COMMIT) and re-sync it, or fix the --set values." >&2
    return 1
  fi
  return 0
}

# nqa_pod_state <namespace> -- one line per pod: name, ready, restartCount, waiting reason (tab-separated).
nqa_pod_state() {
  kubectl get pods -n "$1" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].ready}{"\t"}{.status.containerStatuses[0].restartCount}{"\t"}{.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}' 2>/dev/null || true
}

# nqa_judge <state-before> <state-after> -- rc 0 healthy; rc 1 (reasons on stdout) when there is no pod,
# a pod is not Ready or waits in CrashLoopBackOff/Error at the end, or its restartCount grew in between.
# An old restart that did not move is fine: a one-off startup restart must not fail a healthy pod.
nqa_judge() {
  local before="$1" after="$2" out
  if [ -z "${after}" ]; then
    echo "no pods found"
    return 1
  fi
  out=$(printf '%s\n--\n%s\n' "${before}" "${after}" | awk -F'\t' '
    /^--$/ { second = 1; next }
    !second { base[$1] = $3 + 0; next }
    { if ($2 != "true") print $1 ": not Ready"
      if (($1 in base) && $3 + 0 > base[$1]) print $1 ": restarted during the window (" base[$1] " -> " $3 ")"
      if ($4 == "CrashLoopBackOff" || $4 == "Error") print $1 ": waiting " $4 }')
  [ -z "${out}" ] || { printf '%s\n' "${out}"; return 1; }
}

# nqa_wait_stable <namespace> [window-seconds] -- snapshot, wait NQA_STABLE_WINDOW (default 60s), judge.
nqa_wait_stable() {
  local before
  before=$(nqa_pod_state "$1")
  sleep "${2:-${NQA_STABLE_WINDOW:-60}}"
  nqa_judge "${before}" "$(nqa_pod_state "$1")"
}
