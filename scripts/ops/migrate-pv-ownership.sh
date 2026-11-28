#!/usr/bin/env bash
# Migrate on-disk ownership of nfs-csi PersistentVolumes when moving a stateful
# workload to a new non-root UID:GID (e.g. CIS 5.7.3 KSV020/KSV021 -> 10100:10100).
#
# Dry-run by default (zero cluster or NFS mutations). Pass --apply to execute.
#
# Why this script exists (precedent: PR #845 SeaweedFS 1000 -> 10100):
#   - scripts/cluster/nfs-root-squash-align.sh only re-owns root-owned (uid/gid 0)
#     entries, leaving existing non-root data (e.g. 1000:2000) untouched.
#   - fsGroup is inert on nfs.csi.k8s.io, so kubelet will not re-own NFS files.
#   - The PV root directory itself is owned by nobody:nogroup (65534:65534) under
#     root_squash and MUST NOT be chowned; only contents (-mindepth 1) are changed.
#   - Operator-managed StatefulSets (e.g. prometheus-operator) reconcile replicas
#     back to 1 if scaled directly; pass --operator-cr KIND/NAME to pause the CR
#     via spec.paused (or scale via --operator-mode replicas) before stopping pods.
#   - When ArgoCD auto-sync/selfHeal is enabled on the owning Application, pause
#     auto-sync before running --apply (or pass --no-scale-back, sync the new UID
#     spec, then unpause/scale back) so pods do not restart under the old UID.
set -euo pipefail

KUBECONFIG_PATH="${KUBECONFIG:-}"
NODE_SPEC=""
USE_LOCAL=false
IDENTITY=""
KNOWN_HOSTS="$HOME/.ssh/known_hosts"
SSH_OPTS=()
NFS_SHARE_PATH="/srv/nfs/k8s"
NAMESPACE=""
WORKLOAD=""
PVCS=()
TARGET=""
OPERATOR_CR=""
OPERATOR_MODE="paused"
ALLOW_OWNED=false
NO_SCALE_BACK=false
TIMEOUT=180
APPLY=false

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: migrate-pv-ownership.sh \
  --namespace NS --workload KIND/NAME --pvc PVC [--pvc PVC ...] --target UID:GID \
  (--node [NAME=]USER@HOST | --local) \
  [--kubeconfig FILE] [--identity KEY] [--known-hosts FILE] [--ssh-opt OPTION]... \
  [--nfs-share PATH] [--operator-cr KIND/NAME] [--operator-mode paused|replicas] \
  [--allow-owned] [--no-scale-back] [--timeout SECONDS] [--apply]

Dry-run by default. Pass --apply to stop the workload, chown PV contents on the
NFS server (never the 65534 PV root directory), verify ownership, and scale back.

Operator-managed StatefulSets (e.g. Prometheus, Alertmanager):
  Pass --operator-cr prometheus/prometheus-stack-kube-prom-prometheus (with default
  --operator-mode paused, or --operator-mode replicas) so the operator does not
  recreate pods before chown completes.
USAGE
}

invocation=("$@")
while (($#)); do
  case "$1" in
    --kubeconfig) KUBECONFIG_PATH="${2:?--kubeconfig requires a path}"; shift 2 ;;
    --node) NODE_SPEC="${2:?--node requires [NAME=]USER@HOST}"; shift 2 ;;
    --local) USE_LOCAL=true; shift ;;
    --identity) IDENTITY="${2:?--identity requires a key path}"; shift 2 ;;
    --known-hosts) KNOWN_HOSTS="${2:?--known-hosts requires a path}"; shift 2 ;;
    --ssh-opt) SSH_OPTS+=(-o "${2:?--ssh-opt requires an OpenSSH option}"); shift 2 ;;
    --nfs-share) NFS_SHARE_PATH="${2:?--nfs-share requires a path}"; shift 2 ;;
    --namespace|-n) NAMESPACE="${2:?--namespace requires a value}"; shift 2 ;;
    --workload) WORKLOAD="${2:?--workload requires KIND/NAME}"; shift 2 ;;
    --pvc) PVCS+=("${2:?--pvc requires a PVC name}"); shift 2 ;;
    --target) TARGET="${2:?--target requires UID:GID}"; shift 2 ;;
    --operator-cr) OPERATOR_CR="${2:?--operator-cr requires KIND/NAME}"; shift 2 ;;
    --operator-mode) OPERATOR_MODE="${2:?--operator-mode requires paused or replicas}"; shift 2 ;;
    --allow-owned) ALLOW_OWNED=true; shift ;;
    --no-scale-back) NO_SCALE_BACK=true; shift ;;
    --timeout) TIMEOUT="${2:?--timeout requires seconds}"; shift 2 ;;
    --apply) APPLY=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

# Validate arguments (fail closed)
if [[ -z "${NAMESPACE}" || -z "${WORKLOAD}" || "${#PVCS[@]}" -eq 0 || -z "${TARGET}" ]]; then
  usage >&2
  exit 2
fi
if [[ "${USE_LOCAL}" == true && -n "${NODE_SPEC}" ]] || [[ "${USE_LOCAL}" == false && -z "${NODE_SPEC}" ]]; then
  usage >&2
  die "specify exactly one of --node [NAME=]USER@HOST or --local"
fi
if [[ -n "${KUBECONFIG_PATH}" && ! -r "${KUBECONFIG_PATH}" ]]; then
  die "kubeconfig is not readable: ${KUBECONFIG_PATH}"
fi
[[ "${NAMESPACE}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || die "invalid namespace: ${NAMESPACE}"
[[ "${WORKLOAD}" =~ ^[a-zA-Z]+/[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "invalid --workload (expected KIND/NAME): ${WORKLOAD}"
WORKLOAD_KIND="$(printf '%s' "${WORKLOAD%%/*}" | tr '[:upper:]' '[:lower:]')"
WORKLOAD_NAME="${WORKLOAD#*/}"
case "${WORKLOAD_KIND}" in
  statefulset|sts) WORKLOAD_KIND="statefulset" ;;
  deployment|deploy) WORKLOAD_KIND="deployment" ;;
  *) die "unsupported workload kind '${WORKLOAD_KIND}' (expected statefulset or deployment)" ;;
esac
WORKLOAD_CANON="${WORKLOAD_KIND}/${WORKLOAD_NAME}"

for pvc in "${PVCS[@]}"; do
  [[ "${pvc}" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "invalid PVC name: ${pvc}"
done

# D-E2-PV-MIGRATE-NUMERIC: refuse UID/GID 0 or non-numeric strings so chown never re-introduces root ownership.
[[ "${TARGET}" =~ ^[1-9][0-9]*:[1-9][0-9]*$ ]] || die "invalid --target (expected numeric non-zero UID:GID, e.g. 10100:10100): ${TARGET}"
TARGET_UID="${TARGET%%:*}"
TARGET_GID="${TARGET#*:}"

[[ "${NFS_SHARE_PATH}" =~ ^/[a-zA-Z0-9._/-]+$ && "${NFS_SHARE_PATH}" != *..* ]] || die "invalid --nfs-share path: ${NFS_SHARE_PATH}"
NFS_SHARE_PATH="${NFS_SHARE_PATH%/}"
[[ -n "${NFS_SHARE_PATH}" ]] || die "--nfs-share cannot be /"

[[ "${TIMEOUT}" =~ ^[1-9][0-9]*$ ]] || die "invalid --timeout: ${TIMEOUT}"
case "${OPERATOR_MODE}" in
  paused|replicas) ;;
  *) die "invalid --operator-mode '${OPERATOR_MODE}' (expected paused or replicas)" ;;
esac
if [[ -n "${OPERATOR_CR}" ]]; then
  [[ "${OPERATOR_CR}" =~ ^[a-zA-Z0-9.-]+/[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "invalid --operator-cr (expected KIND/NAME): ${OPERATOR_CR}"
fi

SSH_TARGET=""
if [[ "${USE_LOCAL}" == false ]]; then
  if [[ "${NODE_SPEC}" =~ ^[a-z0-9.-]+=([a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+)$ ]]; then
    SSH_TARGET="${NODE_SPEC#*=}"
  elif [[ "${NODE_SPEC}" =~ ^[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$ ]]; then
    SSH_TARGET="${NODE_SPEC}"
  else
    die "invalid --node (expected [NAME=]USER@HOST): ${NODE_SPEC}"
  fi
fi

command -v kubectl >/dev/null 2>&1 || die "kubectl is required"
command -v jq >/dev/null 2>&1 || die "jq is required"

kube() {
  if [[ -n "${KUBECONFIG_PATH}" ]]; then
    kubectl --kubeconfig "${KUBECONFIG_PATH}" --request-timeout=15s "$@"
  else
    kubectl --request-timeout=15s "$@"
  fi
}

SSH_CMD=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=${KNOWN_HOSTS}")
[[ -z "${IDENTITY}" ]] || SSH_CMD+=(-i "${IDENTITY}")
SSH_CMD+=("${SSH_OPTS[@]+"${SSH_OPTS[@]}"}")

# All NFS filesystem operations run inside one root shell (sudo -n) because the
# share root is 0750 nobody:nogroup and an unprivileged user cannot traverse it
# (lesson 2026-11-27, PR #845).
emit_nfs_helper() {
  cat <<'NFS_HELPER'
set -euo pipefail
ACTION="$1"
SHARE="$2"
SUBDIR="$3"

case "${SUBDIR}" in
  ""|/*|*..*|*[$' \t\r\n']*) echo "ERROR: unsafe subDir '${SUBDIR}'" >&2; exit 1 ;;
esac
SHARE_REAL="$(cd "${SHARE}" && pwd -P)"
DIR="${SHARE_REAL}/${SUBDIR}"
if [[ ! -d "${DIR}" ]]; then
  echo "ERROR: PV directory not found on NFS server: ${DIR}" >&2
  exit 1
fi
DIR_REAL="$(cd "${DIR}" && pwd -P)"
case "${DIR_REAL}" in
  "${SHARE_REAL}/"*) ;;
  *) echo "ERROR: PV directory '${DIR_REAL}' escapes share root '${SHARE_REAL}'" >&2; exit 1 ;;
esac

root_owner="$(stat -c '%u:%g' "${DIR_REAL}" 2>/dev/null || stat -f '%u:%g' "${DIR_REAL}")"
root_mode="$(stat -c '%a' "${DIR_REAL}" 2>/dev/null || stat -f '%Lp' "${DIR_REAL}")"

case "${ACTION}" in
  inspect)
    entry_count="$(find "${DIR_REAL}" -xdev -mindepth 1 -print0 | tr -cd '\0' | wc -c | tr -d ' ')"
    owners="$(find "${DIR_REAL}" -xdev -mindepth 1 -exec sh -c '
      for p in "$@"; do
        stat -c "%u:%g" "$p" 2>/dev/null || stat -f "%u:%g" "$p"
      done
    ' _ {} + | sort | uniq -c | sort -rn | awk '{print $2 "(" $1 ")"}' | tr '\n' ' ' | sed 's/ $//')"
    printf 'DIR=%s\nROOT_OWNER=%s\nROOT_MODE=%s\nENTRY_COUNT=%s\nOWNERS=%s\n' \
      "${DIR_REAL}" "${root_owner}" "${root_mode}" "${entry_count}" "${owners:-none}"
    ;;
  chown)
    T_UID="$4"
    T_GID="$5"
    T_PAIR="${T_UID}:${T_GID}"
    # D-E2-PV-MINDEPTH1: never chown the PV root dir (nobody:nogroup 65534:65534 under root_squash);
    # -h avoids following symlinks; -xdev keeps traversal on the NFS export filesystem.
    find "${DIR_REAL}" -xdev -mindepth 1 -exec chown -h "${T_PAIR}" -- {} +
    # Verify zero entries remain with another owner, and PV root dir ownership did not change.
    mismatch="$(find "${DIR_REAL}" -xdev -mindepth 1 \( ! -uid "${T_UID}" -o ! -gid "${T_GID}" \) -print)"
    if [[ -n "${mismatch}" ]]; then
      echo "ERROR: entries with non-${T_PAIR} ownership remain under ${DIR_REAL}:" >&2
      printf '%s\n' "${mismatch}" | head -n 20 >&2
      exit 1
    fi
    after_owner="$(stat -c '%u:%g' "${DIR_REAL}" 2>/dev/null || stat -f '%u:%g' "${DIR_REAL}")"
    if [[ "${after_owner}" != "${root_owner}" ]]; then
      echo "ERROR: PV root directory owner changed unexpectedly (${root_owner} -> ${after_owner})" >&2
      exit 1
    fi
    entry_count="$(find "${DIR_REAL}" -xdev -mindepth 1 -print0 | tr -cd '\0' | wc -c | tr -d ' ')"
    printf 'VERIFIED_DIR=%s\nVERIFIED_ENTRIES=%s\nROOT_OWNER_PRESERVED=%s\n' \
      "${DIR_REAL}" "${entry_count}" "${after_owner}"
    ;;
  *)
    echo "ERROR: unknown helper action: ${ACTION}" >&2
    exit 2
    ;;
esac
NFS_HELPER
}

run_nfs_helper() {
  local action="$1"; shift
  if [[ "${USE_LOCAL}" == true ]]; then
    if [[ "$(id -u)" -eq 0 ]]; then
      emit_nfs_helper | bash -s -- "${action}" "$@"
    else
      emit_nfs_helper | sudo -n bash -s -- "${action}" "$@"
    fi
  else
    emit_nfs_helper | "${SSH_CMD[@]}" "${SSH_TARGET}" sudo -n bash -s -- "${action}" "$@"
  fi
}

if [[ "${APPLY}" == true ]]; then MODE="APPLY"; else MODE="DRY-RUN"; fi
echo "=== NFS PV ownership migration (${MODE}) ==="
echo "namespace=${NAMESPACE} workload=${WORKLOAD_CANON} target=${TARGET} nfs_share=${NFS_SHARE_PATH}"
if [[ -n "${OPERATOR_CR}" ]]; then
  echo "operator_cr=${OPERATOR_CR} mode=${OPERATOR_MODE}"
fi

# 1. Inspect workload, replicas, pod selector, and ownerReferences
WL_JSON="$(kube -n "${NAMESPACE}" get "${WORKLOAD_CANON}" -o json)"
ORIG_REPLICAS="$(printf '%s' "${WL_JSON}" | jq -r '.spec.replicas // 1')"
[[ "${ORIG_REPLICAS}" =~ ^[0-9]+$ ]] || die "unexpected .spec.replicas '${ORIG_REPLICAS}' on ${WORKLOAD_CANON}"

OWNER_REF="$(printf '%s' "${WL_JSON}" | jq -r '(.metadata.ownerReferences // [])[0] | select(. != null) | "\(.kind)/\(.name)"')"
if [[ -n "${OWNER_REF}" && -z "${OPERATOR_CR}" && "${ALLOW_OWNED}" == false ]]; then
  # D-E2-PV-MIGRATE-OP: operator-managed StatefulSets (e.g. prometheus-operator) recreate pods if
  # scaled directly; refuse unless --operator-cr is given (or --allow-owned if paused out of band).
  die "${NAMESPACE}/${WORKLOAD_CANON} is owned by ${OWNER_REF}; pass --operator-cr <kind>/<name> so the operator does not recreate pods during chown (or --allow-owned if already paused)"
fi

SELECTOR="$(printf '%s' "${WL_JSON}" | jq -r '(.spec.selector.matchLabels // {}) | to_entries | map("\(.key)=\(.value)") | join(",")')"
[[ -n "${SELECTOR}" ]] || die "could not determine matchLabels selector for ${NAMESPACE}/${WORKLOAD_CANON}"

CR_ORIG_REPLICAS=""
if [[ -n "${OPERATOR_CR}" ]]; then
  CR_JSON="$(kube -n "${NAMESPACE}" get "${OPERATOR_CR}" -o json)"
  CR_ORIG_REPLICAS="$(printf '%s' "${CR_JSON}" | jq -r '.spec.replicas // 1')"
fi

echo "workload replicas=${ORIG_REPLICAS} selector=${SELECTOR}${OWNER_REF:+ owner=${OWNER_REF}}"

# 2. Resolve every PVC -> PV -> nfs.csi.k8s.io volumeAttributes.subDir (fail closed on non-NFS)
RESOLVED_PVS=()
RESOLVED_SUBDIRS=()
OBSERVED_PRIOR_OWNER=""

for pvc in "${PVCS[@]}"; do
  PVC_JSON="$(kube -n "${NAMESPACE}" get pvc "${pvc}" -o json)"
  PHASE="$(printf '%s' "${PVC_JSON}" | jq -r '.status.phase // ""')"
  PV_NAME="$(printf '%s' "${PVC_JSON}" | jq -r '.spec.volumeName // ""')"
  [[ "${PHASE}" == "Bound" && -n "${PV_NAME}" ]] || die "PVC ${NAMESPACE}/${pvc} is not Bound (phase='${PHASE}', volumeName='${PV_NAME}')"

  PV_JSON="$(kube get pv "${PV_NAME}" -o json)"
  CSI_DRIVER="$(printf '%s' "${PV_JSON}" | jq -r '.spec.csi.driver // ""')"
  SC_NAME="$(printf '%s' "${PV_JSON}" | jq -r '.spec.storageClassName // ""')"
  if [[ "${CSI_DRIVER}" != "nfs.csi.k8s.io" ]]; then
    die "REFUSE non-NFS PV: PVC ${NAMESPACE}/${pvc} -> PV ${PV_NAME} has driver='${CSI_DRIVER:-none}' storageClass='${SC_NAME:-none}' (only nfs.csi.k8s.io is supported)"
  fi
  SUBDIR="$(printf '%s' "${PV_JSON}" | jq -r '.spec.csi.volumeAttributes.subDir // .spec.csi.volumeAttributes.subdir // ""')"
  if [[ -z "${SUBDIR}" ]]; then
    die "PV ${PV_NAME} (PVC ${NAMESPACE}/${pvc}) is missing spec.csi.volumeAttributes.subDir"
  fi
  if [[ "${SUBDIR}" == /* || "${SUBDIR}" == *..* || ! "${SUBDIR}" =~ ^[a-zA-Z0-9._/-]+$ ]]; then
    die "PV ${PV_NAME} has unsafe volumeAttributes.subDir='${SUBDIR}'"
  fi

  INSPECT_OUT="$(run_nfs_helper inspect "${NFS_SHARE_PATH}" "${SUBDIR}")"
  INSPECT_DIR="$(printf '%s\n' "${INSPECT_OUT}" | sed -n 's/^DIR=//p')"
  INSPECT_ROOT="$(printf '%s\n' "${INSPECT_OUT}" | sed -n 's/^ROOT_OWNER=//p')"
  INSPECT_MODE="$(printf '%s\n' "${INSPECT_OUT}" | sed -n 's/^ROOT_MODE=//p')"
  INSPECT_CNT="$(printf '%s\n' "${INSPECT_OUT}" | sed -n 's/^ENTRY_COUNT=//p')"
  INSPECT_OWNERS="$(printf '%s\n' "${INSPECT_OUT}" | sed -n 's/^OWNERS=//p')"

  echo "PVC ${NAMESPACE}/${pvc} -> PV ${PV_NAME} (sc=${SC_NAME}, driver=${CSI_DRIVER})"
  echo "  nfs_dir=${INSPECT_DIR} root=${INSPECT_ROOT} mode=${INSPECT_MODE} entries=${INSPECT_CNT} owners=[${INSPECT_OWNERS}]"
  if [[ -z "${OBSERVED_PRIOR_OWNER}" && "${INSPECT_OWNERS}" != "none" ]]; then
    first_owner="${INSPECT_OWNERS%%(*}"
    if [[ "${first_owner}" =~ ^[1-9][0-9]*:[1-9][0-9]*$ ]]; then
      OBSERVED_PRIOR_OWNER="${first_owner}"
    fi
  fi

  RESOLVED_PVS+=("${PV_NAME}")
  RESOLVED_SUBDIRS+=("${SUBDIR}")
done

ROLLBACK_TARGET="${OBSERVED_PRIOR_OWNER:-<OLD_UID>:<OLD_GID>}"
print_rollback() {
  echo "--- Rollback steps ---"
  echo "1. If the new pod fails to start or you revert the GitOps/Helm UID change, re-own PV contents back to ${ROLLBACK_TARGET}:"
  printf '   bash %q' "$0"
  local i=0
  while (( i < ${#invocation[@]} )); do
    local arg="${invocation[i]}"
    if [[ "${arg}" == "--target" ]]; then
      printf ' --target %q' "${ROLLBACK_TARGET}"
      i=$((i + 2))
      continue
    elif [[ "${arg}" == "--apply" ]]; then
      i=$((i + 1))
      continue
    fi
    printf ' %q' "${arg}"
    i=$((i + 1))
  done
  printf ' --apply\n'
  if [[ -n "${OPERATOR_CR}" ]]; then
    if [[ "${OPERATOR_MODE}" == "paused" ]]; then
      echo "2. If interrupted while paused, unpause the operator CR:"
      echo "   kubectl -n ${NAMESPACE} patch ${OPERATOR_CR} --type=merge -p '{\"spec\":{\"paused\":false}}'"
    else
      echo "2. If interrupted while scaled to 0, restore operator CR replicas:"
      echo "   kubectl -n ${NAMESPACE} patch ${OPERATOR_CR} --type=merge -p '{\"spec\":{\"replicas\":${CR_ORIG_REPLICAS}}}'"
    fi
  fi
  echo "3. Confirm pods are Ready: kubectl -n ${NAMESPACE} rollout status ${WORKLOAD_CANON} --timeout=${TIMEOUT}s"
}

if [[ "${APPLY}" == false ]]; then
  echo "--- Planned steps (DRY-RUN: no changes made) ---"
  if [[ -n "${OPERATOR_CR}" ]]; then
    if [[ "${OPERATOR_MODE}" == "paused" ]]; then
      echo "1. Pause operator CR: kubectl -n ${NAMESPACE} patch ${OPERATOR_CR} --type=merge -p '{\"spec\":{\"paused\":true}}'"
      echo "2. Scale workload to 0: kubectl -n ${NAMESPACE} scale ${WORKLOAD_CANON} --replicas=0"
    else
      echo "1. Scale operator CR to 0: kubectl -n ${NAMESPACE} patch ${OPERATOR_CR} --type=merge -p '{\"spec\":{\"replicas\":0}}'"
    fi
  else
    echo "1. Scale workload to 0: kubectl -n ${NAMESPACE} scale ${WORKLOAD_CANON} --replicas=0"
  fi
  echo "2. Wait for 0 pods matching selector '${SELECTOR}' in namespace '${NAMESPACE}' (timeout ${TIMEOUT}s)"
  for i in "${!PVCS[@]}"; do
    echo "3.$((i + 1)). On NFS server (as root): find ${NFS_SHARE_PATH}/${RESOLVED_SUBDIRS[i]} -xdev -mindepth 1 -exec chown -h ${TARGET} -- {} + (keeping PV root dir owner untouched) and verify 0 mismatched entries"
  done
  if [[ "${NO_SCALE_BACK}" == true ]]; then
    echo "4. Leave workload stopped (--no-scale-back) so caller can sync the new UID spec before starting pods."
  else
    if [[ -n "${OPERATOR_CR}" ]]; then
      if [[ "${OPERATOR_MODE}" == "paused" ]]; then
        echo "4. Scale workload back to ${ORIG_REPLICAS} and unpause ${OPERATOR_CR} (spec.paused=false)"
      else
        echo "4. Restore ${OPERATOR_CR} spec.replicas=${CR_ORIG_REPLICAS}"
      fi
    else
      echo "4. Scale workload back to ${ORIG_REPLICAS}: kubectl -n ${NAMESPACE} scale ${WORKLOAD_CANON} --replicas=${ORIG_REPLICAS}"
    fi
    echo "5. Wait for rollout: kubectl -n ${NAMESPACE} rollout status ${WORKLOAD_CANON} --timeout=${TIMEOUT}s"
  fi
  print_rollback
  echo "Dry run complete. Re-run with --apply to execute."
  exit 0
fi

# 3. Apply: stop workload (pausing operator CR when specified)
if [[ -n "${OPERATOR_CR}" ]]; then
  if [[ "${OPERATOR_MODE}" == "paused" ]]; then
    echo "Pausing operator CR ${NAMESPACE}/${OPERATOR_CR}..."
    kube -n "${NAMESPACE}" patch "${OPERATOR_CR}" --type=merge -p '{"spec":{"paused":true}}'
    echo "Scaling ${NAMESPACE}/${WORKLOAD_CANON} to 0..."
    kube -n "${NAMESPACE}" scale "${WORKLOAD_CANON}" --replicas=0
  else
    echo "Scaling operator CR ${NAMESPACE}/${OPERATOR_CR} to 0..."
    kube -n "${NAMESPACE}" patch "${OPERATOR_CR}" --type=merge -p '{"spec":{"replicas":0}}'
  fi
else
  echo "Scaling ${NAMESPACE}/${WORKLOAD_CANON} to 0..."
  kube -n "${NAMESPACE}" scale "${WORKLOAD_CANON}" --replicas=0
fi

echo "Waiting for pods (${SELECTOR}) in ${NAMESPACE} to terminate..."
deadline=$((SECONDS + TIMEOUT))
while true; do
  pod_count="$(kube -n "${NAMESPACE}" get pods -l "${SELECTOR}" -o json | jq '.items | length')"
  if [[ "${pod_count}" -eq 0 ]]; then
    echo "All pods stopped."
    break
  fi
  if (( SECONDS >= deadline )); then
    print_rollback >&2
    die "timed out waiting for ${NAMESPACE}/${WORKLOAD_CANON} pods to terminate (${pod_count} still present)"
  fi
  sleep 2
done

# 4. Chown PV contents on NFS server and verify
for i in "${!PVCS[@]}"; do
  echo "Migrating ownership of ${NAMESPACE}/${PVCS[i]} (${RESOLVED_PVS[i]} -> ${RESOLVED_SUBDIRS[i]}) to ${TARGET}..."
  if ! VERIFY_OUT="$(run_nfs_helper chown "${NFS_SHARE_PATH}" "${RESOLVED_SUBDIRS[i]}" "${TARGET_UID}" "${TARGET_GID}")"; then
    print_rollback >&2
    die "chown/verification failed for PVC ${NAMESPACE}/${PVCS[i]}"
  fi
  printf '%s\n' "${VERIFY_OUT}" | sed 's/^/  /'
done

# 5. Scale back (unless --no-scale-back)
if [[ "${NO_SCALE_BACK}" == true ]]; then
  echo "Leaving ${NAMESPACE}/${WORKLOAD_CANON} stopped (--no-scale-back)."
  echo "After syncing the updated UID (${TARGET}) manifest, start the workload:"
  if [[ -n "${OPERATOR_CR}" ]]; then
    if [[ "${OPERATOR_MODE}" == "paused" ]]; then
      echo "  kubectl -n ${NAMESPACE} scale ${WORKLOAD_CANON} --replicas=${ORIG_REPLICAS}"
      echo "  kubectl -n ${NAMESPACE} patch ${OPERATOR_CR} --type=merge -p '{\"spec\":{\"paused\":false}}'"
    else
      echo "  kubectl -n ${NAMESPACE} patch ${OPERATOR_CR} --type=merge -p '{\"spec\":{\"replicas\":${CR_ORIG_REPLICAS}}}'"
    fi
  else
    echo "  kubectl -n ${NAMESPACE} scale ${WORKLOAD_CANON} --replicas=${ORIG_REPLICAS}"
  fi
else
  if [[ -n "${OPERATOR_CR}" ]]; then
    if [[ "${OPERATOR_MODE}" == "paused" ]]; then
      echo "Scaling ${NAMESPACE}/${WORKLOAD_CANON} back to ${ORIG_REPLICAS} and unpausing ${OPERATOR_CR}..."
      kube -n "${NAMESPACE}" scale "${WORKLOAD_CANON}" --replicas="${ORIG_REPLICAS}"
      kube -n "${NAMESPACE}" patch "${OPERATOR_CR}" --type=merge -p '{"spec":{"paused":false}}'
    else
      echo "Restoring ${NAMESPACE}/${OPERATOR_CR} replicas to ${CR_ORIG_REPLICAS}..."
      kube -n "${NAMESPACE}" patch "${OPERATOR_CR}" --type=merge -p "{\"spec\":{\"replicas\":${CR_ORIG_REPLICAS}}}"
    fi
  else
    echo "Scaling ${NAMESPACE}/${WORKLOAD_CANON} back to ${ORIG_REPLICAS}..."
    kube -n "${NAMESPACE}" scale "${WORKLOAD_CANON}" --replicas="${ORIG_REPLICAS}"
  fi
  echo "Waiting for rollout of ${NAMESPACE}/${WORKLOAD_CANON}..."
  if ! kube -n "${NAMESPACE}" rollout status "${WORKLOAD_CANON}" --timeout="${TIMEOUT}s"; then
    print_rollback >&2
    die "rollout failed for ${NAMESPACE}/${WORKLOAD_CANON}"
  fi
fi

print_rollback
echo "=== Migration complete (${NAMESPACE}/${WORKLOAD_CANON} -> ${TARGET}) ==="
