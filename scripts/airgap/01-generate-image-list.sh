#!/bin/bash
set -euo pipefail

# =============================================================================
# 01-generate-image-list.sh — Scans source to produce images.txt
#
# Sources scanned:
#   - VERSIONS.md (authoritative version table)
#   - scripts/**/*.sh (image references in kubectl/helm commands)
#   - gitops/apps/*.yaml (Helm values image.repository + image.tag)
#   - gitops/resources/*.yaml (container spec image fields)
#   - Vagrantfile (K8s/containerd versions → derive kubeadm image list)
#
# Output format: one image reference per line (registry/repo:tag)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_FILE="${1:-/dev/stdout}"

tmp=$(mktemp)
trap 'rm -f "${tmp}"' EXIT

# 1) Images embedded as `image: <ref>` in YAML manifests
grep -rhE --exclude-dir=bak --exclude=images.txt '^[[:space:]]*image:[[:space:]]+' \
  "${PROJECT_ROOT}/gitops" \
  "${PROJECT_ROOT}/scripts" \
  2>/dev/null \
  | sed -E 's/^[[:space:]]*image:[[:space:]]+//; s/["'"'"']//g; s/[[:space:]]+#.*$//' \
  | grep -E '^[a-z0-9][a-z0-9._/-]+:[A-Za-z0-9._-]+$' \
  >> "${tmp}" || true

# 2) Images from `repository:` + `tag:` pairs in Helm values blocks
python3 - "${PROJECT_ROOT}/gitops" >> "${tmp}" <<'PYEOF' || true
import os, re, sys
root = sys.argv[1]
for dirpath, _, files in os.walk(root):
    for f in files:
        if not f.endswith(('.yaml', '.yml')):
            continue
        p = os.path.join(dirpath, f)
        try:
            txt = open(p).read()
        except Exception:
            continue
        # Match `repository: X` followed within 4 lines by `tag: Y`
        for m in re.finditer(r'repository:\s*([^\s#]+).{0,200}?tag:\s*["\']?([^"\'\s#]+)', txt, re.DOTALL):
            repo, tag = m.group(1).strip(), m.group(2).strip()
            if repo and tag and not repo.startswith('{{'):
                print(f"{repo}:{tag}")
PYEOF

# 3) Images from Vagrantfile K8s_VERSION → kubeadm-managed images
K8S_VER=$(grep -E '^K8S_PATCH_VERSION\s*=' "${PROJECT_ROOT}/Vagrantfile" | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || echo "1.35.4")
cat >> "${tmp}" <<KUBE_IMAGES
registry.k8s.io/kube-apiserver:v${K8S_VER}
registry.k8s.io/kube-controller-manager:v${K8S_VER}
registry.k8s.io/kube-scheduler:v${K8S_VER}
registry.k8s.io/kube-proxy:v${K8S_VER}
registry.k8s.io/pause:3.10
registry.k8s.io/etcd:3.5.21-0
registry.k8s.io/coredns/coredns:v1.12.0
KUBE_IMAGES

# 4) Images from scripts/cluster/*.sh helm install/upgrade commands with --set image.tag=
grep -rhE --exclude-dir=bak '\-\-set.*image\.' "${PROJECT_ROOT}/scripts" 2>/dev/null \
  | grep -oE '[a-z0-9][a-z0-9._/-]+:[A-Za-z0-9._-]+' \
  | grep -E '^[a-z0-9]' >> "${tmp}" || true

# Deduplicate, sort, and filter out obvious false-positives (like "runtime: systemd")
sort -u "${tmp}" \
  | grep -vE '^(runtime|cgroupDriver|tag|image|name):' \
  | grep -vE '^(quay\.io|docker\.io|ghcr\.io|registry\.k8s\.io|cr\.fluentbit\.io)/?$' \
  | grep -E '^[a-z0-9][a-z0-9._/-]+(/[a-z0-9._-]+)*:[A-Za-z0-9._+-]+$' \
  > "${OUT_FILE}"

count=$(wc -l < "${OUT_FILE}")
echo "Collected ${count} unique image refs" >&2
