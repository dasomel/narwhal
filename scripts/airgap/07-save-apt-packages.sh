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
# apt has to resolve dependencies as the target architecture and release, which a Mac
# cannot do natively. Two ways to get one:
#
#   --docker  (default) run it in a container of the target platform. Nothing outside this
#             machine is involved, so the bundle can be built with no cluster online and
#             the same command works in CI. This is the one to reach for.
#   --ssh     drive a live node instead. Kept because it resolves against exactly the
#             sources the node has, which is the tie-breaker if a mirror ever differs, and
#             because it is how the arm64 bundle was built.
#
# Both collect /var/cache/apt/archives and stream the result back into the bundle.
#
# Output: ${AIRGAP_BUNDLE_DIR}/apt/  — flat .deb dir plus a Packages.gz index, consumable
# as `deb [trusted=yes] file:///srv/airgap/apt ./` with no HTTP server involved.
#
# Usage:
#   AIRGAP_ARCH=linux/amd64 ./scripts/airgap/07-save-apt-packages.sh
#   ./scripts/airgap/07-save-apt-packages.sh --docker --platform linux/amd64
#   ./scripts/airgap/07-save-apt-packages.sh --ssh "ssh -i KEY vagrant@1.2.3.4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/00-config.sh"

SSH_CMD=""
MODE="docker"
# 00-config.sh sets AIRGAP_ARCH in docker's `linux/arch` form, which is exactly what
# --platform wants, so the bundle's architecture and the container's cannot drift.
PLATFORM="${AIRGAP_ARCH:-linux/amd64}"
case "${PLATFORM}" in */*) ;; *) PLATFORM="linux/${PLATFORM}" ;; esac
# Must match the nodes' release: package versions are per-release, and a bundle built on a
# different one installs but pulls in a second libc.
BUILDER_IMAGE="${AIRGAP_BUILDER_IMAGE:-ubuntu:24.04}"
OUT="${AIRGAP_BUNDLE_DIR}/apt"
K8S_PATCH="${K8S_PATCH_VERSION:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh)  SSH_CMD="$2"; MODE="ssh"; shift 2 ;;
    --docker) MODE="docker"; shift ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    --image) BUILDER_IMAGE="$2"; shift 2 ;;
    --out)  OUT="$2"; shift 2 ;;
    --k8s-version) K8S_PATCH="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ "${MODE}" = "ssh" ] && [ -z "${SSH_CMD}" ]; then
  echo "ERROR: --ssh needs a command that takes a shell script on stdin." >&2
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
if [ "${MODE}" = "docker" ]; then
  echo "  Builder:    ${BUILDER_IMAGE} on ${PLATFORM}"
else
  echo "  Builder:    ${SSH_CMD}"
fi

mkdir -p "${OUT}"

# A node runs the script as an unprivileged user and a container runs it as root, where
# sudo is usually not even installed.
SUDO="sudo"
# A provisioned node already has pkgs.k8s.io configured by 03-k8s-install.sh; a stock
# container has neither that source nor the tools to fetch it, so add both. Doing it here
# rather than in the shared body keeps the ssh path byte-identical to what built arm64.
PREP=""
if [ "${MODE}" = "docker" ]; then
  SUDO=""
  PREP=$(cat <<PREPEOF
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg dpkg-dev >/dev/null
install -d -m 0755 /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_PATCH%.*}/deb/Release.key" \
  | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${K8S_PATCH%.*}/deb/ /" \
  > /etc/apt/sources.list.d/kubernetes.list
# Refresh before the body's apt-cache madison: on a node 03-k8s-install.sh added this
# source and updated long ago, so the body assumes an index that already knows kubelet.
apt-get update -qq
PREPEOF
)
fi

# The build half. Downloads only — nothing is installed or removed on the node.
#   --reinstall so already-installed packages are fetched again instead of skipped, which
#     is the normal state on a node that has been provisioned.
#   dpkg-scanpackages builds the index; it lives in dpkg-dev, installed here if missing.
REMOTE_SCRIPT=$(cat <<REMOTE
set -euo pipefail
${PREP}
K8S_APT_VERSION="\$(apt-cache madison kubelet 2>/dev/null | awk -v v="${K8S_PATCH}-" '\$3 ~ "^"v && !seen {print \$3; seen=1}')"
if [ -z "\${K8S_APT_VERSION}" ]; then
  echo "ERROR: kubelet ${K8S_PATCH} not offered by this node's APT sources." >&2
  exit 1
fi
echo "  resolved kubelet/kubeadm/kubectl -> \${K8S_APT_VERSION}"

${SUDO} rm -rf /tmp/narwhal-apt && mkdir -p /tmp/narwhal-apt
${SUDO} apt-get update -qq
command -v dpkg-scanpackages >/dev/null 2>&1 || ${SUDO} apt-get install -y -qq dpkg-dev

${SUDO} apt-get clean
${SUDO} apt-get install -y --download-only --reinstall \
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
  ${SUDO} apt-get install -y --download-only --reinstall "\$p" >/dev/null 2>&1 || true
done

${SUDO} cp /var/cache/apt/archives/*.deb /tmp/narwhal-apt/ 2>/dev/null || true
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
TMP_TAR=$(mktemp)
CID=""
cleanup() {
  [ -n "${CID}" ] && docker rm -f "${CID}" >/dev/null 2>&1
  rm -f "${TMP_TAR}"
  return 0
}
trap cleanup EXIT

if [ "${MODE}" = "docker" ]; then
  command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker is required for --docker mode (or use --ssh against a node)." >&2
    exit 1
  }
  # The docker CLI resolves registry credentials before it pulls anything, and a broken
  # `credsStore` makes it wait forever with no output whatsoever — not an error, not a
  # timeout, just silence that reads as a slow pull. On this host the config named
  # "desktop" and `docker-credential-desktop` was on PATH but never answered, Docker
  # Desktop not being installed. So presence of the helper is not the test, and there is
  # no cheap test that cannot itself hang.
  #
  # Sidestep it: the builder image is public, so no credentials are needed at all. Set
  # AIRGAP_KEEP_DOCKER_CONFIG=1 if the image lives in a private registry.
  if [ "${AIRGAP_KEEP_DOCKER_CONFIG:-0}" != "1" ]; then
    DOCKER_CONFIG=$(mktemp -d)
    printf '{}' > "${DOCKER_CONFIG}/config.json"
    export DOCKER_CONFIG
  fi

  echo "  downloading in a ${PLATFORM} container (this takes a few minutes)..."
  # Created and started rather than `run --rm`, because the archive has to be copied out
  # after the process exits — the same separation the ssh path needs, for the same reason.
  CID=$(docker create --platform "${PLATFORM}" "${BUILDER_IMAGE}" sleep infinity)
  docker start "${CID}" >/dev/null
  if ! docker exec -i "${CID}" bash -s <<<"${REMOTE_SCRIPT}" 2>&1 | sed 's/^/  /'; then
    echo "ERROR: download in the container failed (see output above)." >&2
    exit 1
  fi
  if ! docker cp "${CID}:/tmp/narwhal-apt.tgz" "${TMP_TAR}" 2>/dev/null; then
    echo "ERROR: could not copy /tmp/narwhal-apt.tgz out of the container." >&2
    exit 1
  fi
else
  echo "  downloading on the node (this takes a few minutes)..."
  if ! ${SSH_CMD} 'bash -s' <<<"${REMOTE_SCRIPT}" 2>&1 | sed 's/^/  /'; then
    echo "ERROR: remote download failed (see output above)." >&2
    exit 1
  fi
  if ! ${SSH_CMD} 'cat /tmp/narwhal-apt.tgz' > "${TMP_TAR}" 2>/dev/null; then
    echo "ERROR: could not retrieve /tmp/narwhal-apt.tgz from the node." >&2
    exit 1
  fi
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
