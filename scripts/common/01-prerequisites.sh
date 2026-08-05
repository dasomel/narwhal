#!/bin/bash
set -euo pipefail

# Binaries come from the airgap bundle, never a download.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

echo "=== Prerequisites Installation ==="

#=========================================
# Force apt to use IPv4
#=========================================
# These VMs have no working IPv6 route. Mirrors like pkgs.k8s.io resolve to IPv6
# first, so apt picks the AAAA record and fails with "Network is unreachable"
# (observed on workers fetching kubeadm/kubelet/kubectl on Ubuntu 26.04). Pin IPv4.
echo 'Acquire::ForceIPv4 "true";' | sudo tee /etc/apt/apt.conf.d/99force-ipv4 >/dev/null

#=========================================
# AIRGAP: serve every .deb from the bundle instead of the network.
#
# This runs before anything else installs a package, so switching the sources here covers
# chrony/nfs-common/jq below, containerd in 02, and kubelet/kubeadm/kubectl in 03. Without
# it an "airgap" install still reached pkgs.k8s.io and the Ubuntu archive — the mirrored
# images and bundled charts were never the part that failed first.
#
# file:// rather than a local HTTP server: the bundle is already mounted at /srv/airgap/apt
# by the Vagrantfile, so there is no service to start, nothing to keep alive, and no order
# dependency between the registry and the first apt-get. The mount is under /srv and not
# under /home/vagrant because apt fetches as the unprivileged `_apt` user — see the
# Vagrantfile comment on that synced_folder.
#
# [trusted=yes] because the bundle is unsigned. It is built by scripts/airgap/07 from the
# same official sources this would otherwise fetch, and it never leaves the host.
#=========================================
if [ "${AIRGAP:-0}" = "1" ]; then
  # Enforce the isolation instead of assuming it. In a real airgap there is no default
  # route, so dropping it is a no-op; on this Vagrant setup there IS one, and leaving it
  # means any missed offline path silently succeeds over the internet and the gap is only
  # discovered on a customer's isolated network. That is exactly how the Helm charts sat in
  # the bundle unread for months — an "airgap" install fetched every chart from a public
  # repo and nobody noticed, because it worked.
  #
  # Host-only (192.168.56.0/24) and the NAT subnet stay reachable: they are directly
  # connected, so `vagrant ssh` and node-to-node traffic do not need the default route.
  if ip route show default 2>/dev/null | grep -q .; then
    echo "=== AIRGAP: dropping the default route to enforce isolation ==="
    ip route show default | sed 's/^/  removing: /'
    sudo ip route del default || true
  fi

  echo "=== AIRGAP: switching APT to the bundle ==="
  AIRGAP_APT_DIR="${AIRGAP_APT_DIR:-/srv/airgap/apt}"
  if [ ! -f "${AIRGAP_APT_DIR}/Packages.gz" ]; then
    echo "ERROR: AIRGAP=1 but ${AIRGAP_APT_DIR}/Packages.gz is missing." >&2
    echo "       Build it with scripts/airgap/07-save-apt-packages.sh, then re-run." >&2
    exit 1
  fi

  # Check readability as the user apt will actually fetch as. root can read the index while
  # `_apt` cannot, and apt renders that as "File not found - .../Packages" — a message that
  # sends you hunting for a missing index instead of a directory it cannot traverse.
  if ! sudo -u _apt test -r "${AIRGAP_APT_DIR}/Packages.gz"; then
    echo "ERROR: ${AIRGAP_APT_DIR}/Packages.gz is not readable by the _apt user." >&2
    echo "       Every directory on the path needs o+x (apt drops privileges to _apt)." >&2
    namei -m "${AIRGAP_APT_DIR}/Packages.gz" >&2 || true
    exit 1
  fi

  # Move the online sources aside rather than deleting them, so a node can be put back
  # online by reversing this. .disabled is not a suffix apt reads.
  for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
    [ -e "$f" ] || continue
    case "$f" in *.disabled) continue ;; esac
    sudo mv "$f" "$f.disabled"
  done

  echo "deb [trusted=yes] file://${AIRGAP_APT_DIR} ./" \
    | sudo tee /etc/apt/sources.list.d/narwhal-airgap.list >/dev/null
  sudo apt-get update -qq
  echo "  APT now serves $(apt-cache stats 2>/dev/null | awk '/Total package names/{print $4}') package names from the bundle"
fi

CLUSTER_NAME="${CLUSTER_NAME:-narwhal}"
MASTER_COUNT="${MASTER_COUNT:-3}"
MASTER_IP_BASE="${MASTER_IP_BASE:-192.168.56.1}"
VIP_ADDRESS="${VIP_ADDRESS:-192.168.56.100}"
WORKER_COUNT="${WORKER_COUNT:-3}"
WORKER_IP_BASE="${WORKER_IP_BASE:-192.168.56.2}"
NODE_IP="${NODE_IP:-}"
DOMAIN="${DOMAIN:-local.narwhal.internal}"

#=========================================
# Ensure private network IP is assigned
# VMware Vagrant plugin sometimes fails to create netplan config
#=========================================
if [ -n "${NODE_IP}" ]; then
  echo "Ensuring private network IP ${NODE_IP} is assigned..."

  # Find the interface that should have this IP (second ethernet, not the NAT one)
  # Wait for the interface to appear
  PRIV_IFACE=""
  for attempt in {1..30}; do
    # Look for interface with the IP already assigned (|| true: grep returns 1 on no match, pipefail would exit)
    PRIV_IFACE=$(ip -o addr show | grep "${NODE_IP}" | awk '{print $2}' | head -1 || true)
    if [ -n "${PRIV_IFACE}" ]; then
      echo "Private network IP already assigned on ${PRIV_IFACE}"
      break
    fi

    # Find the second ethernet interface (not the NAT one)
    NAT_IFACE=$(ip -o addr show | grep "172\\.16\\." | awk '{print $2}' | head -1)
    CANDIDATE=$(ip -o link show | grep -v lo | grep -v "${NAT_IFACE:-__none__}" | awk -F': ' '{print $2}' | head -1)

    if [ -n "${CANDIDATE}" ]; then
      # Check if it has no IPv4 address
      HAS_IP=$(ip -o -4 addr show "${CANDIDATE}" 2>/dev/null | wc -l)
      if [ "${HAS_IP}" -eq 0 ]; then
        echo "Configuring ${CANDIDATE} with ${NODE_IP}/24 via netplan..."
        sudo tee /etc/netplan/50-vagrant.yaml > /dev/null <<NETEOF
---
network:
  version: 2
  renderer: networkd
  ethernets:
    ${CANDIDATE}:
      addresses:
      - ${NODE_IP}/24
NETEOF
        sudo chmod 600 /etc/netplan/50-vagrant.yaml
        sudo netplan apply 2>&1 || true
        sleep 2
        # Verify
        if ip -o addr show | grep -q "${NODE_IP}"; then
          PRIV_IFACE="${CANDIDATE}"
          echo "Private network IP ${NODE_IP} assigned on ${PRIV_IFACE}"
          break
        fi
      fi
    fi

    echo "Waiting for private network interface... (${attempt}/30)"
    sleep 2
  done

  if [ -z "${PRIV_IFACE}" ]; then
    echo "ERROR: Could not assign private network IP ${NODE_IP}"
    ip -o addr show
    exit 1
  fi
fi

#=========================================
# Configure DNS (fix VMware DNS issues)
#=========================================
echo "Configuring DNS..."

# Add master-1 dnsmasq as primary DNS for *.${DOMAIN} resolution
# The ~${DOMAIN} routing domain ensures only matching queries go to dnsmasq
# On master-1, 10-dnsmasq.sh replaces systemd-resolved entirely, so this is a no-op.
# Cloud: dnsmasq is skipped (L2-bound), so do NOT repoint DNS at the master nodes
# (that would break resolution). Keep the default cloud resolver; *.${DOMAIN} is
# covered by /etc/hosts entries below + in-cluster CoreDNS.
# Cloud: dnsmasq runs on the BASTION (setup-bastion-proxy.sh) rather than on master-1,
# because it has to answer before the cluster exists and squid on the same host then
# resolves these names for proxied requests too. DNS_SERVER is the bastion's private IP.
if [ "${PROVIDER:-vagrant}" = "kakao" ] && [ -n "${DNS_SERVER:-}" ] \
  && systemctl is-active --quiet systemd-resolved; then
  echo "Routing *.${DOMAIN} to ${DNS_SERVER} (bastion dnsmasq)..."
  sudo mkdir -p /etc/systemd/resolved.conf.d
  # Domains=~ makes this a routing-only domain: only *.${DOMAIN} goes to the bastion, and
  # everything else keeps using the cloud resolver. Without the ~ this would become a
  # search suffix and hijack unqualified lookups.
  sudo tee /etc/systemd/resolved.conf.d/narwhal-dns.conf << EOF
[Resolve]
DNS=${DNS_SERVER}
Domains=~${DOMAIN}
EOF
  sudo systemctl restart systemd-resolved
  resolvectl query "keycloak.${DOMAIN}" 2>&1 | head -2 || echo "  WARN: ${DOMAIN} not resolving yet"
elif [ "${PROVIDER:-vagrant}" != "kakao" ] && systemctl is-active --quiet systemd-resolved; then
  sudo mkdir -p /etc/systemd/resolved.conf.d
  # Build DNS list dynamically from all master IPs
  MASTER_DNS=""
  for idx in $(seq 0 $((MASTER_COUNT - 1))); do
    MASTER_DNS="${MASTER_DNS}${MASTER_IP_BASE}${idx} "
  done
  # Primary DNS = master dnsmasq nodes only; public resolvers go to FallbackDNS.
  # Cramming masters + 8.8.8.8 + 8.8.4.4 into DNS= exceeds the glibc 3-nameserver
  # limit ("Nameserver limits were exceeded, some omitted") so the public ones
  # were silently dropped from /etc/resolv.conf anyway. FallbackDNS is the correct
  # place for them — systemd-resolved uses it when the primary servers fail.
  sudo tee /etc/systemd/resolved.conf.d/dns.conf << EOF
[Resolve]
DNS=${MASTER_DNS}
FallbackDNS=8.8.8.8 8.8.4.4 1.1.1.1
Domains=~${DOMAIN}
EOF
  sudo systemctl restart systemd-resolved
fi

# Verify DNS is working
echo "Testing DNS resolution..."
nslookup registry.k8s.io || echo "DNS test warning, continuing..."

# Configure /etc/hosts
cat <<EOF | sudo tee -a /etc/hosts

# Kubernetes Cluster
${VIP_ADDRESS}   ${CLUSTER_NAME}-vip
EOF

# Add master nodes
for i in $(seq 1 "$MASTER_COUNT"); do
  MASTER_IP="${MASTER_IP_BASE}$((i - 1))"
  echo "${MASTER_IP}   ${CLUSTER_NAME}-master-${i}" | sudo tee -a /etc/hosts
done

# Backward compatibility alias (narwhal-master -> master-1)
echo "${MASTER_IP_BASE}0   ${CLUSTER_NAME}-master" | sudo tee -a /etc/hosts

# Add worker nodes
for i in $(seq 1 "$WORKER_COUNT"); do
  echo "${WORKER_IP_BASE}${i}   ${CLUSTER_NAME}-worker-${i}" | sudo tee -a /etc/hosts
done

# DNS leak fix: systemd-resolved leaks *.local.narwhal.internal to NAT interface (enp2s0),
# causing harbor.local.narwhal.internal to resolve to its public AWS IP → TLS 'unrecognized name'.
# Pin harbor to the APISIX gateway's MetalLB LB IP so all nodes resolve it
# internally. NOTE: this is the APISIX LB IP (192.168.56.200), NOT the
# kube-apiserver VIP (${VIP_ADDRESS}=192.168.56.100) — harbor is served via APISIX.
APISIX_LB_IP="${APISIX_LB_IP:-192.168.56.200}"

# Vagrant only. systemd-resolved leaks *.${DOMAIN} to the NAT interface, so harbor
# resolves to a public address and image pulls fail with TLS 'unrecognized name'. Pin it.
#
# The cloud path deliberately has no equivalent. Enumerating service names here was tried
# and is the wrong shape: the list has to track every route that gets added, a presence
# check keyed on one name means a second never applies, and none of it helps squid, which
# resolves on behalf of every proxied request. The bastion's dnsmasq answers the whole
# wildcard zone for both the nodes and squid.
if [ "${PROVIDER:-vagrant}" != "kakao" ]; then
  if ! grep -q "harbor.${DOMAIN}" /etc/hosts; then
    echo "${APISIX_LB_IP}   harbor.${DOMAIN}" | sudo tee -a /etc/hosts
    echo "Added harbor.${DOMAIN} -> ${APISIX_LB_IP} to /etc/hosts"
  else
    echo "harbor.${DOMAIN} already present in /etc/hosts, skipping"
  fi
fi
#=========================================
# Configure Clock Synchronization (chrony)
#=========================================
echo "Configuring clock synchronization via chrony..."
if ! command -v chronyc &>/dev/null; then
  echo "Installing chrony..."
  sudo apt-get update || echo "WARN: apt-get update issue, continuing..."
  sudo apt-get install -y chrony || echo "WARN: chrony install issue, continuing..."
fi

# Detect the chrony unit name (chrony.service on 24.04; chronyd.service on some releases).
# Ubuntu 26.04 ships no systemd-timesyncd, so chrony is the single source of truth — no
# timesyncd fallback (it only produced noisy "Unit not found" errors on 26.04).
sudo systemctl daemon-reload 2>/dev/null || true
CHRONY_SVC=""
# `systemctl cat`, not `systemctl list-unit-files | grep -q`. grep -q exits the moment it
# matches, which closes the pipe while systemctl is still writing its (long) unit table;
# systemctl dies of SIGPIPE and `set -o pipefail` turns rc=141 into a false "not found".
# Measured on a node where chrony.service was installed and enabled: 37 of 40 runs reported
# NOT FOUND, `systemctl cat` 0 of 40. The failure was silent and expensive — everything
# below is inside `if [ -n "${CHRONY_SVC}" ]`, so a false negative skipped the makestep
# 0.1 -1 fix (D12) and the kubelet clock-sync gate entirely, and the only trace was one
# WARN line claiming clock sync had been left to the hypervisor.
for c in chrony chronyd; do
  if systemctl cat "${c}.service" >/dev/null 2>&1; then
    CHRONY_SVC="$c"
    break
  fi
done
if [ -n "${CHRONY_SVC}" ]; then
  # D12: Ubuntu's default `makestep 1 3` only allows chrony to hard-step the clock
  # during its first 3 corrections after startup; afterwards it falls back to slow
  # slewing only. On a host under heavy CPU contention (many VMs/background jobs),
  # the guest clock can drift again well into the node's uptime, and slew alone
  # can't catch up (observed: chrony sources stuck ~400ms off an hour after boot),
  # causing containerd/kubelet timestamp issues (0/1 <invalid>, CreateContainerError)
  # long after the initial sync. The observed stuck offset (~400ms, persisting for
  # over an hour) was UNDER Ubuntu's default 1-second makestep threshold, so it was
  # never stepped at all — chrony was relying solely on its slow default slew rate
  # (~83ppm), which takes roughly (offset / slew_rate) ≈ 400ms / 0.0000833 ≈ 80
  # minutes to correct. `makestep 0.1 -1` steps immediately whenever the offset
  # exceeds 100ms, for the life of the daemon, so drift converges in seconds.
  # Edited before the restart below so chronyd is only bounced once.
  CHRONY_CONF="/etc/chrony/chrony.conf"
  if [ -f "${CHRONY_CONF}" ]; then
    sudo sed -i -E 's/^makestep\s+[0-9.]+\s+[0-9-]+/makestep 0.1 -1/' "${CHRONY_CONF}"
    grep -q "^makestep 0.1 -1" "${CHRONY_CONF}" || echo "makestep 0.1 -1" | sudo tee -a "${CHRONY_CONF}" >/dev/null
  fi

  echo "Enabling and starting ${CHRONY_SVC}..."
  sudo systemctl enable "${CHRONY_SVC}" || true
  sudo systemctl restart "${CHRONY_SVC}" || true
  sudo timedatectl set-ntp true || true
  sudo chronyc -a makestep || true

  # D-clock/D12: gate kubelet (via ordering, not a hard block) on chrony sync so the
  # clock is correct before static pods record their startTime. Without this, a
  # post-boot makestep() produces ghost containers with zero timestamps → 0/1
  # <invalid> on all static pods. Loosened from the original single-shot
  # `chronyc waitsync 30 0.01 1.0 0` (10ms tolerance failed reliably under host
  # load, and the unit's own hard failure only delayed the log, not kubelet, since
  # the drop-in uses Wants= not Requires=): poll for up to 2 minutes with a
  # relaxed 200ms tolerance, and — critically — ALWAYS exit 0 so the unit never
  # reports "failed"; the ordering (After=) still delays kubelet while polling.
  sudo tee /usr/local/bin/narwhal-clock-sync.sh >/dev/null <<'SCRIPT'
#!/bin/bash
# D12: best-effort clock-sync gate. Never fails — see 01-prerequisites.sh D12 note.
for _ in $(seq 1 24); do
  if chronyc waitsync 1 0.2 1.0 0 &>/dev/null; then
    echo "narwhal-clock-sync: synced within 200ms"
    exit 0
  fi
  sleep 5
done
echo "narwhal-clock-sync: WARN did not converge to 200ms within 2min, proceeding anyway"
exit 0
SCRIPT
  sudo chmod 0755 /usr/local/bin/narwhal-clock-sync.sh

  sudo tee /etc/systemd/system/narwhal-clock-sync.service >/dev/null <<'UNIT'
[Unit]
Description=Wait for chrony clock sync before kubelet (best-effort, never fails)
DefaultDependencies=no
After=chrony.service
Wants=chrony.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/narwhal-clock-sync.sh

[Install]
WantedBy=kubelet.service
WantedBy=containerd.service
UNIT
  # D14: containerd must ALSO be gated on clock sync, not just kubelet. Root cause
  # found live: on a cold VM boot the guest clock can start off by hours (observed
  # a -9h correction); 02-containerd.sh (which installs+starts containerd) runs
  # AFTER this script, so on a fresh install containerd itself is usually fine —
  # but on every REBOOT, containerd.service starts from systemd's normal boot
  # ordering, independent of this script, and can start creating/timestamping
  # container objects BEFORE chrony corrects a large drift. When chrony later
  # steps the clock backward, those already-created objects appear to have
  # CreatedAt timestamps in the "future" relative to the corrected clock, which
  # corrupts containerd's CRI store bookkeeping and produces the persistent
  # "failed to reserve container name" wedge (kubelet retries collide with the
  # stale future-timestamped reservation forever). Same drop-in for both services.
  for svc in kubelet containerd; do
    sudo mkdir -p "/etc/systemd/system/${svc}.service.d"
    sudo tee "/etc/systemd/system/${svc}.service.d/10-after-chrony.conf" >/dev/null <<'DROP'
[Unit]
After=narwhal-clock-sync.service
Wants=narwhal-clock-sync.service
DROP
  done
  sudo systemctl daemon-reload
  sudo systemctl enable narwhal-clock-sync.service || true
  echo "Clock-sync gate installed: narwhal-clock-sync.service blocks kubelet until synced"
else
  echo "WARN: chrony service not found after install; clock sync left to the hypervisor"
fi


# ──────────────────────────────────────────────
# Kernel prerequisites for kubeadm
#
# Nothing in this repo used to establish these — the pre-baked box already ships
# /etc/sysctl.d/k8s-network.conf and /etc/modules-load.d/k8s.conf, so the dependency
# was invisible until a plain cloud image was used. There kubeadm init dies at
# preflight with
#   [ERROR FileContent--proc-sys-net-ipv4-ip_forward]: contents are not set to 1
# Idempotent, so it is a no-op on the box.
#
# br_netfilter must be loaded BEFORE the bridge sysctls are applied: the
# net.bridge.* keys do not exist until the module is in, and sysctl silently skips
# missing keys.
# ──────────────────────────────────────────────
# NFS client. No script installed it — the box ships nfs-common, so on a cloud image
# every csi-driver-nfs mount fails with
#   mount: bad option; ... you might need a /sbin/mount.<type> helper program
# which surfaces to the pod as a 110s mount timeout, not as a missing package.
# Needed on every node, not just the server: the CSI node plugin mounts on whichever
# node the pod lands.
echo "Ensuring NFS client (nfs-common)..."
dpkg -s nfs-common >/dev/null 2>&1 || sudo apt-get install -y nfs-common
echo "  mount.nfs: $(command -v mount.nfs || echo MISSING)"

# NFSv3 lock callbacks come FROM the server TO this node, on statd's port. Left dynamic
# they land on whatever the portmapper picked, which a default-deny security group drops —
# and a dropped callback hangs rather than fails. Pin them so the group can name them;
# the server pins the matching ports in 01-nfs-server.sh.
# /etc/nfs.conf, not /etc/default/nfs-common: the systemd units read the former, so
# STATDOPTS in the defaults file is silently ignored (the server hit exactly this with
# mountd, which kept binding random high ports the security group drops).
sudo mkdir -p /etc/nfs.conf.d
sudo tee /etc/nfs.conf.d/narwhal.conf >/dev/null <<'NFSCEOF'
[statd]
port = 4047
outgoing-port = 4048

[lockd]
port = 4045
udp-port = 4045
NFSCEOF
sudo tee /etc/sysctl.d/90-nfs-lockd.conf >/dev/null <<'LOCKDEOF'
fs.nfs.nlm_tcpport = 4045
fs.nfs.nlm_udpport = 4045
LOCKDEOF
sudo sysctl -p /etc/sysctl.d/90-nfs-lockd.conf >/dev/null 2>&1 || true
sudo systemctl restart rpc-statd 2>/dev/null || true
echo "  statd pinned to 4047/4048, lockd to 4045"

# jq. Present on both images today, but only as a transitive dependency — nothing in the
# repo ever asked for it, while 11-3-keycloak-clients.sh drives Keycloak entirely through
# it. One base image that stops pulling it in breaks Phase 2, so make the need explicit.
echo "Ensuring jq..."
dpkg -s jq >/dev/null 2>&1 || sudo apt-get install -y jq
echo "  jq: $(jq --version 2>/dev/null || echo MISSING)"

# yq. The pre-baked Vagrant box ships it, so nothing here had ever had to install it —
# on a plain cloud image patch-apiserver-memory.sh dies with `yq: command not found`
# straight after a successful `kubeadm init`, and 11-4-keycloak-apiserver.sh would have
# hit the same wall later in Phase 2.
#
# It must be mikefarah/yq (Go), NOT the `yq` in apt, which is a Python wrapper around jq
# with different semantics — `yq -i "(.spec.containers[] | select(...)).env += [...]"`
# is v4 syntax and the wrapper cannot do in-place edits at all.
YQ_VERSION="${YQ_VERSION:-v4.44.6}"
if ! command -v yq >/dev/null 2>&1; then
  echo "Installing yq ${YQ_VERSION} from the airgap bundle..."
  install_bin yq
fi
# Fail loudly rather than letting a jq-wrapper masquerade as yq until the first -i edit.
if ! yq --version 2>&1 | grep -q 'mikefarah'; then
  echo "ERROR: yq is not mikefarah/yq — got: $(yq --version 2>&1)" >&2
  echo "       Remove the apt 'yq' package and re-run; the manifest edits need v4." >&2
  exit 1
fi
echo "  yq: $(yq --version)"

echo "Ensuring kubeadm kernel prerequisites (modules + sysctl)..."
sudo tee /etc/modules-load.d/k8s.conf >/dev/null <<'MODEOF'
overlay
br_netfilter
MODEOF
for mod in overlay br_netfilter; do
  sudo modprobe "${mod}" 2>/dev/null || echo "  WARN: modprobe ${mod} failed"
done

sudo tee /etc/sysctl.d/k8s-network.conf >/dev/null <<'SYSEOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
SYSEOF
sudo sysctl --system >/dev/null 2>&1 || true

echo "  ip_forward=$(cat /proc/sys/net/ipv4/ip_forward)" \
     "bridge-nf-call-iptables=$(cat /proc/sys/net/bridge/bridge-nf-call-iptables 2>/dev/null || echo NA)"

echo "=== Prerequisites Done ==="
