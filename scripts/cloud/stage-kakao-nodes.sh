#!/bin/bash
# Stage the repo onto Kakao Cloud nodes at the paths the provisioning scripts expect.
#
# Vagrant mounts scripts/, configs/ and gitops/ into /home/vagrant via synced_folder,
# and 31 scripts hardcode that layout (/home/vagrant/.kube/config-local,
# /home/vagrant/scripts/common/lib.sh, /home/vagrant/configs/gitops/...). Rather than
# parameterize all of them — and risk the working Vagrant path — this reproduces the
# same layout on the cloud nodes, where the login user is ubuntu and nothing is mounted.
#
# Nodes have no public IP, so everything goes through the bastion with ProxyJump.
# Transfers use tar over ssh: rsync is not guaranteed on the base image.
#
# Usage (from the repo root, with the Kakao infra applied):
#   ./scripts/cloud/stage-kakao-nodes.sh              # all masters + workers
#   ./scripts/cloud/stage-kakao-nodes.sh 172.16.0.10  # one node
set -euo pipefail

# macOS bsdtar writes an AppleDouble sidecar (._<name>) for every file carrying
# extended attributes, and those are binary. A vendored chart shipped that way gives
# ArgoCD charts/kong/templates/.__helpers.tpl next to _helpers.tpl, and helm template
# dies with "error converting YAML to JSON: yaml: control characters are not allowed"
# — the app never renders. Vagrant never hits this because it mounts, not tars.
export COPYFILE_DISABLE=1

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
# Where the scripts think they live. Deliberately /home/vagrant even though the login
# user is ubuntu — the path is the contract, not the account.
STAGE_DIR="${STAGE_DIR:-/home/vagrant}"

cd "$(dirname "$0")/../.."

if [ ! -d "${TF_DIR}" ]; then
  echo "ERROR: ${TF_DIR} not found — run this from the narwhal repo root" >&2
  exit 1
fi

echo "=== Reading Kakao Cloud endpoints from OpenTofu state ==="
BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
# tofu emits an absolute path; the fallback is relative to TF_DIR, so normalize
# either form against the repo root rather than trusting the caller's CWD.
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY} - has tofu apply finished?" >&2; exit 1; }

if [ $# -gt 0 ]; then
  NODES=("$@")
else
  # One subshell, one cd — masters then workers, each a JSON array on its own line.
  node_json=$(cd "${TF_DIR}" && tofu output -json master_private_ips && tofu output -json worker_private_ips)
  # read loop, not mapfile: macOS ships bash 3.2 and mapfile arrived in 4.0.
  NODES=()
  while IFS= read -r _ip; do
    [ -n "${_ip}" ] && NODES+=("${_ip}")
  done < <(printf '%s\n' "${node_json}" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if line:
        for ip in json.loads(line):
            print(ip)
')
fi

echo "  bastion : ${BASTION_IP}"
echo "  key     : ${SSH_KEY}"
echo "  nodes   : ${NODES[*]}"

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

# tar over ssh: src dir on the host -> dest dir on the node. Owned by the login user
# so the scripts can write (kubeconfig, join files) without sudo.
push_dir() {
  local ip="$1" src="$2" dest="$3"
  ssh_node "${ip}" "sudo mkdir -p '${dest}' && sudo chown -R ${SSH_USER}:${SSH_USER} '$(dirname "${dest}")'"
  tar czf - -C "$(dirname "${src}")" "$(basename "${src}")" \
    | ssh_node "${ip}" "tar xzf - -C '$(dirname "${dest}")'"
}

# Ubuntu cloud images ship unattended-upgrades enabled and apt-daily-upgrade.timer
# fires within ~20 minutes of first boot. The provisioning scripts assume exclusive
# dpkg access, so on 2026-07-26 it held /var/lib/dpkg/lock-frontend and killed
# 02-containerd.sh on two nodes; on the bastion the same run upgraded squid and
# restarted it, which is what made apt fail with "Connection refused" on all six.
# The pre-baked Vagrant box never hits either, which is why nothing guarded it.
quiesce_apt() {
  local ip="$1"
  ssh_node "${ip}" "
    sudo systemctl disable --now unattended-upgrades.service 2>/dev/null || true
    sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
    sudo systemctl stop apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
    # Whatever is mid-transaction still has to finish before we can install.
    for _ in \$(seq 1 60); do
      sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
      sleep 5
    done
    sudo dpkg --configure -a 2>/dev/null || true
    echo \"    apt quiesced (unattended-upgrades: \$(systemctl is-enabled unattended-upgrades.service 2>/dev/null || echo removed))\"
  "
}

# The provisioning scripts assume a vagrant user exists, not just the /home/vagrant
# path: 02-init-cluster.sh chowns the kubeconfig to vagrant:vagrant and dies with
# "chown: invalid user: 'vagrant:vagrant'" on a cloud image. Creating the account is
# the same call as reproducing the path — it satisfies every such assumption at once,
# including any not found by grep, and leaves the Vagrant path untouched.
#
# ubuntu joins the group and the tree stays group-writable so staging, which runs as
# ubuntu over ssh, keeps working after the directory changes owner.
ensure_vagrant_user() {
  local ip="$1"
  ssh_node "${ip}" "
    id vagrant >/dev/null 2>&1 || sudo useradd --home-dir '${STAGE_DIR}' --shell /bin/bash vagrant
    sudo usermod -aG vagrant ${SSH_USER}
    sudo mkdir -p '${STAGE_DIR}'
    sudo chown -R vagrant:vagrant '${STAGE_DIR}'
    sudo chmod -R g+w '${STAGE_DIR}'
    echo \"    vagrant user: uid=\$(id -u vagrant) home=\$(getent passwd vagrant | cut -d: -f6)\"
  "
}

for ip in "${NODES[@]}"; do
  echo ""
  echo "=== Staging ${ip} ==="
  quiesce_apt "${ip}"
  ensure_vagrant_user "${ip}"
  ssh_node "${ip}" "sudo mkdir -p '${STAGE_DIR}' && sudo chown ${SSH_USER}:${SSH_USER} '${STAGE_DIR}'"

  # Mirrors the Vagrantfile synced_folder contract exactly:
  #   scripts/ -> /home/vagrant/scripts
  #   configs/ -> /home/vagrant/configs
  #   gitops/  -> /home/vagrant/configs/gitops
  push_dir "${ip}" "scripts" "${STAGE_DIR}/scripts"
  push_dir "${ip}" "configs" "${STAGE_DIR}/configs"
  # gitops/ nests inside configs/ on the node, matching the third synced_folder.
  tar czf - -C . gitops | ssh_node "${ip}" "tar xzf - -C '${STAGE_DIR}/configs'"

  # Belt and braces: COPYFILE_DISABLE covers bsdtar, this covers anything already
  # there from an earlier run.
  ssh_node "${ip}" "find '${STAGE_DIR}/scripts' '${STAGE_DIR}/configs' -name '._*' -delete 2>/dev/null || true"
  ssh_node "${ip}" "chmod -R +x '${STAGE_DIR}/scripts' 2>/dev/null || true; ls -d ${STAGE_DIR}/scripts ${STAGE_DIR}/configs ${STAGE_DIR}/configs/gitops"
done

echo ""
echo "=== Staged $((${#NODES[@]})) node(s) ==="
echo "Next: run the provisioning scripts on each node with PROVIDER=kakao, e.g."
echo "  ssh -i ${SSH_KEY} -J ${SSH_USER}@${BASTION_IP} ${SSH_USER}@${NODES[0]}"
echo "  sudo -E PROVIDER=kakao ${STAGE_DIR}/scripts/common/01-prerequisites.sh"
