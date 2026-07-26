#!/bin/bash
set -euo pipefail

# Repo default, NOT a 1.7.* pin. containerd 1.7.12 ships an AppArmor profile that
# predates runc 1.3, and on Ubuntu 24.04 (runc 1.3.4) the kernel then denies runc the
# right to signal its own container init:
#
#   apparmor="DENIED" operation="signal" profile="cri-containerd.apparmor.d"
#     comm="runc" requested_mask="receive" signal=kill peer="runc"
#
# Containers cannot be killed, so pods wedge in Terminating and a database that is
# asked to stop never does. On 2026-07-26 this took out the CNPG cluster mid-Liquibase
# and blocked Phase 2 at 11-2-keycloak-config. It never showed on Vagrant because the
# 26.04 box has no 1.7.x candidate and always landed on containerd 2.x.
#
# 24.04 offers 2.2.1 and 26.04 offers 2.x, so the default is right on both. Set
# CONTAINERD_VERSION explicitly only to reproduce an old environment.
CONTAINERD_VERSION="${CONTAINERD_VERSION:-}"

echo "=== containerd ${CONTAINERD_VERSION:-<repo default>} Installation ==="

sudo apt-get update
if [ -n "${CONTAINERD_VERSION}" ]; then
  if ! sudo apt-get install -y containerd="${CONTAINERD_VERSION}"; then
    echo "containerd=${CONTAINERD_VERSION} not available in repo; installing distro default containerd..."
    sudo apt-get install -y containerd
  fi
else
  sudo apt-get install -y containerd
fi

installed_ctr="$(dpkg-query -W -f='${Version}' containerd 2>/dev/null || echo unknown)"
echo "containerd installed: ${installed_ctr}"
case "${installed_ctr}" in
  1.7.*) echo "WARN: containerd 1.7.x + runc 1.3 denies signal delivery under AppArmor;" \
              "pods will wedge in Terminating. Upgrade to 2.x." >&2 ;;
esac

# Configure containerd
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null

# Enable SystemdCgroup (required for K8s)
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml

# Update sandbox_image
sudo sed -i 's|sandbox_image = ".*"|sandbox_image = "registry.k8s.io/pause:3.10"|' /etc/containerd/config.toml

# D3: Enable registry config_path so containerd reads per-registry certs.d entries.
# Required for harbor.${DOMAIN} (behind APISIX, private CA) without global insecure_registries.
# Root cause: containerd 2.x (Ubuntu 26.04) uses plugin key 'io.containerd.cri.v1.images'
# and emits config_path = '' (single-quoted), while 1.7 used "io.containerd.grpc.v1.cri"
# with double-quoted empty string. The old grep/sed matched neither. Fix: use a Python
# in-place rewrite that handles both quote styles and both plugin namespaces, and covers
# ALL config_path='' occurrences (line 54 under cri.v1.images.registry and line 245
# under grpc.v1.cri.registry in the 2.x default config).
sudo python3 - /etc/containerd/config.toml <<'PYEOF'
import re, sys
path = sys.argv[1]
text = open(path).read()
# Replace any config_path = '' or config_path = "" (both quote styles, arbitrary whitespace)
# Only target empty values; leave already-set paths alone.
new_text = re.sub(
    r'(config_path\s*=\s*)["\']["\']',
    r'\1"/etc/containerd/certs.d"',
    text
)
if new_text == text:
    # config_path line absent entirely — insert under the registry sub-section header
    # (matches both 1.7 grpc.v1.cri and 2.x cri.v1.images registry headers)
    new_text = re.sub(
        r'(\[plugins\.[\'"]io\.containerd\.(grpc\.v1\.cri|cri\.v1\.images)[\'"]\.registry\])',
        r'\1\n    config_path = "/etc/containerd/certs.d"',
        new_text
    )
    if new_text == text:
        print("containerd: WARNING — could not locate registry section to set config_path", file=sys.stderr)
        sys.exit(1)
    print("containerd: inserted config_path under registry section")
else:
    print("containerd: config_path set to /etc/containerd/certs.d")
open(path, 'w').write(new_text)
PYEOF

# Create certs.d entry for harbor.${DOMAIN}.
# Internal cluster registry behind APISIX — skip TLS verify (no node CA trust
# needed; this is the verified pull path for harbor.${DOMAIN}).
DOMAIN="${DOMAIN:-local.narwhal.internal}"
HARBOR_CERTS_DIR="/etc/containerd/certs.d/harbor.${DOMAIN}"
sudo mkdir -p "${HARBOR_CERTS_DIR}"
if [ ! -f "${HARBOR_CERTS_DIR}/hosts.toml" ]; then
  sudo tee "${HARBOR_CERTS_DIR}/hosts.toml" > /dev/null <<HTOML
server = "https://harbor.${DOMAIN}"

[host."https://harbor.${DOMAIN}"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
HTOML
  echo "containerd: created ${HARBOR_CERTS_DIR}/hosts.toml"
else
  echo "containerd: ${HARBOR_CERTS_DIR}/hosts.toml already exists, skipping"
fi

# Configure service limits
sudo mkdir -p /etc/systemd/system/containerd.service.d
cat <<EOF | sudo tee /etc/systemd/system/containerd.service.d/limits.conf
[Service]
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Restart=always
RestartSec=5
EOF

# Restart service
sudo systemctl daemon-reload
sudo systemctl restart containerd
sudo systemctl enable containerd

echo "=== containerd Done ==="
