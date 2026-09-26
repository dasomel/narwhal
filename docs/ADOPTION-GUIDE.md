# Narwhal Adoption Guide

> Goal: minimize **Time to First Verified Success** for a new operator. Exact component versions remain authoritative in `VERSIONS.md`.

## 1. What success means

Narwhal is not considered successfully installed merely because Kubernetes nodes are `Ready`. A first success means the cluster is healthy **and** the platform integration layer is observable: GitOps reconciliation works, identity is reachable, core platform applications become healthy, and the repository verification path can prove that state.

## 2. Choose the smallest supported path

Start with the Vagrant development profile unless you specifically need Kakao Cloud AMD64 or the air-gapped path. Treat those as separate deployment profiles rather than variations of one quick start.

## 3. First-success contract

Use the repository README for prerequisites and bootstrap commands, then verify in this order:

1. Kubernetes control plane and workers are healthy.
2. Argo CD can reconcile the GitOps application set.
3. Core platform endpoints and identity integration are reachable.
4. Run `scripts/test/verify-cluster.sh` for live cluster verification.
5. Run `scripts/test/test-sso.sh` when validating the SSO integration boundary.
6. Use the deployment-profile regression check where applicable, for example `scripts/test/regression-check-kakao.sh` on Kakao Cloud.

A green unit/static check does not substitute for live cluster or SSO evidence.

## 4. Read next

- `docs/IMPLEMENTATION-STATUS.md` — implemented scope, not roadmap
- `VERSIONS.md` — exact component versions
- `docs/common/architecture.md` — architecture
- `docs/common/lessons-log.md` — integration/incident knowledge
- `docs/common/compliance-hardening.md` — governance and hardening

## 5. Operating boundaries

RBAC widening, destructive operations, cluster topology changes, storage/network/identity changes, and GitOps source-of-truth changes are design-level changes. They require evidence appropriate to the affected runtime boundary.

## 6. Documentation rule

When installation, compatibility, identity, architecture, or verification behavior changes, update the source document first and refresh `docs/IMPLEMENTATION-STATUS.md` and this adoption path only from reproducible evidence.