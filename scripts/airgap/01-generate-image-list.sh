#!/bin/bash
set -euo pipefail

# =============================================================================
# 01-generate-image-list.sh — Produce the airgap image list (images.txt)
#
# TWO MODES:
#
#   --live [OUT]   RECOMMENDED. Extract the ACTUAL image set from a running,
#                  fully-provisioned cluster (pod snapshot) and union the
#                  transient job/init images that don't appear in a point-in-time
#                  snapshot (kaniko, alpine/git, alpine/k8s, velero-plugin), plus
#                  `kubeadm config images list` — pause and kube-proxy are invisible
#                  to a pod snapshot and the control-plane tags must match the
#                  kubeadm the nodes actually run. This
#                  is the only mode that captures Helm CHART-DEFAULT images —
#                  Cilium, Alloy, Loki, Tempo, Prometheus stack, Istio, Kyverno,
#                  cert-manager, MetalLB, ArgoCD, Keycloak, CNPG, etc. — which the
#                  static source-scan below cannot see (they have no explicit
#                  `image:` ref in our gitops). Static mode undercounts by ~60.
#
#   (default)      STATIC source-scan (legacy). Scans gitops/ + scripts/ for
#                  explicit `image:` refs, Helm repository+tag pairs, kubeadm
#                  images from the Vagrantfile, and `--set image.*` flags. Kept
#                  as an offline sanity cross-check ONLY — it is NOT complete;
#                  never ship its output as the airgap bundle list.
#
# Output format: one image reference per line (registry/repo:tag); `#` comments
# and blank lines are ignored by the consumer scripts (02-save-images.sh).
#
# Usage:
#   scripts/airgap/01-generate-image-list.sh --live images.txt   # regenerate for real
#   scripts/airgap/01-generate-image-list.sh images.txt          # static cross-check
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODE="static"
if [[ "${1:-}" == "--live" ]]; then
  MODE="live"
  shift
fi
OUT_FILE="${1:-/dev/stdout}"

# --- Transient job/init images: run one-off (Jobs, init containers, build) so a
#     point-in-time pod snapshot may miss them, but a from-scratch airgap install
#     needs them. Keep this list in sync with the build/hook flows. -------------
TRANSIENT_IMAGES=$(cat <<'EOF'
gcr.io/kaniko-project/executor:latest
docker.io/alpine/git:latest
docker.io/alpine/k8s:1.31.4
velero/velero-plugin-for-aws:v1.14.1
mirror.gcr.io/aquasec/trivy:0.60.0
EOF
)
# NOTE: the trivy scanner (aquasec/trivy) is spawned by trivy-operator as a
# short-lived VulnerabilityReport scan Job, so it only appears in a live pod
# snapshot while a scan is running — pinned here so it's captured deterministically.
# Bump the tag when trivy-operator (which selects it) is upgraded.

# In-cluster-built image: produced by Kaniko from source pushed to Gitea, NOT
# pulled from any upstream registry — must never be in a pull-based mirror list.
INCLUSTER_BUILT_RE='harbor\.local\.narwhal\.internal/library/narwhal-portal'

emit_header() {
  cat <<HEADER
# Airgap image list — narwhal IDP cluster
#
# GROUND TRUTH: images actually running in a fully-provisioned cluster (kubectl
# pod snapshot) UNION the transient job/init images (kaniko executor, alpine/git
# for the portal build, alpine/k8s for velero/CSI hook jobs, velero-plugin-for-aws).
# Regenerate with:  scripts/airgap/01-generate-image-list.sh --live images.txt
# (static source-scan mode cannot see chart-default images and undercounts by ~60).
#
# EXCLUDED intentionally:
#   - harbor.local.narwhal.internal/library/narwhal-portal:latest — built IN-CLUSTER
#     by Kaniko; produced, not pulled, so it must not be in a pull-based bundle.
#
# :latest TAGS — deliberate vs. hardening:
#   - ghcr.io/dasomel/goharbor/* :latest is INTENTIONAL — the custom multi-arch
#     Harbor rebuild is republished to :latest, and we want new builds picked up
#     automatically. Do NOT digest-pin these (it defeats that).
#   - gcr.io/kaniko-project/executor:latest and docker.io/alpine/git:latest are
#     third-party build helpers; digest-pin them if you need a fully reproducible
#     hardened airgap bundle. Left at :latest here to match the deploy manifests.
#
# Last regenerated (--live): ${LIVE_STAMP:-unknown}
HEADER
  echo ""
}

# --- kubectl runner: try host kubectl, fall back to vagrant ssh master-1 --------
kubectl_live() {
  if kubectl get nodes >/dev/null 2>&1; then
    kubectl "$@"
  else
    ( cd "${PROJECT_ROOT}" && vagrant ssh master-1 -c "kubectl $*" 2>/dev/null )
  fi
}

# The control-plane image set, straight from the tool that pulls it. A pod snapshot
# cannot produce this: pause is the sandbox image and never appears as a pod
# container, and kube-proxy never runs at all because Cilium replaces it. Both were
# absent from the bundle, and the three apiserver/controller-manager/scheduler refs
# it did contain were a patch release behind whatever kubeadm the nodes installed —
# so on Kakao Cloud 5 of the 7 refs kubeadm wanted were missing and a real airgap
# install would have died at kubeadm init.
kubeadm_live() {
  if kubeadm config images list >/dev/null 2>&1; then
    kubeadm config images list 2>/dev/null
  else
    ( cd "${PROJECT_ROOT}" && vagrant ssh master-1 -c "kubeadm config images list" 2>/dev/null | tr -d '\r' )
  fi
}

if [[ "${MODE}" == "live" ]]; then
  echo "Extracting image set from the running cluster..." >&2
  live=$(kubectl_live get pods -A -o jsonpath='{range .items[*]}{range .spec.initContainers[*]}{.image}{"\n"}{end}{range .spec.containers[*]}{.image}{"\n"}{end}{end}')
  if [[ -z "${live}" ]]; then
    echo "ERROR: could not read images from the cluster (host kubectl and 'vagrant ssh master-1' both failed)." >&2
    echo "       Run this from the repo root with a reachable cluster." >&2
    exit 1
  fi
  kubeadm_imgs=$(kubeadm_live | grep -E '^[a-z0-9.-]+/' || true)
  if [[ -z "${kubeadm_imgs}" ]]; then
    echo "ERROR: could not read 'kubeadm config images list'." >&2
    echo "       Without it the bundle silently omits pause and kube-proxy and pins the" >&2
    echo "       control plane to whatever was running, not what kubeadm will ask for." >&2
    exit 1
  fi
  echo "  kubeadm control-plane images: $(printf '%s\n' "${kubeadm_imgs}" | wc -l | tr -d ' ')" >&2

  LIVE_STAMP="$(date +%Y-%m-%d) (from live cluster)"
  {
    emit_header
    { printf '%s\n' "${live}"; printf '%s\n' "${TRANSIENT_IMAGES}"; printf '%s\n' "${kubeadm_imgs}"; } \
      | sed -E 's/@sha256:.*//' \
      | grep -vE "${INCLUSTER_BUILT_RE}" \
      | grep -vE '^[[:space:]]*$' \
      | sort -u
  } > "${OUT_FILE}"
  count=$(grep -cvE '^#|^[[:space:]]*$' "${OUT_FILE}")
  echo "Collected ${count} image refs (live + transient)" >&2
  exit 0
fi

# =============================== STATIC MODE ==================================
# (legacy source-scan — incomplete, cross-check only)
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
echo "Collected ${count} unique image refs (STATIC scan — INCOMPLETE, cross-check only; use --live for the real list)" >&2
