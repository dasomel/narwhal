---
name: narwhal-version-upgrade
description: Upgrade a Narwhal platform component version across manifests, images, charts, air-gap inventory, and runtime evidence. Use when bumping an existing component or dependency version without redesigning the component lifecycle.
license: Apache-2.0
compatibility: Requires repository checkout plus the documented Helm, yq, shell, Vagrant, and Kubernetes toolchain; network research may be required for upstream release notes.
metadata:
  openforge-scope: project
  openforge-owner: dasomel/narwhal
  openforge-maturity: verified
  openforge-version: "1"
---

# Narwhal Version Upgrade

## Use When

- Bumping an existing component image/chart/application version.
- Reviewing breaking changes or version compatibility for an existing Narwhal component.

## Do Not Use When

- The component is new or its ownership/install structure changes -> `narwhal-component-lifecycle`.
- The current issue is an unexplained runtime failure -> `narwhal-cluster-debug`.

## Inputs

- Component name, current version, requested/target version.
- All current version/image references in the repository.
- Upstream release notes and architecture/image support evidence.

## Workflow

1. Read the issue/spec, `VERSIONS.md`, `docs/common/agent-operational-rules.md`, and grep all current component references.
2. Verify the target release exists, is appropriate for both supported architectures, and identify breaking chart/config/API changes.
3. Update the canonical version/image/chart references together. A tag change must include the air-gap image inventory when that artifact is preloaded.
4. Do not use a mutable tag as an upgrade mechanism; GitOps needs a manifest diff.
5. Mirror the new pin on the bastion registry BEFORE the pin bump goes live (mandatory on air-gapped/Kakao clusters, where nodes have no upstream egress and a reschedule of an unmirrored pin ends in `ImagePullBackOff`): on the bastion, `skopeo copy --all --preserve-digests docker://<src>@<digest> docker://<mirror>/<path>:<tag>`, then confirm from a node that `/v2/<path>/manifests/<tag>` answers 200 with the same `Docker-Content-Digest`. `<path>` is the fully qualified ref (`docker.io/library/busybox`, `docker.io/<org>/<name>`, `ghcr.io/...`). Verify with `MIRROR=http://<bastion>:5000 python3 scripts/test/check-mirror-pins.py` (read-only; fails on MISSING/DIGEST-MISMATCH). Runbook: `scripts/airgap/README.md` ("Mirror a pin bump").
6. Render/validate changed Helm/YAML and run relevant repository checks.
7. If a cluster is available, confirm the application-reported running version rather than inferring it from chart metadata.
8. Finish with `narwhal-verification` and state any runtime gap.

## Verification

Evidence should cover reference consistency, rendered configuration, build/static checks where applicable, and live reported version when runtime verification is claimed.

## Stop / Escalate When

- Release notes imply a migration, API/RBAC/storage redesign, or unsupported architecture.
- The new artifact is unavailable in an acceptable registry or requires a mutable tag exception.
- Runtime evidence contradicts the declared version.

## References

- `VERSIONS.md`
- `scripts/airgap/images.txt`
- `scripts/test/check-mirror-pins.py`
- `docs/common/agent-operational-rules.md`
- component provisioning/GitOps sources
