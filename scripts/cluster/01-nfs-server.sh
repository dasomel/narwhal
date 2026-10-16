#!/bin/bash
set -euo pipefail

NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
HOST_NETWORK_CIDR="${HOST_NETWORK_CIDR:-192.168.56.0/24}"
POD_NETWORK_CIDR="${POD_NETWORK_CIDR:-10.244.0.0/16}"
# Optional dedicated data device (Kakao: /dev/disk/by-id/virtio-<volume id[0:20]>, set by
# scripts/cloud/provision-kakao.sh). Empty/unset keeps the share on the root filesystem.
NFS_DATA_DEVICE="${NFS_DATA_DEVICE:-}"

echo "=== NFS Server Installation ==="
echo "Share Path: ${NFS_SHARE_PATH}"

# Install NFS server and quota tools
sudo apt-get update
sudo apt-get install -y nfs-kernel-server quota quotatool

# Dedicated XFS data volume (only when NFS_DATA_DEVICE is set; Vagrant leaves it unset).
# XFS project quota can only be enabled at the initial mount, so the export lives on its
# own prjquota-mounted filesystem. D-NFS-VOL safety rules, in order of risk:
#  - never mkfs a device that already carries a filesystem (data loss);
#  - never mount over a non-empty share path: moving live data is a runbook
#    (docs/runbooks/nfs-xfs-migration.md), not something a provisioning script may do.
if [ -n "${NFS_DATA_DEVICE}" ]; then
  echo "Dedicated NFS data device: ${NFS_DATA_DEVICE}"
  for _ in $(seq 1 60); do
    [ -b "${NFS_DATA_DEVICE}" ] && break
    sleep 2
  done
  if [ ! -b "${NFS_DATA_DEVICE}" ]; then
    echo "ERROR: ${NFS_DATA_DEVICE} did not appear within 120s (is the volume attached?)." >&2
    exit 1
  fi
  data_dev=$(readlink -f "${NFS_DATA_DEVICE}")
  mounted_src=$(findmnt -rno SOURCE --mountpoint "${NFS_SHARE_PATH}" 2>/dev/null || true)
  if [ -n "${mounted_src}" ] && [ "$(readlink -f "${mounted_src}")" = "${data_dev}" ]; then
    echo "${data_dev} is already mounted on ${NFS_SHARE_PATH}"
  else
    sudo mkdir -p "${NFS_SHARE_PATH}"
    if [ -n "$(sudo ls -A "${NFS_SHARE_PATH}")" ]; then
      echo "ERROR: ${NFS_SHARE_PATH} is not empty and ${data_dev} is not mounted there." >&2
      echo "       Refusing to mount over live data. Migrate with docs/runbooks/nfs-xfs-migration.md." >&2
      exit 1
    fi
    fs_type=$(sudo blkid -p -o value -s TYPE "${data_dev}" || true)
    if [ -z "${fs_type}" ]; then
      echo "No filesystem on ${data_dev}; creating XFS (label narwhal-nfs)"
      sudo mkfs.xfs -L narwhal-nfs "${data_dev}"
    elif [ "${fs_type}" != "xfs" ]; then
      echo "ERROR: ${data_dev} already holds a '${fs_type}' filesystem; refusing to touch it." >&2
      exit 1
    fi
    data_uuid=$(sudo blkid -p -o value -s UUID "${data_dev}")
    sudo sed -i "\|[[:space:]]${NFS_SHARE_PATH}[[:space:]]|d" /etc/fstab
    echo "UUID=${data_uuid} ${NFS_SHARE_PATH} xfs defaults,prjquota,nofail 0 2" | sudo tee -a /etc/fstab >/dev/null
    sudo systemctl daemon-reload
    sudo mount "${NFS_SHARE_PATH}"
  fi
  # nofail keeps boot alive without the disk; the NFS server must not export an empty
  # root-disk directory in that case.
  sudo mkdir -p /etc/systemd/system/nfs-server.service.d
  printf '[Unit]\nRequiresMountsFor=%s\n' "${NFS_SHARE_PATH}" |
    sudo tee /etc/systemd/system/nfs-server.service.d/requires-mount.conf >/dev/null
  sudo systemctl daemon-reload
  if ! findmnt -rno FSTYPE,OPTIONS --mountpoint "${NFS_SHARE_PATH}" | grep -Eq '^xfs .*(prjquota|pquota)'; then
    echo "ERROR: ${NFS_SHARE_PATH} is not an XFS mount with prjquota:" >&2
    findmnt "${NFS_SHARE_PATH}" >&2 || true
    exit 1
  fi
fi

# Create NFS share directory
#
# Least-privilege default (narwhal#186): the share root used to be mode 0777, world
# writable to any client on the export ACL regardless of squash. Owner-only rwx is
# enough — the only writer is the nfs.csi.k8s.io provisioner (05-nfs-quota-agent.sh),
# which creates PV subdirectories as the client's root, squashed below to nobody, the
# same identity that owns this directory. Nothing else needs write access to the root.
sudo mkdir -p "${NFS_SHARE_PATH}"
sudo chown nobody:nogroup "${NFS_SHARE_PATH}"
sudo chmod 750 "${NFS_SHARE_PATH}"

# csi-driver-nfs creates <namespace>/<pvc> directories through root_squash as
# nobody:nogroup. A server-side timer owns this permission repair: unlike chmod over the
# client export, it runs as real root, and unlike the quota agent it does not depend on
# container UID/capabilities or the agent's hostPath view. find is deliberately depth-2,
# same-filesystem, and non-following so only PV directories change, never the share root,
# namespace directories, or symlink targets. A pod can be scheduled in the few seconds
# before the next pass; its initial write may fail and restart, then succeed after mode
# convergence (the live check retries writes for this startup window). Use 0777 because
# the platform's workloads have different UIDs and fsGroups, with no shared per-PVC GID
# contract; a fixed group mode would leave some supported workloads unable to write.
sudo tee /usr/local/sbin/narwhal-nfs-pv-permissions >/dev/null <<'PERMISSIONEOF'
#!/bin/sh
set -eu
find /srv/nfs/k8s -xdev -mindepth 2 -maxdepth 2 -type d -exec chmod 0777 -- {} +
PERMISSIONEOF
sudo chmod 0755 /usr/local/sbin/narwhal-nfs-pv-permissions

sudo tee /etc/systemd/system/narwhal-nfs-pv-permissions.service >/dev/null <<'UNITEOF'
[Unit]
Description=Set writable permissions on NFS CSI PV directories
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/narwhal-nfs-pv-permissions
UNITEOF

sudo tee /etc/systemd/system/narwhal-nfs-pv-permissions.timer >/dev/null <<'TIMEREOF'
[Unit]
Description=Converge permissions on new NFS CSI PV directories

[Timer]
OnBootSec=5s
OnUnitActiveSec=5s
Unit=narwhal-nfs-pv-permissions.service

[Install]
WantedBy=timers.target
TIMEREOF
sudo systemctl daemon-reload
sudo systemctl enable --now narwhal-nfs-pv-permissions.timer
# Repair any PV directories that predate this installation immediately.
sudo systemctl start narwhal-nfs-pv-permissions.service

# Configure exports
#
# Least-privilege default (narwhal#186): no_root_squash used to be the default for both
# the host and pod networks, so any root-capable client on either CIDR retained real root
# identity on the export. No component in this repo has been found to require that —
# csi-driver-nfs's provisioner only needs to create/chown subdirectories under a root it
# already owns as nobody, which root_squash (the default when root_squash/no_root_squash
# is omitted) already allows since the share root above is owned nobody:nogroup. If a
# future component needs real root identity on the export, add it as its own scoped
# export line here with root_squash left off, and document the owner/scope/reason next
# to it plus a regression check below — do not remove root_squash from these two lines.
cat <<EOF | sudo tee /etc/exports
# Kubernetes NFS share
${NFS_SHARE_PATH}  ${HOST_NETWORK_CIDR}(rw,sync,no_subtree_check,root_squash)
${NFS_SHARE_PATH}  ${POD_NETWORK_CIDR}(rw,sync,no_subtree_check,root_squash)
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
# Capture, then test: two greps chained on `mount` can SIGPIPE it under pipefail.
mount_entry=$(mount | grep "${NFS_MOUNT}" || true)
if ! grep -q prjquota <<<"${mount_entry}"; then
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
