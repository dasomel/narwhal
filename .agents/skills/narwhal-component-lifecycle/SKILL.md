---
name: narwhal-component-lifecycle
description: Add or materially change a Narwhal platform component across provisioning, GitOps, images, versions, and runtime validation. Use when work touches a platform component under scripts/cluster, gitops charts/resources, or VERSIONS.md.
license: Apache-2.0
compatibility: Requires the Narwhal repository and its documented shell, Helm, yq, Vagrant, and Kubernetes toolchain.
metadata:
  openforge-scope: project
  openforge-owner: dasomel/narwhal
  openforge-maturity: draft
  openforge-version: "1"
---

# Narwhal Component Lifecycle

## Use When

- Adding a new IDP/platform component.
- Changing a component's install path, GitOps ownership, image set, or runtime contract.
- Editing `scripts/cluster/`, Narwhal GitOps charts/resources, or the component inventory together.

## Do Not Use When

- Only diagnosing an existing live-cluster failure -> use `narwhal-cluster-debug`.
- Only bumping a component version without changing its lifecycle -> use `narwhal-version-upgrade`.
- Only proving completion -> use `narwhal-verification`.

## Inputs

- Component purpose and requested behavior.
- Existing adjacent component pattern.
- Upstream version/image/chart compatibility information.

## Workflow

1. Read `AGENTS.md`, `docs/common/agent-operational-rules.md`, the issue/spec, and the closest existing component implementation.
2. Determine current ownership across provisioning scripts, GitOps applications/resources, configuration, images, and `VERSIONS.md` before creating new files.
3. Check ARM64/AMD64 image availability and avoid mutable tags.
4. Make the smallest coherent component change. Preserve existing script ordering and GitOps source-of-truth boundaries.
5. Update every inventory/source that the component lifecycle actually owns; do not create a second deployment path for the same resource.
6. Render/validate configuration using existing project commands and structured YAML tooling.
7. Hand off to `narwhal-verification` for completion evidence.

## Verification

At minimum, verify syntax/rendering and consistency of changed scripts/YAML/version/image references. When the change affects live behavior, verify on the cluster or explicitly state that runtime behavior was not exercised.

## Stop / Escalate When

- The component requires new cluster-wide RBAC, admission exemptions, API-server flags, destructive storage changes, or a new source of truth not covered by the issue/design.
- Upstream architecture/image support is uncertain.
- The change would rely on mutable or disappearing artifacts without an accepted exception.

## References

- `AGENTS.md`
- `docs/common/agent-operational-rules.md`
- `docs/common/lessons-log.md`
- `scripts/cluster/`
- Narwhal GitOps charts/resources and `VERSIONS.md`
