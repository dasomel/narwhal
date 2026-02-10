#!/bin/bash
set -euo pipefail

NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
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
${NFS_SHARE_PATH}  192.168.56.0/24(rw,sync,no_subtree_check,no_root_squash)
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

# Initialize project quota directory
sudo mkdir -p /etc/projects /etc/projid
sudo touch /etc/projects /etc/projid

# Enable and start NFS server
sudo systemctl enable nfs-kernel-server
sudo systemctl restart nfs-kernel-server

# Verify exports
echo "=== NFS Exports ==="
sudo exportfs -v

echo "=== NFS Server Installation Done ==="
