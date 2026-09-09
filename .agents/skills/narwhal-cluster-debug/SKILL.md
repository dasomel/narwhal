---
name: narwhal-cluster-debug
description: Diagnose and fix Narwhal live-cluster failures by correlating Kubernetes state, GitOps ownership, configuration, and recorded failure discriminators. Use for unhealthy pods, broken integrations, sync/runtime drift, SSO/network/storage failures, or failed provisioning.
license: Apache-2.0
compatibility: Requires the Narwhal checkout and, for live evidence, access to the target Vagrant/cloud Kubernetes cluster and relevant CLI tools.
metadata:
  openforge-scope: project
  openforge-owner: dasomel/narwhal
  openforge-maturity: draft
  openforge-version: "1"
---

# Narwhal Cluster Debug

## Use When

- A cluster service, Pod, route, SSO flow, GitOps application, storage path, or provisioning step is failing.
- The symptom could have multiple known Narwhal-specific causes.

## Do Not Use When

- Adding a new component -> `narwhal-component-lifecycle`.
- Performing a planned version-only bump -> `narwhal-version-upgrade`.

## Inputs

- Exact symptom/error and when it started.
- Target namespace/workload/component.
- Current repository revision and cluster context.

## Workflow

1. Read `docs/common/lessons-log.md` and `docs/common/agent-operational-rules.md`; search for the symptom/discriminator before changing anything.
2. Capture current live evidence: relevant Pods/events/logs, ArgoCD state, service/endpoints, and the owning manifest/config source.
3. Determine whether the live object is GitOps-owned before applying a temporary change. Self-heal can erase ad-hoc fixes.
4. Compare repository intent to live state. For Synced-but-different resources, inspect configured ignore differences before blaming ArgoCD.
5. Isolate one cause. Prefer a reproduction or executable discriminator that separates it from similar failures.
6. Apply the smallest fix in the owning source. Avoid parallel cluster mutations that obscure causality or exhaust constrained VMs.
7. Re-run the same discriminator and verify from the user/service side when networking, identity, DNS, storage, or routing is involved.
8. Update or sharpen `docs/common/lessons-log.md` for a newly learned incident pattern.
9. Finish with `narwhal-verification` before claiming completion.

## Verification

Report repository evidence and live-cluster evidence separately. A manifest apply, ArgoCD Synced state, or healthy Pod alone does not prove end-to-end service behavior.

## Stop / Escalate When

- A fix requires permission widening, admission exemptions, destructive storage action, or topology change not in scope.
- The target cluster/context cannot be verified safely.
- Multiple patches no longer narrow the diagnosis.

## References

- `docs/common/lessons-log.md`
- `docs/common/agent-operational-rules.md`
- owning provisioning/GitOps sources
- project validation commands and cluster operational scripts
