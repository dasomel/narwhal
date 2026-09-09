# Narwhal Agent Operational Rules

This document owns durable Narwhal-specific operational knowledge that should be available to any coding agent. Dated incidents remain in `docs/common/lessons-log.md`; repeatable task workflows live in project skills.

## Recording incidents

- Every fix writes or sharpens a row in `docs/common/lessons-log.md` under the matching cause section.
- Mistakes introduced while repairing count too; record the discriminator and the wrong-looking-but-plausible remedy that caused or hid the failure.
- Search the log for the symptom before adding a row. Improve an existing near-match rather than creating parallel truth.
- Record discriminators, not conclusions. A useful entry tells the next operator how to distinguish this cause from similar symptoms.
- A lesson becomes a durable rule here only after it generalizes beyond a single incident.

## Images and registries

- Never trust a mutable tag on the pre-baked Vagrant box. Pin immutable version tags.
- ArgoCD deploys manifest diffs, so a re-published mutable tag does not create a GitOps change. Version upgrades must change the manifest tag and update `scripts/airgap/images.txt` where applicable.
- Distroless images do not provide a shell. Use an image/tooling path that actually exposes the needed binary instead of assuming `/bin/sh`.
- Verify the running version from the application's own runtime/API evidence rather than inferring it from chart metadata.
- Registry preference for new dependencies is `ghcr.io` > `registry.k8s.io` > `quay.io` > `docker.io`; document why Docker Hub is unavoidable when used.
- Bitnami images/charts are not the default dependency source. Prefer upstream official images or Operators; use Bitnami only when there is no practical alternative and document the reason.

## GitOps / ArgoCD

- ArgoCD self-heal reverts ad-hoc `kubectl apply` changes. Persist intended changes through the Narwhal GitOps source and `scripts/gitops/push-to-gitea.sh` where that workflow applies.
- Do not push the Narwhal repository root directly to the internal Gitea remote; the trees have different roots.
- Never embed credentials in Git remote URLs.
- When ArgoCD reports Synced but live state differs, inspect `argocd-cm` `resource.customizations.ignoreDifferences` before treating synchronization itself as broken.

## Istio ambient

- Workloads that are incompatible with ztunnel/HBONE for SSO cookies or plain-HTTP kubelet probes may require `istio.io/dataplane-mode: none`.
- When opting out a workload, check its backing services/clients too; STRICT mTLS can otherwise block non-mesh traffic.

## Kyverno

- Optional fields in enforce `validate` patterns must use the correct optional anchors; a missing optional field must not make every Pod fail admission.
- A running workload is not proof that the current policy allows its next recreation. Before upgrades/reschedules of privileged, hostPID, hostNetwork, or hostPort workloads, verify the namespace/workload is still allowed by policy.

## Editing manifests and values

- Use structured YAML tooling such as `yq` for structural edits instead of brittle text substitution.
- Before adding a Helm values key, inspect the existing map and merge into it. Duplicate map keys can silently resolve last-wins.
- Validate rendered output with `helm template` where Helm behavior matters; YAML syntax alone is not sufficient.
- Boolean-like values passed through Helm CLI need type-aware handling; use string-specific options when the chart expects a string.

## Shell reliability

- Scripts retain `set -euo pipefail` unless there is a documented reason otherwise.
- Under `pipefail`, early-closing consumers such as `head -1`, `awk '{print; exit}'`, or `grep -q` can SIGPIPE an upstream producer and turn an apparent match into rc=141 or a false branch condition. Prefer a no-pipe check, capture first, or consume without early exit.
- A non-matching `grep` returns 1. Handle it deliberately when absence is an expected branch rather than allowing `set -e` to terminate the script.
- Send function logs to stderr when stdout is a data return path; command substitution captures stdout.

## Keycloak

- Use first-class Operator fields for supported resource settings rather than assuming `unsupported.podTemplate` survives reconciliation.
- Keycloak Operator workloads are StatefulSets; address the real Pod/container instead of assuming a Deployment.
- OIDC clients that require a specific audience must configure the appropriate audience mapper and verify the resulting token claim.

## Core flows

- Provisioning order is encoded in the script prefixes under `scripts/common/` and `scripts/cluster/`.
- `scripts/up.sh` is the project Phase 2 platform driver; bare `vagrant up` does not imply the same full platform path.
- GitOps application ownership lives under the Narwhal app/platform charts and repository GitOps resources. Confirm the current tree before adding a parallel source of truth.

## Repository guardrails

- `.vagrant/` is generated and must not be hand-edited.
- Do not hardcode passwords, tokens, kubeconfig credentials, or other secrets into scripts/manifests.
- Shell and YAML conventions are owned by existing format/lint checks where available.
- Infrastructure resources are constrained: avoid broad parallel cluster mutations. Change a bounded set, confirm it settles, then continue.
- Runtime success must be verified from the user/service side when the property depends on networking, DNS, routing, identity, storage, or the live cluster; an apply/sync success alone is not sufficient evidence.
