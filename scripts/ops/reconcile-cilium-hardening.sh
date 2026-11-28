#!/bin/bash
# reconcile-cilium-hardening.sh
# Existing-cluster upgrade helper for Cilium 1.19.4 pod-security & bpffs hardening
# (D-CILIUM-BPFFS-SELINUX, D-CILIUM-HARDENING, D-CILIUM-RECONCILE-BASE).
#
# Run on master-1 with sudo. Supports --dry-run (default)
# and --apply. Reads live user-supplied Helm values and deep-merges only the hardening
# overlay via yq v4 without using helm --reuse-values.
set -euo pipefail

export PATH="${PATH}:/usr/local/bin"

MODE="dry-run"
SKIP_HOST_BPF=false
KUBECTL="${KUBECTL:-kubectl}"
HELM="${HELM:-helm}"
YQ="${YQ:-yq}"
CILIUM_CLI="${CILIUM_CLI:-cilium}"
CILIUM_VERSION="${CILIUM_VERSION:-1.19.4}"
CILIUM_CHART_TGZ="${CILIUM_CHART_TGZ:-}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) MODE="dry-run" ;;
    --apply) MODE="apply" ;;
    --skip-host-bpf) SKIP_HOST_BPF=true ;;
    *)
      echo "Usage: $0 [--dry-run|--apply] [--skip-host-bpf]" >&2
      exit 1
      ;;
  esac
  shift
done

if ! command -v "${YQ}" >/dev/null 2>&1 || ! "${YQ}" --version 2>&1 | grep -q 'mikefarah'; then
  echo "ERROR: mikefarah/yq v4 is required" >&2
  exit 1
fi

if [ -z "${CILIUM_CHART_TGZ}" ]; then
  # shellcheck source=/dev/null
  if [ -f /home/vagrant/scripts/common/lib-charts.sh ]; then
    source /home/vagrant/scripts/common/lib-charts.sh
    CILIUM_CHART_TGZ="$(chart cilium)"
  fi
fi
[ -f "${CILIUM_CHART_TGZ}" ] || {
  echo "ERROR: cilium chart archive not found (set CILIUM_CHART_TGZ)" >&2
  exit 1
}

if [ "${SKIP_HOST_BPF}" = "false" ]; then
  # D-CILIUM-BPFFS-UNIT: no mount unit — systemd mounts /sys/fs/bpf as an API file system
  # and refuses a sys-fs-bpf.mount for it (systemd 259, 2026-11-28); only verify below.
  if ! findmnt -rn -t bpf -T /sys/fs/bpf >/dev/null 2>&1; then
    if [ "${MODE}" = "dry-run" ]; then
      echo "WARN: /sys/fs/bpf is not currently mounted as type bpf (systemd normally mounts it at boot)" >&2
    else
      echo "ERROR: /sys/fs/bpf is not mounted as type bpf; aborting before disabling bpf.autoMount" >&2
      exit 1
    fi
  fi
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
tar xzf "${CILIUM_CHART_TGZ}" -C "${WORK}"

# D-CILIUM-RECONCILE-BASE: read live user-supplied values and deep-merge only the hardening
# overlay via yq v4 so existing settings (e.g. routingMode, tunnelProtocol, IPAM, operator.replicas)
# survive without --reuse-values. Cost: requires mikefarah/yq v4; escape: pass a hand-merged
# values file directly to `helm upgrade cilium ... -f <file>`.
"${HELM}" get values cilium -n kube-system -o yaml > "${WORK}/live-values.yaml"
if ! "${YQ}" '.' "${WORK}/live-values.yaml" >/dev/null 2>&1; then
  echo "ERROR: failed to parse live cilium values from helm get values" >&2
  exit 1
fi
if [ "$("${YQ}" 'tag' "${WORK}/live-values.yaml")" = "!!null" ]; then
  printf '{}\n' > "${WORK}/live-values.yaml"
fi

cat > "${WORK}/hardening-overlay.yaml" <<'EOF'
bpf:
  autoMount:
    enabled: false
podSecurityContext:
  seccompProfile:
    type: RuntimeDefault
securityContext:
  seLinuxOptions: null
envoy:
  podSecurityContext:
    seccompProfile:
      type: RuntimeDefault
    runAsUser: 0
    runAsGroup: 10100
  securityContext:
    seLinuxOptions: null
  extraHostPathMounts:
    - name: bpf-maps
      mountPath: /sys/fs/bpf
      hostPath: /sys/fs/bpf
      hostPathType: DirectoryOrCreate
      readOnly: false
      mountPropagation: HostToContainer
hubble:
  ui:
    securityContext:
      runAsNonRoot: true
      seccompProfile:
        type: RuntimeDefault
    frontend:
      securityContext:
        capabilities:
          drop:
            - ALL
    backend:
      securityContext:
        capabilities:
          drop:
            - ALL
operator:
  podSecurityContext:
    seccompProfile:
      type: RuntimeDefault
    runAsNonRoot: true
    runAsUser: 10100
    runAsGroup: 10100
EOF

"${YQ}" eval-all '. as $item ireduce ({}; . * $item)' "${WORK}/live-values.yaml" "${WORK}/hardening-overlay.yaml" > "${WORK}/merged-values.yaml"

if [ "${MODE}" = "dry-run" ]; then
  echo "=== Values diff (live vs merged hardening overlay) ==="
  diff_rc=0
  diff -u "${WORK}/live-values.yaml" "${WORK}/merged-values.yaml" || diff_rc=$?
  if [ "${diff_rc}" -gt 1 ]; then
    echo "ERROR: diff failed with exit code ${diff_rc}" >&2
    exit "${diff_rc}"
  fi
  echo "=== Dry-run Cilium upgrade (server-side) ==="
  "${HELM}" upgrade cilium "${WORK}/cilium" -n kube-system --version "${CILIUM_VERSION}" -f "${WORK}/merged-values.yaml" --dry-run=server
  exit 0
fi

PREV_REV="$("${HELM}" list -n kube-system -f '^cilium$' -o json | "${YQ}" -r '.[0].revision // ""')"
if [[ ! "${PREV_REV}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: could not determine current cilium Helm revision (got '${PREV_REV}')" >&2
  exit 1
fi

echo "=== Applying Cilium hardening upgrade (previous revision=${PREV_REV}) ==="
apply_failed=false
if ! "${HELM}" upgrade cilium "${WORK}/cilium" -n kube-system --version "${CILIUM_VERSION}" -f "${WORK}/merged-values.yaml"; then
  apply_failed=true
else
  for res in ds/cilium ds/cilium-envoy deploy/cilium-operator deploy/hubble-ui; do
    if ! "${KUBECTL}" -n kube-system rollout status "${res}" --timeout=180s; then
      apply_failed=true
      break
    fi
  done
  if [ "${apply_failed}" = "false" ] && command -v "${CILIUM_CLI}" >/dev/null 2>&1; then
    if ! "${CILIUM_CLI}" status --wait --wait-duration 120s; then
      apply_failed=true
    fi
  fi
fi

if [ "${apply_failed}" = "true" ]; then
  echo "ERROR: Cilium hardening upgrade/rollout failed; not auto-rolling back CNI." >&2
  echo "To roll back manually, run:" >&2
  echo "  helm rollback cilium ${PREV_REV} -n kube-system --wait" >&2
  exit 1
fi
