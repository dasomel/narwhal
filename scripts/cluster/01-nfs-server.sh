#!/bin/bash
set -euo pipefail

NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
HOST_NETWORK_CIDR="${HOST_NETWORK_CIDR:-192.168.56.0/24}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"

echo "=== NFS Server Installation ==="
echo "Share Path: ${NFS_SHARE_PATH}"

# Install NFS server and quota tools
sudo apt-get update
sudo apt-get install -y nfs-kernel-server quota quotatool

# Create NFS share directory
sudo mkdir -p "${NFS_SHARE_PATH}"
sudo chown nobody:nogroup "${NFS_SHARE_PATH}"
sudo chmod 777 "${NFS_SHARE_PATH}"

# Configure exports
cat <<EOF | sudo tee /etc/exports
# Kubernetes NFS share
${NFS_SHARE_PATH}  ${HOST_NETWORK_CIDR}(rw,sync,no_subtree_check,no_root_squash)
${NFS_SHARE_PATH}  ${POD_NETWORK_CIDR}(rw,sync,no_subtree_check,no_root_squash)
EOF

# Apply exports
sudo exportfs -ra

# Enable project quota on root filesystem (ext4)
# Find the device for NFS_SHARE_PATH
NFS_DEVICE=$(df "${NFS_SHARE_PATH}" | tail -1 | awk '{print $1}')
NFS_MOUNT=$(df "${NFS_SHARE_PATH}" | tail -1 | awk '{print $6}')

echo "NFS Device: ${NFS_DEVICE}"
echo "NFS Mount: ${NFS_MOUNT}"

# Check if already mounted with prjquota
if ! mount | grep "${NFS_MOUNT}" | grep -q prjquota; then
  echo "Enabling project quota on ${NFS_MOUNT}..."

  # For ext4, enable quota feature
  FS_TYPE=$(df -T "${NFS_SHARE_PATH}" | tail -1 | awk '{print $2}')
  if [ "${FS_TYPE}" = "ext4" ]; then
    # Try to remount with prjquota
    sudo mount -o remount,prjquota "${NFS_MOUNT}" 2>/dev/null || true
  fi
fi

# Ensure NFS restarts after reboot with network dependency
sudo mkdir -p /etc/systemd/system/nfs-kernel-server.service.d
cat <<EOF | sudo tee /etc/systemd/system/nfs-kernel-server.service.d/restart.conf
[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Restart=on-failure
RestartSec=10
EOF

# Enable and start NFS server
#=========================================
# Pin the NFSv3 helper ports
#=========================================
# mountd, statd and lockd otherwise bind whatever the portmapper hands them. On a cloud
# with a default-deny security group that is fatal in a specific way: an unlisted port is
# DROPPED, not refused, so a lock-grant callback from the server to a client does not fail
# — it hangs until TCP gives up. Pinning them lets the security group name them.
#
# Ports are chosen outside 30000-32767: that range is the NodePort range and is open to
# 0.0.0.0/0, which is the last place these belong.
# /etc/nfs.conf, not /etc/default/nfs-kernel-server. The defaults file is the sysv-era
# path; the systemd units that actually start these daemons read nfs.conf, so
# RPCMOUNTDOPTS there is silently ignored — mountd kept landing on random high ports
# (rpcinfo showed 49793, 53677, ...) while lockd, pinned by sysctl, sat correctly on 4045.
# The v3 mount then timed out: the client asks rpcbind for mountd's port and gets one the
# security group drops.
sudo tee /etc/nfs.conf.d/narwhal.conf >/dev/null <<'NFSCONFEOF'
[mountd]
port = 20048
manage-gids = y

[statd]
port = 4047
outgoing-port = 4048

[lockd]
port = 4045
udp-port = 4045

[nfsd]
threads = 8
vers3 = y
vers4 = y
NFSCONFEOF

# Belt and braces for lockd: the sysctls apply to the running kernel module immediately,
# where the nfs.conf value only takes effect when lockd next starts.
sudo tee /etc/sysctl.d/90-nfs-lockd.conf >/dev/null <<'LOCKDEOF'
fs.nfs.nlm_tcpport = 4045
fs.nfs.nlm_udpport = 4045
LOCKDEOF
sudo sysctl -p /etc/sysctl.d/90-nfs-lockd.conf >/dev/null 2>&1 || true

sudo systemctl daemon-reload
sudo systemctl enable nfs-kernel-server
sudo systemctl restart nfs-kernel-server
sudo systemctl restart nfs-mountd 2>/dev/null || true
sudo systemctl restart rpc-statd 2>/dev/null || true

# Assert the pinning took. mountd on a random port is exactly the state that made the v3
# mount time out, and it is invisible unless asked for.
MOUNTD_PORT=$(rpcinfo -p 2>/dev/null | awk '$5=="mountd" && $3=="tcp" {print $4; exit}')
if [ "${MOUNTD_PORT}" != "20048" ]; then
  echo "ERROR: mountd is on port ${MOUNTD_PORT:-unknown}, expected 20048." >&2
  echo "       The security group only opens 20048, so v3 mounts will time out." >&2
  rpcinfo -p >&2 || true
  exit 1
fi
echo "mountd pinned to ${MOUNTD_PORT}, lockd $(rpcinfo -p 2>/dev/null | awk '$5=="nlockmgr" && $3=="tcp" {print $4; exit}')"

# Verify exports
echo "=== NFS Exports ==="
sudo exportfs -v

echo "=== NFS Server Installation Done ==="
