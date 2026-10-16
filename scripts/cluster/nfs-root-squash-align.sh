#!/bin/bash
# Align the on-disk NFS layout with a root_squash export (narwhal#224, #186).
#
# Run on the NFS server (master-1) AS ROOT (sudo bash ...); it refuses to start otherwise (D-rs-root):
#   sudo bash scripts/cluster/nfs-root-squash-align.sh            # dry run (default): prints the plan, changes nothing
#   sudo bash scripts/cluster/nfs-root-squash-align.sh --apply    # performs the chown/chmod below
# Why root: once the share root is nobody:nogroup 0750 an unprivileged user cannot traverse it, so
# every PV directory looks absent. Exit codes: 0 ok, 1 error / not root, 3 PVs skipped or unresolved,
# 4 a PV directory could not be read (never reported as success).
#
# What it changes (and nothing else; /etc/exports is NEVER touched, the export switch is a
# separate step in docs/runbooks/nfs-xfs-migration.md):
#   1. Every root-owned entry (uid 0 or gid 0) inside each nfs.csi.k8s.io PV directory is
#      chowned to that PV's workload identity. root_squash maps a client's uid 0 to nobody, so
#      files only root could write (SeaweedFS filer/master, alertmanager/prometheus subPath
#      dirs) become unwritable. Only root-owned entries are touched; files already owned by
#      the app are left alone.
#   2. Namespace directories (<share>/<ns>) become nobody:nogroup, the identity the
#      squashed csi-driver-nfs provisioner has on a Vagrant install (D-ns: no chmod 777; the
#      provisioner owns the dir, so 0755 suffices; an owner without rwx is fixed with u+rwx).
#   3. The share root becomes nobody:nogroup 0750 (the Vagrant/01-nfs-server.sh layout).
#
# Target identity per PV: STATIC_TARGETS (reviewed) first, else the bound pod's
# container runAsUser / pod fsGroup. A PV with neither is reported UNRESOLVED and never touched.
# Running-pod guard: a dir is skipped when a Running consumer runs as an explicit non-root uid
# other than the target, since chowning would lock that process out. Consumers running as
# the target uid, or as root (no_root_squash today: root bypasses permissions, so a chown
# cannot break it), are safe. This is why SeaweedFS can be chowned while its root pods run;
# they then restart as uid 1000 from the GitOps change.
set -euo pipefail

NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
KUBECTL="${KUBECTL:-sudo kubectl}"
APPLY=false

# D-sw-static: SeaweedFS pods have no securityContext until the GitOps change that
# sets uid 1000 (verified image user `seaweed`) is synced, so their identity cannot be read
# from the live pod yet. Format: "<namespace>/<pvc>=<uid>:<gid>".
STATIC_TARGETS=(
  "storage/data-filer-seaweedfs-filer-0=1000:1000"
  "storage/data-storage-seaweedfs-master-0=1000:1000"
)

case "${1:-}" in
  --apply) APPLY=true ;;
  "") ;;
  -h | --help)
    sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "usage: $0 [--apply]" >&2
    exit 2
    ;;
esac

# D-rs-root: fail fast; an unprivileged run silently skips every PV once the share root is 0750.
if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root: sudo bash $0 ${1:-}" >&2
  exit 1
fi

if ${APPLY}; then MODE="APPLY"; else MODE="DRY-RUN"; fi
echo "=== NFS root_squash alignment (${MODE}) share=${NFS_SHARE_PATH} ==="

[ -d "${NFS_SHARE_PATH}" ] || { echo "ERROR: ${NFS_SHARE_PATH} is not a directory; run this on the NFS server." >&2; exit 1; }
command -v jq >/dev/null || { echo "ERROR: jq is required." >&2; exit 1; }

# act <description> <command...>: print, and run only with --apply.
act() {
  local desc="$1"; shift
  echo "  [${MODE}] ${desc}"
  if ${APPLY}; then "$@"; fi
}

static_target() {
  local key="$1" entry
  for entry in "${STATIC_TARGETS[@]}"; do
    if [ "${entry%%=*}" = "${key}" ]; then
      echo "${entry#*=}"
      return 0
    fi
  done
  return 1
}

echo "--- preconditions (report only) ---"
echo "export lines: $(grep -E "^[[:space:]]*${NFS_SHARE_PATH}[[:space:]]" /etc/exports 2>/dev/null | tr -s ' ' | tr '\n' ';' || true)"
fs_policy="$(${KUBECTL} get csidriver nfs.csi.k8s.io -o jsonpath='{.spec.fsGroupPolicy}' 2>/dev/null || echo unknown)"
echo "CSIDriver nfs.csi.k8s.io fsGroupPolicy='${fs_policy}'"
if [ "${fs_policy}" = "File" ]; then
  echo "  BLOCKER for the export switch: kubelet would chown PV volumes through root_squash. Fix: docs/runbooks/nfs-xfs-migration.md step 'CSIDriver'."
fi
if systemctl is-active --quiet narwhal-nfs-pv-permissions.timer 2>/dev/null; then
  echo "PV permission timer: active"
else
  echo "PV permission timer: MISSING  (BLOCKER for dynamic provisioning under root_squash; install per runbook)"
fi

echo "--- PV directories ---"
PV_ROWS="$(${KUBECTL} get pv -o json | jq -r '.items[]
  | select(.spec.csi.driver == "nfs.csi.k8s.io" and .spec.claimRef != null)
  | [.spec.claimRef.namespace, .spec.claimRef.name,
     (.spec.csi.volumeAttributes.subDir // .spec.csi.volumeAttributes.subdir // (.spec.claimRef.namespace + "/" + .spec.claimRef.name))]
  | @tsv')"
# One row per pod volume: ns, claim, pod, phase, runAsUser, gid (fsGroup, else runAsGroup).
POD_ROWS="$(${KUBECTL} get pods -A -o json | jq -r '.items[]
  | select(.status.phase == "Running" or .status.phase == "Pending")
  | . as $p | ($p.spec.volumes // [])[] | select(.persistentVolumeClaim != null)
  | . as $v
  | ([($p.spec.containers[] | select([.volumeMounts[]?.name] | index($v.name)))] | first // {}) as $c
  | [$p.metadata.namespace, $v.persistentVolumeClaim.claimName, $p.metadata.name, $p.status.phase,
     (($c.securityContext.runAsUser // $p.spec.securityContext.runAsUser) // "" | tostring),
     (($p.spec.securityContext.fsGroup // $c.securityContext.runAsGroup // $p.spec.securityContext.runAsGroup) // "" | tostring)]
  | @tsv')"

skipped=0
unreadable=0
while IFS=$'\t' read -r ns claim subdir; do
  [ -n "${ns}" ] || continue
  dir="${NFS_SHARE_PATH}/${subdir}"
  key="${ns}/${claim}"
  echo "${key} -> ${dir}"
  if stat_err="$(stat -c '%a' "${dir}" 2>&1 >/dev/null)"; [[ "${stat_err}" == *"Permission denied"* ]]; then
    echo "  UNREADABLE: cannot traverse ${dir} (${stat_err}); nothing was checked for this PV"
    unreadable=$((unreadable + 1))
    continue
  fi
  if [ ! -d "${dir}" ]; then
    echo "  SKIP: directory not found on this host"
    skipped=$((skipped + 1))
    continue
  fi

  target=""
  if target="$(static_target "${key}")"; then
    echo "  target ${target} (static map)"
  else
    # Consumers with an explicit uid must agree; the first one defines the target.
    first="$(printf '%s\n' "${POD_ROWS}" | awk -F'\t' -v n="${ns}" -v c="${claim}" '$1==n && $2==c && $5!="" {print $5 ":" ($6!="" ? $6 : $5); exit}')"
    if [ -n "${first}" ]; then
      target="${first}"
      echo "  target ${target} (from pod securityContext)"
    else
      echo "  UNRESOLVED: no static entry and no consumer runAsUser; not touching it"
      skipped=$((skipped + 1))
      continue
    fi
  fi
  t_uid="${target%%:*}"

  # Running-pod guard (see header).
  unsafe="$(printf '%s\n' "${POD_ROWS}" | awk -F'\t' -v n="${ns}" -v c="${claim}" -v t="${t_uid}" \
    '$1==n && $2==c && $4=="Running" && $5!="" && $5!="0" && $5!=t {print $3 " uid=" $5}')"
  if [ -n "${unsafe}" ]; then
    echo "  REFUSE: a Running consumer runs as a different non-root uid: ${unsafe}"
    skipped=$((skipped + 1))
    continue
  fi

  if ! count="$(sudo find "${dir}" -xdev \( -uid 0 -o -gid 0 \) -print0 | tr -cd '\0' | wc -c | tr -d ' ')"; then
    echo "  UNREADABLE: find failed under ${dir}; not counted as aligned"
    unreadable=$((unreadable + 1))
    continue
  fi
  if [ "${count}" -eq 0 ]; then
    echo "  ok: no root-owned entries"
    continue
  fi
  act "chown -h ${target} on ${count} root-owned entries in ${dir}" \
    sudo find "${dir}" -xdev \( -uid 0 -o -gid 0 \) -exec chown -h "${target}" -- {} +
done <<<"${PV_ROWS}"

echo "--- namespace directories ---"
while IFS= read -r nsdir; do
  [ -n "${nsdir}" ] || continue
  owner="$(sudo stat -c '%U:%G' "${nsdir}")"
  mode="$(sudo stat -c '%a' "${nsdir}")"
  if [ "${owner}" != "nobody:nogroup" ]; then
    act "chown nobody:nogroup ${nsdir} (was ${owner}; the squashed provisioner must own it)" \
      sudo chown nobody:nogroup "${nsdir}"
  fi
  if [ "$(( 8#${mode} & 8#700 ))" -ne $(( 8#700 )) ]; then
    act "chmod u+rwx ${nsdir} (was ${mode})" sudo chmod u+rwx "${nsdir}"
  fi
  if [ "${owner}" = "nobody:nogroup" ]; then echo "  ok: ${nsdir} ${owner} ${mode}"; fi
done < <(sudo find "${NFS_SHARE_PATH}" -mindepth 1 -maxdepth 1 -type d)

echo "--- share root ---"
root_owner="$(sudo stat -c '%U:%G' "${NFS_SHARE_PATH}")"
root_mode="$(sudo stat -c '%a' "${NFS_SHARE_PATH}")"
[ "${root_owner}" = "nobody:nogroup" ] || act "chown nobody:nogroup ${NFS_SHARE_PATH} (was ${root_owner})" sudo chown nobody:nogroup "${NFS_SHARE_PATH}"
[ "${root_mode}" = "750" ] || act "chmod 750 ${NFS_SHARE_PATH} (was ${root_mode})" sudo chmod 750 "${NFS_SHARE_PATH}"
[ "${root_owner}:${root_mode}" != "nobody:nogroup:750" ] || echo "  ok: ${NFS_SHARE_PATH} nobody:nogroup 750"

echo "=== done (${MODE}); skipped/unresolved PVs: ${skipped}; unreadable PVs: ${unreadable} ==="
if ! ${APPLY}; then echo "Dry run only. Re-run with --apply to perform the changes above."; fi
if [ "${unreadable}" -gt 0 ]; then exit 4; fi
if [ "${skipped}" -gt 0 ]; then exit 3; fi
