#!/bin/bash
# Shared configuration for airgap scripts. Source this from other 0X-*.sh scripts.
# Override via env vars before sourcing.

# Target private registry URL (hostname:port) — where mirrored images land
: "${AIRGAP_REGISTRY:=registry.airgap.local:5000}"

# Architecture(s) to mirror. Narwhal default is ARM64 (Apple Silicon host).
# Multiple archs: "linux/arm64,linux/amd64"
: "${AIRGAP_ARCH:=linux/arm64}"

# Narwhal domain (used for Harbor endpoint resolution in airgap mode)
: "${DOMAIN:=local.narwhal.io}"

# Bundle output directory — collected tar/OCI layouts + Helm charts
: "${AIRGAP_BUNDLE_DIR:=$(pwd)/narwhal-airgap-bundle}"

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

export AIRGAP_REGISTRY AIRGAP_ARCH DOMAIN AIRGAP_BUNDLE_DIR AIRGAP_SKOPEO_DEST_TLS_VERIFY AIRGAP_SOURCE_REGISTRIES
