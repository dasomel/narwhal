# Current Implementation Status

Last verified: 2026-08-28 against `main`.

This file is a concise snapshot of capabilities that are implemented and documented in the repository today. It is not a roadmap; planned work belongs in GitHub Issues and design documents.

## Platform scope

Narwhal is an open-source Kubernetes Internal Developer Platform that integrates GitOps, IAM/SSO, service mesh, observability, registry, storage, backup, policy, API gateway and a management portal as one reproducible operating unit.

## Implemented baseline

- Kubernetes v1.35 HA control plane
- Cilium + Hubble networking and observability
- kube-vip control-plane VIP and MetalLB load balancing
- APISIX API gateway with OIDC integration
- Argo CD + Gitea GitOps application delivery
- Keycloak SSO integration across platform applications
- Istio ambient mode service mesh
- Prometheus, Grafana, Loki, Tempo and Grafana Alloy observability stack
- Harbor registry, OpenBao secrets, Kyverno policy, Headlamp UI
- NFS CSI, SeaweedFS, Velero and CloudNative-PG platform services
- Narwhal Portal for day-2 platform visibility and operations

## Verification and evidence

The current README records the following verified scale:

- 35 GitOps-managed applications
- 51 CI regression checks
- 120+ live cluster verification checks
- 49 SSO verification checks
- 263 documented integration incidents with discriminators
- 104 container images and 27 Helm charts in the architecture-specific offline bundle

The operating model is deliberately evidence-driven:

```text
Incident -> Lesson -> Discriminator -> Regression check -> Upgrade gate
```

The source of truth for exact component versions is `VERSIONS.md`; duplicated exact versions should not be added to new documents.

## Deployment profiles

Implemented deployment paths include:

- Vagrant development environments on ARM64/AMD64
- Kakao Cloud AMD64
- fully air-gapped installation profiles

## Engineering governance

The repository now also adopts the OpenForge engineering baseline for documentation naming, security policy, GitHub templates and shared engineering conventions. Compliance and hardening guidance is maintained under `docs/common/`.

## Related evidence

- `README.md`
- `VERSIONS.md`
- `docs/common/lessons-log.md`
- `docs/common/architecture.md`
- `docs/common/compliance-hardening.md`
- `scripts/test/verify-cluster.sh`
- `scripts/test/test-sso.sh`
- `scripts/test/regression-check-kakao.sh`

When one of the figures or capabilities above changes, update the source document first and then refresh this snapshot.