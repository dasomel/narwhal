#!/bin/bash
set -euo pipefail

# narwhal boot-heal installer
# Runs ONCE during provisioning (all nodes); the installed systemd units
# then fire automatically on every subsequent boot.
# Idempotent: safe to run more than once.

echo "=== Boot-Heal Install ==="

SCRIPTS_DIR="/home/vagrant/scripts"
BIN_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"

#=========================================
# 1. Install per-node boot-heal
#    (all nodes: masters + workers)
#=========================================
echo "Installing narwhal-boot-heal.sh -> ${BIN_DIR}/narwhal-boot-heal.sh"
cp "${SCRIPTS_DIR}/common/boot-heal.sh" "${BIN_DIR}/narwhal-boot-heal.sh"
chmod 0755 "${BIN_DIR}/narwhal-boot-heal.sh"

cat > "${SYSTEMD_DIR}/narwhal-boot-heal.service" <<'EOF'
[Unit]
Description=Narwhal per-node boot wedge healer
Documentation=https://github.com/dasomel/narwhal
# Run after containerd and kubelet have been started by systemd ordering.
# A 45s internal sleep in the script gives them additional settle time.
After=containerd.service kubelet.service
Wants=containerd.service kubelet.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/narwhal-boot-heal.sh
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier=narwhal-boot-heal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable narwhal-boot-heal.service
echo "narwhal-boot-heal.service enabled"

#=========================================
# 1b. Install periodic timer layer (ADDITIONAL to the boot-time
#     oneshot above, not a replacement): re-runs the same wedge
#     check-and-heal logic every 2 minutes for the life of the node,
#     so a wedge that appears mid-runtime (e.g. under Phase-2 host
#     load, long after boot) gets self-healed without waiting for the
#     next reboot. Detection in boot-heal.sh is conservative (see
#     comments in that script) so this is a no-op on a healthy node.
#=========================================
echo "Installing narwhal-boot-heal.timer (periodic safety net)"

cat > "${SYSTEMD_DIR}/narwhal-boot-heal-periodic.service" <<'EOF'
[Unit]
Description=Narwhal per-node periodic wedge healer (timer-triggered)
Documentation=https://github.com/dasomel/narwhal

[Service]
Type=oneshot
ExecStart=/usr/local/bin/narwhal-boot-heal.sh --periodic
StandardOutput=journal
StandardError=journal
SyslogIdentifier=narwhal-boot-heal
EOF

cat > "${SYSTEMD_DIR}/narwhal-boot-heal.timer" <<'EOF'
[Unit]
Description=Periodically re-run the narwhal per-node wedge healer
Documentation=https://github.com/dasomel/narwhal

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit=narwhal-boot-heal-periodic.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now narwhal-boot-heal.timer
echo "narwhal-boot-heal.timer enabled and started"

#=========================================
# 2. Install cluster-level heal on master-1 only
#    Detection: hostname == narwhal-master-1
#=========================================
NODE_HOSTNAME="$(hostname)"
if [[ "${NODE_HOSTNAME}" == "narwhal-master-1" ]]; then
  echo "Detected master-1; installing narwhal-cluster-heal.sh"

  cp "${SCRIPTS_DIR}/cluster/cluster-heal.sh" "${BIN_DIR}/narwhal-cluster-heal.sh"
  chmod 0755 "${BIN_DIR}/narwhal-cluster-heal.sh"

  cat > "${SYSTEMD_DIR}/narwhal-cluster-heal.service" <<'EOF'
[Unit]
Description=Narwhal cluster-level post-reboot healer (master-1)
Documentation=https://github.com/dasomel/narwhal
# Requires network connectivity (kubectl uses the cluster VIP).
# Also wait for the per-node boot-heal to finish first.
After=network-online.target narwhal-boot-heal.service
Wants=network-online.target
Requires=narwhal-boot-heal.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/narwhal-cluster-heal.sh
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier=narwhal-cluster-heal

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable narwhal-cluster-heal.service
  echo "narwhal-cluster-heal.service enabled"
else
  echo "Not master-1 (${NODE_HOSTNAME}); skipping cluster-heal install"
fi

echo "=== Boot-Heal Install Done ==="
