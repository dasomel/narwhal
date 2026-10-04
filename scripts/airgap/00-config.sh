#!/bin/bash
# Shared configuration for airgap scripts. Source this from other 0X-*.sh scripts.
# Override via env vars before sourcing.

# Target private registry URL (hostname:port) — where mirrored images land
: "${AIRGAP_REGISTRY:=registry.airgap.local:5000}"

# Bootstrap registry image. Chicken-and-egg: 04 stands up this registry to HOST
# all other images, so it can't be pulled from the mirror — it is side-loaded
# from a docker-archive tar saved into the bundle by 02 and loaded by 04.
: "${AIRGAP_BOOTSTRAP_REGISTRY_IMAGE:=docker.io/library/registry:2}"

# Target architecture for the bundle. ONE arch per bundle (skopeo --override-arch
# picks a single-arch image out of each multi-arch manifest).
#   - linux/arm64  → local Apple-Silicon Vagrant cluster (default)
#   - linux/amd64  → x86_64 targets, e.g. Kakao Cloud
# Build a second bundle by re-running 02/03/04 with AIRGAP_ARCH=linux/amd64; the
# bundle dir below is arch-suffixed so the two never overwrite each other.
: "${AIRGAP_ARCH:=linux/arm64}"

# Narwhal domain (used for Harbor endpoint resolution in airgap mode)
: "${DOMAIN:=local.narwhal.internal}"

# Bundle output directory — collected OCI layouts + Helm charts + bootstrap tar.
# Arch-suffixed so linux/arm64 and linux/amd64 bundles coexist:
#   narwhal-airgap-bundle-arm64  /  narwhal-airgap-bundle-amd64
: "${AIRGAP_BUNDLE_DIR:=$(pwd)/narwhal-airgap-bundle-${AIRGAP_ARCH##*/}}"

# Skopeo TLS options (disable verification for self-signed registries)
: "${AIRGAP_SKOPEO_DEST_TLS_VERIFY:=false}"

# Known external registries that need mirroring
# Narwhal policy: ghcr > registry.k8s.io > quay > docker.io (CLAUDE.md)
AIRGAP_SOURCE_REGISTRIES=(
  "registry.k8s.io"
  "quay.io"
  "ghcr.io"
  "docker.io"
  "cr.fluentbit.io"
  "charts.bitnami.com"    # listed only so we can *warn* — Bitnami is banned
)

export AIRGAP_REGISTRY AIRGAP_BOOTSTRAP_REGISTRY_IMAGE AIRGAP_ARCH DOMAIN AIRGAP_BUNDLE_DIR AIRGAP_SKOPEO_DEST_TLS_VERIFY AIRGAP_SOURCE_REGISTRIES
