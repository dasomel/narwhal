---
name: narwhal-verification
description: Verify Narwhal infrastructure and GitOps changes before claiming completion, distinguishing offline script/YAML/security checks from real cluster and end-to-end evidence. Use after component, upgrade, bug-fix, or configuration changes.
license: Apache-2.0
compatibility: Requires repository validation tools; real runtime verification additionally requires access to the target Narwhal Kubernetes cluster.
metadata:
  openforge-scope: project
  openforge-owner: dasomel/narwhal
  openforge-maturity: draft
  openforge-version: "1"
---

# Narwhal Verification

## Use When

- Finishing a change under provisioning scripts, GitOps charts/resources, platform configuration, or cluster integration.
- Deciding whether a fix/upgrade is actually proven.

## Do Not Use When

- You still need to identify the failure cause -> `narwhal-cluster-debug`.
- The requested change has not yet been implemented.

## Inputs

- Changed file list and intended behavior.
- Whether a Vagrant/cloud cluster is available.
- Any known high-risk boundary: secrets, RBAC, admission policy, API-server settings, storage, identity, or networking.

## Workflow

1. Read the relevant project Makefile/scripts/tests plus `docs/common/agent-operational-rules.md`.
2. Run the repository's actual deterministic checks for changed shell/YAML/templates rather than substituting prose review.
3. Validate rendered Helm/configuration where the change depends on chart semantics.
4. Check version/image inventory consistency when component versions or images moved.
5. Review secrets/RBAC/admission/image-source implications for high-risk changes.
6. If a cluster is available, inspect nodes, unhealthy Pods, ArgoCD application state, and the changed service's real behavior.
7. For networking, DNS, identity, storage, or ingress changes, test the user/service path rather than stopping at apply/sync success.
8. Cross-check `docs/common/lessons-log.md` for relevant regression signatures.

## Verification

Report evidence by class:

- static/lint/render;
- unit/script regression;
- GitOps/cluster state;
- end-to-end/user-side runtime;
- security/policy review.

State explicitly which higher-level path was not exercised. Do not use offline checks as proof of live-cluster behavior.

## Stop / Escalate When

- Required runtime evidence is unavailable and the property cannot be proven offline.
- The change widens privilege or destructive scope without an approved design.
- Validation reveals a separate issue outside the requested scope; report it separately rather than auto-fixing it.

## References

- `AGENTS.md`
- `docs/common/agent-operational-rules.md`
- `docs/common/lessons-log.md`
- existing Makefile/scripts/tests and GitOps validation entrypoints
