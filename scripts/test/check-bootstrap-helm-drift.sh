#!/usr/bin/env bash
set -euo pipefail

#=============================================================================
# check-bootstrap-helm-drift.sh
#
# LIVE check: verifies whether running Helm releases installed by bootstrap
# scripts (not owned by ArgoCD Applications) match the literal --set,
# --set-string, and --set-json flags declared in the provisioning scripts.
# Values provided solely via values files (-f) or heredocs are not parsed and
# are reported as UNCHECKED rather than falsely claimed as in sync.
#
# Read-only operations:
#   - kubectl get nodes (cluster readiness check)
#   - helm get values <release> -n <ns> -o json (fetch live user-supplied values)
#
# Known limits (static text scan, not a shell parser):
#   - The offline completeness check finds helm invocations by the literal tokens
#     `helm` ... `upgrade` on a logical line. A command assembled dynamically without
#     those tokens is not seen; a helm command quoted inside a multi-line string may be
#     counted and then needs an allowlist entry (it fails closed in that direction).
#   - Values passed through `-f <file>` are not compared (reported as UNCHECKED).
#
# Exit codes:
#   0: IN SYNC / UNCHECKED (no drift detected; UNCHECKED releases counted in summary)
#   1: DRIFT DETECTED (one or more keys differ from script declarations)
#   2: CANNOT RUN (cluster unreachable, helm failed, or release missing)
#
# Environment variables:
#   HELM_CMD    Helm command override (default: helm)
#               e.g. HELM_CMD="sudo helm --kubeconfig /etc/kubernetes/admin.conf"
#               Note: arguments with spaces are not supported.
#   KUBECTL_CMD Kubectl command override (default: kubectl)
#               e.g. KUBECTL_CMD="sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"
#               Note: arguments with spaces are not supported.
#
# Usage:
#   scripts/test/check-bootstrap-helm-drift.sh
#   scripts/test/check-bootstrap-helm-drift.sh --release=apisix-ingress-controller
#   vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/test/check-bootstrap-helm-drift.sh"
#   vagrant ssh master-1 -c "HELM_CMD='sudo helm --kubeconfig /etc/kubernetes/admin.conf' KUBECTL_CMD='sudo kubectl --kubeconfig /etc/kubernetes/admin.conf' bash /home/vagrant/scripts/test/check-bootstrap-helm-drift.sh"
#=============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_HELPER="${SCRIPT_DIR}/lib/check-bootstrap-helm-drift.py"

TARGET_RELEASE=""

for arg in "$@"; do
  case "${arg}" in
    --release=*)
      TARGET_RELEASE="${arg#--release=}"
      ;;
    -h|--help)
      cat << 'EOF'
Usage: check-bootstrap-helm-drift.sh [OPTIONS]

Checks for drift between bootstrap script Helm flags and running Helm releases.

Options:
  --release=<name>   Check only the specified release (e.g. apisix-ingress-controller)
  -h, --help         Show this help message

Environment variables:
  HELM_CMD           Helm command override (default: helm)
                     Guest example: HELM_CMD="sudo helm --kubeconfig /etc/kubernetes/admin.conf"
                     Note: arguments with spaces are not supported.
  KUBECTL_CMD        Kubectl command override (default: kubectl)
                     Guest example: KUBECTL_CMD="sudo kubectl --kubeconfig /etc/kubernetes/admin.conf"
                     Note: arguments with spaces are not supported.

Exit codes:
  0: in sync or unchecked
  1: drift detected
  2: cannot run
EOF
      exit 0
      ;;
    *)
      echo "ERROR: unrecognized argument: ${arg}" >&2
      exit 2
      ;;
  esac
done

# Set default KUBECONFIG if present in conventional paths and not already set
if [ -z "${KUBECONFIG:-}" ]; then
  if [ -f /home/vagrant/.kube/config-local ]; then
    export KUBECONFIG=/home/vagrant/.kube/config-local
  elif [ -f /etc/kubernetes/admin.conf ]; then
    export KUBECONFIG=/etc/kubernetes/admin.conf
  fi
fi

# Ensure python3 is available
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required but not installed." >&2
  exit 2
fi

if [ ! -f "${PYTHON_HELPER}" ]; then
  echo "ERROR: python helper not found: ${PYTHON_HELPER}" >&2
  exit 2
fi

# Command overrides for host vs guest execution
# Arguments with spaces are not supported; split into argv arrays with read -r -a (no eval).
HELM_CMD="${HELM_CMD:-helm}"
KUBECTL_CMD="${KUBECTL_CMD:-kubectl}"

# Split command strings into array tokens to preserve arguments cleanly
read -r -a HELM_BIN <<< "${HELM_CMD}"
read -r -a KUBECTL_BIN <<< "${KUBECTL_CMD}"

# Preflight: check cluster connectivity
echo "Checking cluster reachability via '${KUBECTL_CMD}'..."
if ! "${KUBECTL_BIN[@]}" get nodes >/dev/null 2>&1; then
  echo "ERROR: cannot connect to Kubernetes cluster using '${KUBECTL_CMD}'" >&2
  exit 2
fi

# Retrieve list of bootstrap releases
release_list="$(python3 "${PYTHON_HELPER}" --list-releases)"

TOTAL_CHECKED=0
IN_SYNC_COUNT=0
DRIFT_COUNT=0
UNCHECKED_COUNT=0

while IFS=$'\t' read -r rel ns script_rel; do
  [ -z "${rel}" ] && continue

  if [ -n "${TARGET_RELEASE}" ] && [ "${TARGET_RELEASE}" != "${rel}" ]; then
    continue
  fi

  TOTAL_CHECKED=$((TOTAL_CHECKED + 1))
  script_full_path="${ROOT_DIR}/${script_rel}"

  if [ ! -f "${script_full_path}" ]; then
    echo "ERROR: script file not found: ${script_full_path}" >&2
    exit 2
  fi

  echo "Checking release '${rel}' in namespace '${ns}'..."
  live_json=""
  if ! live_json="$("${HELM_BIN[@]}" get values "${rel}" -n "${ns}" -o json 2>/dev/null)"; then
    echo "ERROR: failed to get helm values for release '${rel}' in namespace '${ns}'" >&2
    exit 2
  fi

  # Run Python comparator via stdin pipe (never temporary files, zero secret leak)
  cmp_output=""
  cmp_rc=0
  cmp_output="$(printf '%s\n' "${live_json}" | python3 "${PYTHON_HELPER}" --compare-stdin \
    --release "${rel}" \
    --script "${script_full_path}")" || cmp_rc=$?

  if [ "${cmp_rc}" -eq 1 ]; then
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
    echo "${cmp_output}"
  elif [ "${cmp_rc}" -eq 0 ]; then
    if echo "${cmp_output}" | grep -q "^UNCHECKED:"; then
      UNCHECKED_COUNT=$((UNCHECKED_COUNT + 1))
    else
      IN_SYNC_COUNT=$((IN_SYNC_COUNT + 1))
    fi
    echo "  ${cmp_output}"
  else
    echo "ERROR: comparison failed for release '${rel}': ${cmp_output}" >&2
    exit 2
  fi

done <<< "${release_list}"

if [ "${TOTAL_CHECKED}" -eq 0 ]; then
  if [ -n "${TARGET_RELEASE}" ]; then
    echo "ERROR: release '${TARGET_RELEASE}' is not recognized as a bootstrap release." >&2
  else
    echo "ERROR: no bootstrap releases found to check." >&2
  fi
  exit 2
fi

echo "============================================================================="
if [ "${DRIFT_COUNT}" -gt 0 ]; then
  echo "FAIL: detected drift across ${DRIFT_COUNT} bootstrap Helm release(s) (${IN_SYNC_COUNT} in sync, ${UNCHECKED_COUNT} unchecked)."
  exit 1
fi

if [ "${UNCHECKED_COUNT}" -gt 0 ]; then
  echo "PASS: ${IN_SYNC_COUNT} bootstrap Helm release(s) in sync, ${UNCHECKED_COUNT} unchecked (values file not parsed)."
else
  echo "PASS: all ${IN_SYNC_COUNT} bootstrap Helm release(s) are in sync with provisioning scripts."
fi
exit 0
