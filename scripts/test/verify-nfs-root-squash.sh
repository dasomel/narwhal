#!/bin/bash
set -euo pipefail

#=========================================
# NFS root_squash negative test (narwhal#186)
#=========================================
# Proves the acceptance-criteria claim that "an unprivileged/foreign client cannot
# create/modify data outside its intended area" now that scripts/cluster/01-nfs-server.sh
# exports the share root_squash instead of no_root_squash (0777 world-writable, both CIDRs
# no_root_squash, before the fix).
#
# This is a LIVE-CLUSTER-ONLY test: it mounts the real NFS export as root from an actual
# client and attempts privileged writes against it. No cluster has been available in any
# agent session that has touched narwhal#186 so far, so this script has NOT been run and
# its result is NOT recorded anywhere as passing. Run it by hand on a live cluster and
# record PASS/FAIL in docs/common/compliance-hardening.md under "NFS export least-privilege
# migration (narwhal#186)".
#
# Usage (on any host with the NFS client tools and root, that is on the exported CIDR,
# e.g. run from master-1 or another cluster node via `sudo`):
#   sudo ./scripts/test/verify-nfs-root-squash.sh [NFS_SERVER_IP]
#
# What it checks:
#   1. Mount the export directly (bypassing Kubernetes/csi-driver-nfs) as root.
#   2. As root on the client, create a file under the mount.
#   3. Assert the file lands OWNED BY THE ANONYMOUS UID/GID (nobody:nogroup, 65534:65534),
#      not uid/gid 0 — this is the actual behavioral proof of root_squash taking effect.
#      Under the old no_root_squash config this file would be owned root:root instead.
#   4. Create a directory owned root:root:0700 as a stand-in for another tenant's
#      root-owned private data, then attempt to write into it as the squashed identity —
#      MUST fail with Permission denied, proving a squashed client cannot escalate into
#      another tenant's root-only area.
#   5. Clean up everything it created, unmount, exit non-zero on any assertion failure.

NFS_SERVER="${1:-${NFS_SERVER_IP:-192.168.56.10}}"
NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
MOUNT_POINT="$(mktemp -d /tmp/nfs-root-squash-test.XXXXXX)"
TEST_SUBDIR="root-squash-test-$$"
FAIL=0

cleanup() {
  local rc=$?
  echo "--- cleanup ---"
  if [ -d "${MOUNT_POINT}/${TEST_SUBDIR}" ]; then
    sudo rm -rf "${MOUNT_POINT:?}/${TEST_SUBDIR}" 2>/dev/null || true
  fi
  if mountpoint -q "${MOUNT_POINT}" 2>/dev/null; then
    sudo umount "${MOUNT_POINT}" || true
  fi
  rmdir "${MOUNT_POINT}" 2>/dev/null || true
  exit "${rc}"
}
trap cleanup EXIT

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (needs to mount NFS and write as uid 0 to prove squash)." >&2
  exit 1
fi

echo "=== NFS root_squash negative test ==="
echo "Server: ${NFS_SERVER}:${NFS_SHARE_PATH}"
echo "Mount point: ${MOUNT_POINT}"

sudo mount -t nfs -o vers=3 "${NFS_SERVER}:${NFS_SHARE_PATH}" "${MOUNT_POINT}"

sudo mkdir -p "${MOUNT_POINT}/${TEST_SUBDIR}"

# --- Check 1: root-owned write on the client is squashed on the server ---
PROBE_FILE="${MOUNT_POINT}/${TEST_SUBDIR}/root-write-probe"
sudo touch "${PROBE_FILE}"
OWNER="$(stat -c '%U:%G' "${PROBE_FILE}" 2>/dev/null || stat -f '%Su:%Sg' "${PROBE_FILE}")"
if [ "${OWNER}" = "nobody:nogroup" ] || [ "${OWNER}" = "nobody:nobody" ]; then
  echo "PASS: root-created file squashed to ${OWNER} (expected nobody:nogroup)"
else
  echo "FAIL: root-created file owned ${OWNER}, expected nobody:nogroup — root_squash is NOT in effect"
  FAIL=1
fi

# --- Check 2: squashed identity cannot write into a root-only-owned directory ---
# The stand-in "other tenant's root-owned dir" MUST be created on the SERVER, as real
# root on the export's local filesystem -- NOT through this squashed NFS mount. Once
# mounted here, every write from this client is already squashed to the anonymous
# UID/GID, so a `chown root:root` issued through the mount would itself be denied
# (chown-to-arbitrary-owner requires real root/CAP_CHOWN, which the squashed identity
# does not have) and abort the script under `set -e` before the real assertion runs.
TENANT_SUBDIR="${TEST_SUBDIR}/other-tenant-root-only"
ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes "root@${NFS_SERVER}" \
  "mkdir -p '${NFS_SHARE_PATH}/${TENANT_SUBDIR}' && chown root:root '${NFS_SHARE_PATH}/${TENANT_SUBDIR}' && chmod 700 '${NFS_SHARE_PATH}/${TENANT_SUBDIR}'"
TENANT_DIR="${MOUNT_POINT}/${TENANT_SUBDIR}"

# Attempt the write as root FROM THE CLIENT — on the wire this is squashed to nobody,
# which must NOT have permission to write into a 0700 root:root directory.
if sudo touch "${TENANT_DIR}/should-not-be-writable" 2>/dev/null; then
  echo "FAIL: squashed client was able to write into another tenant's root:root 0700 dir"
  FAIL=1
else
  echo "PASS: squashed client denied write into another tenant's root:root 0700 dir"
fi

echo "=== Result: $([ "${FAIL}" -eq 0 ] && echo PASS || echo FAIL) ==="
exit "${FAIL}"
