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
sudo tee /etc/default/nfs-kernel-server >/dev/null <<'NFSDEOF'
RPCNFSDCOUNT=8
RPCMOUNTDOPTS="--manage-gids --port 20048"
NFSDOPTS=""
NFSDOPTS="$NFSDOPTS --nfs-version 3,4"
NFSDEOF

sudo tee /etc/default/nfs-common >/dev/null <<'NFSCEOF'
NEED_STATD=yes
STATDOPTS="--port 4047 --outgoing-port 4048"
NEED_IDMAPD=yes
NEED_GSSD=no
NFSCEOF

# lockd has no options file; it takes sysctls.
sudo tee /etc/sysctl.d/90-nfs-lockd.conf >/dev/null <<'LOCKDEOF'
fs.nfs.nlm_tcpport = 4045
fs.nfs.nlm_udpport = 4045
LOCKDEOF
sudo sysctl -p /etc/sysctl.d/90-nfs-lockd.conf >/dev/null 2>&1 || true

sudo systemctl daemon-reload
sudo systemctl enable nfs-kernel-server
sudo systemctl restart nfs-kernel-server
sudo systemctl restart rpc-statd 2>/dev/null || true

# Verify exports
echo "=== NFS Exports ==="
sudo exportfs -v

echo "=== NFS Server Installation Done ==="
