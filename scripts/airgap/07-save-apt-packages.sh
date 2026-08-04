#!/bin/bash
set -euo pipefail

# Collect the .deb packages the provisioning scripts install, into the airgap bundle.
#
# The bundle has always carried container images and (since 0da0d87) Helm charts, but the
# install still reached the internet for its OS packages: 03-k8s-install.sh adds
# pkgs.k8s.io as an APT source and pulls kubelet/kubeadm/kubectl from it, and
# 01-prerequisites.sh / 02-containerd.sh pull chrony, nfs-common, jq and containerd from
# the Ubuntu archive. On a genuinely isolated network the install dies at the first
# apt-get, long before any of the mirrored images matter.
#
# The download has to happen on a machine of the target architecture and release — a Mac
# cannot resolve arm64 Ubuntu dependencies — so this drives a node over SSH, collects
# /var/cache/apt/archives there, and streams the result back into the bundle. The node
# needs working internet for the duration; run this while the cluster is still online, or
# against any spare VM of the same box.
#
# Output: ${AIRGAP_BUNDLE_DIR}/apt/  — flat .deb dir plus a Packages.gz index, consumable
# as `deb [trusted=yes] file:///srv/airgap/apt ./` with no HTTP server involved.
#
# Usage:
#   ./scripts/airgap/07-save-apt-packages.sh --ssh "ssh -i KEY vagrant@1.2.3.4"
#   ./scripts/airgap/07-save-apt-packages.sh --ssh "vagrant ssh master-1 -c"   # not supported: needs stdin

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/00-config.sh"

SSH_CMD=""
OUT="${AIRGAP_BUNDLE_DIR}/apt"
K8S_PATCH="${K8S_PATCH_VERSION:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh)  SSH_CMD="$2"; shift 2 ;;
    --out)  OUT="$2"; shift 2 ;;
    --k8s-version) K8S_PATCH="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ -z "${SSH_CMD}" ]; then
  echo "ERROR: --ssh is required (a command that takes a shell script on stdin)." >&2
  echo "       e.g. --ssh \"ssh -i .vagrant/machines/master-1/vmware_desktop/private_key vagrant@172.16.221.139\"" >&2
  exit 1
fi

# Default to the version the Vagrantfile pins, so the bundle and the install agree. Reading
# it here rather than hardcoding keeps the two from drifting the way they did before
# 6e2c108, when the bundle was built for 1.35.5 while every node ran 1.35.7.
if [ -z "${K8S_PATCH}" ]; then
  K8S_PATCH=$(grep -E '^K8S_PATCH_VERSION\s*=' "${SCRIPT_DIR}/../../Vagrantfile" 2>/dev/null \
    | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
fi
[ -n "${K8S_PATCH}" ] || { echo "ERROR: could not determine K8S_PATCH_VERSION." >&2; exit 1; }

# Every package the provisioning path installs, minus kubelet/kubeadm/kubectl (fetched
# below at a pinned version). The first bundle listed only what 01-prerequisites.sh
# installs, because that is the script the airgap work started from — dnsmasq (04-addons),
# nfs-kernel-server/quota/quotatool (05-nfs-quota-agent) were all missing, and each one
# would have surfaced a full run later as `has no installation candidate`. The coverage
# check below is what makes this list stay honest.
AIRGAP_APT_PACKAGES="containerd chrony nfs-common jq dnsmasq nfs-kernel-server quota quotatool"

# Fail here rather than 25 minutes into an offline install: scan the provisioning scripts
# for what they actually apt-get, and refuse to build a bundle that does not cover it.
# Three things this scan has to survive, each of which silently dropped a package when it
# did not: `apt-get install` guarded by `dpkg -s x || ...` (so it is not at line start),
# arguments continued across a backslash newline, and hyphenated names — stripping flags
# with a `-[a-z]+` regex turns nfs-common into nfs. Match anywhere on non-comment lines,
# join continuations first, and drop flags by leading `-` rather than by pattern.
missing=""
for pkg in $(cat "${SCRIPT_DIR}/../cluster"/*.sh "${SCRIPT_DIR}/../common"/*.sh 2>/dev/null \
             | grep -v '^[[:space:]]*#' \
             | sed -e ':a' -e '/\\$/N; s/\\\n/ /; ta' \
             | grep -hoE 'apt-get install[^|&;]*' \
             | sed -E 's/.*apt-get install//' | tr -s ' ' '\n' \
             | grep -vE '^-|[$"={}\\]|^$' \
             | grep -E '^[a-z][a-z0-9.+-]*$' | sort -u); do
  case " ${AIRGAP_APT_PACKAGES} kubelet kubeadm kubectl dpkg-dev " in
    *" ${pkg} "*) ;;
    *) missing="${missing}${pkg} " ;;
  esac
done
if [ -n "${missing}" ]; then
  echo "ERROR: the provisioning scripts install packages the bundle would not carry:" >&2
  echo "         ${missing}" >&2
  echo "       Add them to AIRGAP_APT_PACKAGES in this script, then rebuild." >&2
  exit 1
fi

echo "=== Saving APT packages for the airgap bundle ==="
echo "  Kubernetes: ${K8S_PATCH}"
echo "  Packages:   ${AIRGAP_APT_PACKAGES}"
echo "  Output:     ${OUT}"

mkdir -p "${OUT}"

# The remote half. Downloads only — nothing is installed or removed on the node.
#   --reinstall so already-installed packages are fetched again instead of skipped, which
#     is the normal state on a node that has been provisioned.
#   dpkg-scanpackages builds the index; it lives in dpkg-dev, installed here if missing.
REMOTE_SCRIPT=$(cat <<REMOTE
set -euo pipefail
K8S_APT_VERSION="\$(apt-cache madison kubelet 2>/dev/null | awk -v v="${K8S_PATCH}-" '\$3 ~ "^"v && !seen {print \$3; seen=1}')"
if [ -z "\${K8S_APT_VERSION}" ]; then
  echo "ERROR: kubelet ${K8S_PATCH} not offered by this node's APT sources." >&2
  exit 1
fi
echo "  resolved kubelet/kubeadm/kubectl -> \${K8S_APT_VERSION}"

sudo rm -rf /tmp/narwhal-apt && mkdir -p /tmp/narwhal-apt
sudo apt-get update -qq
command -v dpkg-scanpackages >/dev/null 2>&1 || sudo apt-get install -y -qq dpkg-dev

sudo apt-get clean
sudo apt-get install -y --download-only --reinstall \
  "kubelet=\${K8S_APT_VERSION}" "kubeadm=\${K8S_APT_VERSION}" "kubectl=\${K8S_APT_VERSION}" \
  ${AIRGAP_APT_PACKAGES}

# --reinstall does not pull dependencies that are already satisfied, and on a provisioned
# node they all are. Fetch the closure explicitly so an install onto a *fresh* box still
# resolves: without this the bundle only works on a node that was already provisioned,
# which defeats the point.
DEPS=\$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
        --no-breaks --no-replaces --no-enhances \
        kubelet kubeadm kubectl ${AIRGAP_APT_PACKAGES} 2>/dev/null \
        | grep -E '^[a-z0-9]' | sort -u)
for p in \${DEPS}; do
  sudo apt-get install -y --download-only --reinstall "\$p" >/dev/null 2>&1 || true
done

sudo cp /var/cache/apt/archives/*.deb /tmp/narwhal-apt/ 2>/dev/null || true
cd /tmp/narwhal-apt
dpkg-scanpackages . /dev/null 2>/dev/null | gzip -9c > Packages.gz
echo "  packages: \$(ls -1 *.deb 2>/dev/null | wc -l | tr -d ' ')"
tar czf /tmp/narwhal-apt.tgz -C /tmp narwhal-apt
echo "  archive: \$(stat -c %s /tmp/narwhal-apt.tgz) bytes"
REMOTE
)

# Two calls, not one. Streaming the tar out of the same SSH session that runs apt-get put
# the archive on the same stdout as everything apt and debconf print there, and the first
# run died with "tar: Unrecognized archive format" on the resulting mix. Writing the
# archive on the node and fetching it separately makes the transfer independent of every
# other command's stream hygiene.
echo "  downloading on the node (this takes a few minutes)..."
if ! ${SSH_CMD} 'bash -s' <<<"${REMOTE_SCRIPT}" 2>&1 | sed 's/^/  /'; then
  echo "ERROR: remote download failed (see output above)." >&2
  exit 1
fi

TMP_TAR=$(mktemp)
if ! ${SSH_CMD} 'cat /tmp/narwhal-apt.tgz' > "${TMP_TAR}" 2>/dev/null; then
  echo "ERROR: could not retrieve /tmp/narwhal-apt.tgz from the node." >&2
  rm -f "${TMP_TAR}"
  exit 1
fi
if ! tar tzf "${TMP_TAR}" >/dev/null 2>&1; then
  echo "ERROR: retrieved archive is not a valid tarball ($(stat -f %z "${TMP_TAR}" 2>/dev/null || stat -c %s "${TMP_TAR}") bytes)." >&2
  rm -f "${TMP_TAR}"
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}"
tar xzf "${TMP_TAR}" -C "$(dirname "${OUT}")" --strip-components=0
# tar contains narwhal-apt/; move its contents into ${OUT}
if [ -d "$(dirname "${OUT}")/narwhal-apt" ]; then
  mv "$(dirname "${OUT}")/narwhal-apt"/* "${OUT}/"
  rmdir "$(dirname "${OUT}")/narwhal-apt"
fi
rm -f "${TMP_TAR}"

DEB_COUNT=$(find "${OUT}" -name '*.deb' | wc -l | tr -d ' ')
echo "=== Done ==="
echo "  ${DEB_COUNT} .deb into ${OUT}"
echo "  index: $( [ -f "${OUT}/Packages.gz" ] && echo Packages.gz || echo 'MISSING' )"
[ "${DEB_COUNT}" -gt 0 ] || { echo "ERROR: no packages collected." >&2; exit 1; }
