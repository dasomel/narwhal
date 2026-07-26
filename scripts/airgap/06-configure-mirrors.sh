#!/bin/bash
set -euo pipefail

# =============================================================================
# 06-configure-mirrors.sh — Configure containerd to use airgap registry as mirror
#
# Generates hosts.toml entries for each public registry:
#   /etc/containerd/certs.d/<upstream>/hosts.toml
#     server = https://<upstream>
#     [host."http://<AIRGAP_REGISTRY>/<upstream>"]
#       capabilities = ["pull", "resolve"]
#       skip_verify = true
#
# This lets images be pulled by their ORIGINAL refs (registry.k8s.io/pause:3.10)
# without modifying any YAML/Helm values — containerd rewrites to mirror.
#
# Run this on every node. If invoked on master-1, applies to all nodes via ssh.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

REG="${AIRGAP_REGISTRY}"
REG_SCHEME="${AIRGAP_REGISTRY_SCHEME:-http}"

# shellcheck disable=SC2120  # sudo_cmd is an optional arg with a default; callers may omit it
configure_node() {
  local sudo_cmd="${1:-sudo}"

  # Ensure containerd has config_path enabled
  ${sudo_cmd} grep -q '^\s*config_path = "/etc/containerd/certs.d"' /etc/containerd/config.toml 2>/dev/null || \
    ${sudo_cmd} sed -i 's|config_path = ""|config_path = "/etc/containerd/certs.d"|' /etc/containerd/config.toml

  for upstream in registry.k8s.io quay.io ghcr.io docker.io cr.fluentbit.io; do
    dir="/etc/containerd/certs.d/${upstream}"
    ${sudo_cmd} mkdir -p "${dir}"
    ${sudo_cmd} tee "${dir}/hosts.toml" > /dev/null <<TOMLEOF
# Airgap mirror for ${upstream}
server = "https://${upstream}"

[host."${REG_SCHEME}://${REG}/${upstream}"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
  override_path = true
TOMLEOF
  done

  # docker.io special case: default namespace is library/, but pullthrough cache
  # may store under docker.io/library/*
  ${sudo_cmd} mkdir -p /etc/containerd/certs.d/registry-1.docker.io
  ${sudo_cmd} tee /etc/containerd/certs.d/registry-1.docker.io/hosts.toml > /dev/null <<TOMLEOF
server = "https://registry-1.docker.io"

[host."${REG_SCHEME}://${REG}/docker.io"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
  override_path = true
TOMLEOF

  ${sudo_cmd} systemctl restart containerd
}

# Login user on the other nodes. Vagrant boxes use vagrant; cloud images use the
# distro default (ubuntu on Kakao Cloud), so PROVIDER picks it unless overridden.
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  NODE_SSH_USER="${NODE_SSH_USER:-ubuntu}"
else
  NODE_SSH_USER="${NODE_SSH_USER:-vagrant}"
fi

# If we're on master-1 (has kubectl + node SSH keys), propagate to all nodes
if command -v kubectl >/dev/null 2>&1 && kubectl get nodes >/dev/null 2>&1; then
  echo "Configuring all cluster nodes..."
  NODES=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}')
  for ip in ${NODES}; do
    echo "  → ${ip}"
    # Use scp of this script + remote execute
    tmpfile=$(mktemp)
    declare -f configure_node > "${tmpfile}"
    cat >> "${tmpfile}" <<EOF
AIRGAP_REGISTRY="${REG}"
AIRGAP_REGISTRY_SCHEME="${REG_SCHEME}"
REG="${REG}"
REG_SCHEME="${REG_SCHEME}"
configure_node sudo
EOF
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "${NODE_SSH_USER}@${ip}" "bash -s" < "${tmpfile}" || echo "  WARN: failed on ${ip}"
    rm -f "${tmpfile}"
  done
else
  echo "Configuring local node only..."
  configure_node
fi

echo ""
echo "Mirror configured. Test pull:"
echo "  sudo crictl pull registry.k8s.io/pause:3.10"
