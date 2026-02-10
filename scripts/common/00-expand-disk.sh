#!/bin/bash
set -euo pipefail

#=========================================
# Disk Expansion Script
#=========================================
# Expands the root partition to use all available disk space
# Required for IDP full deployment (minimum 50GB)
# This script runs inside the Linux VM during provisioning

# Check if running on Linux
if [[ "$(uname)" != "Linux" ]]; then
  echo "This script is designed to run inside the Linux VM."
  echo "It will be executed automatically during 'vagrant up'."
  exit 0
fi

echo "=== Disk Expansion ==="

# Ensure LVM directories exist (prevents archive errors)
sudo mkdir -p /etc/lvm/archive /etc/lvm/backup
sudo chmod 755 /etc/lvm/archive /etc/lvm/backup

# Check current disk usage
echo "Current disk usage:"
df -h /

# Get the root device
ROOT_DEVICE=$(findmnt -n -o SOURCE /)
echo "Root device: ${ROOT_DEVICE}"

# Check if LVM is used
if [[ "${ROOT_DEVICE}" == /dev/mapper/* ]]; then
  echo "LVM detected. Expanding LVM..."

  # Get the physical volume
  VG_NAME=$(lvs --noheadings -o vg_name ${ROOT_DEVICE} | tr -d ' ')
  PV_DEVICE=$(pvs --noheadings -o pv_name -S vg_name=${VG_NAME} | tr -d ' ' | head -1)

  echo "Volume Group: ${VG_NAME}"
  echo "Physical Volume: ${PV_DEVICE}"

  # Find the partition device (e.g., /dev/sda3 or /dev/nvme0n1p3)
  if [[ "${PV_DEVICE}" =~ nvme ]]; then
    # NVMe device
    DISK_DEVICE=$(echo "${PV_DEVICE}" | sed 's/p[0-9]*$//')
    PART_NUM=$(echo "${PV_DEVICE}" | grep -oP 'p\K[0-9]+$')
  else
    # SCSI/SATA device
    DISK_DEVICE=$(echo "${PV_DEVICE}" | sed 's/[0-9]*$//')
    PART_NUM=$(echo "${PV_DEVICE}" | grep -oP '[0-9]+$')
  fi

  echo "Disk device: ${DISK_DEVICE}"
  echo "Partition number: ${PART_NUM}"

  # Expand partition using growpart
  if command -v growpart &> /dev/null; then
    echo "Expanding partition with growpart..."
    sudo growpart ${DISK_DEVICE} ${PART_NUM} || true
  else
    echo "Installing cloud-guest-utils for growpart..."
    sudo apt-get update && sudo apt-get install -y cloud-guest-utils
    sudo growpart ${DISK_DEVICE} ${PART_NUM} || true
  fi

  # Resize physical volume
  echo "Resizing physical volume..."
  sudo pvresize ${PV_DEVICE}

  # Extend logical volume to use all free space
  echo "Extending logical volume..."
  sudo lvextend -l +100%FREE ${ROOT_DEVICE} || true

  # Resize filesystem (detect type first)
  echo "Resizing filesystem..."
  FS_TYPE=$(df -T / | tail -1 | awk '{print $2}')
  echo "Filesystem type: ${FS_TYPE}"
  if [[ "${FS_TYPE}" == "xfs" ]]; then
    sudo xfs_growfs /
  elif [[ "${FS_TYPE}" == "ext4" ]] || [[ "${FS_TYPE}" == "ext3" ]]; then
    sudo resize2fs ${ROOT_DEVICE}
  else
    echo "Unknown filesystem type: ${FS_TYPE}"
  fi

else
  # Non-LVM: direct partition
  echo "Non-LVM partition detected."

  if [[ "${ROOT_DEVICE}" =~ nvme ]]; then
    DISK_DEVICE=$(echo "${ROOT_DEVICE}" | sed 's/p[0-9]*$//')
    PART_NUM=$(echo "${ROOT_DEVICE}" | grep -oP 'p\K[0-9]+$')
  else
    DISK_DEVICE=$(echo "${ROOT_DEVICE}" | sed 's/[0-9]*$//')
    PART_NUM=$(echo "${ROOT_DEVICE}" | grep -oP '[0-9]+$')
  fi

  # Expand partition
  if command -v growpart &> /dev/null; then
    sudo growpart ${DISK_DEVICE} ${PART_NUM} || true
  else
    sudo apt-get update && sudo apt-get install -y cloud-guest-utils
    sudo growpart ${DISK_DEVICE} ${PART_NUM} || true
  fi

  # Resize filesystem (detect type first)
  FS_TYPE=$(df -T / | tail -1 | awk '{print $2}')
  echo "Filesystem type: ${FS_TYPE}"
  if [[ "${FS_TYPE}" == "xfs" ]]; then
    sudo xfs_growfs /
  elif [[ "${FS_TYPE}" == "ext4" ]] || [[ "${FS_TYPE}" == "ext3" ]]; then
    sudo resize2fs ${ROOT_DEVICE}
  else
    echo "Unknown filesystem type: ${FS_TYPE}"
  fi
fi

echo ""
echo "Disk expansion complete!"
echo "New disk usage:"
df -h /

echo "=== Disk Expansion Done ==="
