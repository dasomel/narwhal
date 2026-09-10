# AGENTS.md

Narwhal follows the OpenForge model-agnostic agent engineering model.

Inspect repository guidance, architecture/design context, project skills, and the issue/spec relevant to the current task before editing. Do not load unrelated documentation by default. Use documented Makefile/scripts/tests as the source of truth for verification.

## Work contract

- Make the smallest coherent change that solves the requested problem.
- Do not auto-fix unrelated findings; report them separately.
- Preserve Kubernetes/platform layer boundaries, GitOps ownership, security boundaries, and existing access restrictions.
- Treat exported APIs, RBAC/permission widening, destructive operations, cluster topology changes, and source-of-truth changes as design changes.
- Follow existing style and naming conventions. Formatter/linter rules own deterministic style.
- Comments explain why, invariants, compatibility constraints, or hazards; do not narrate obvious code.

## Bug fixes

When feasible: reproduce -> failing regression test/evidence -> minimal fix -> same test passes -> relevant regression suite.

Do not substitute mocked/unit evidence for real cluster/runtime verification when the defect depends on Kubernetes, networking, storage, GitOps, identity, or external services.

## Verification

Do not claim completion without relevant executable evidence. State exactly which checks ran and their scope. Choose verification proportional to task risk and user impact; for user-facing, installation, configuration, upgrade, integration, or high-risk changes, exercise the relevant public journey from a clean environment when practical.

Safe local/disposable inspect-edit-build-test-fix-retest work may proceed within the requested scope. Shared/production/destructive/release/credential/permission/external mutations require explicit authorization unless already granted.

## Convergence

End substantive work as A) complete and verified, B) meaningful verified progress with the next blocker isolated, or C) stop because further work requires unjustified scope, fragile patches, unsupported assumptions, or unacceptable risk.

Do not keep patching when the work is no longer converging.

References:
- https://github.com/dasomel/openforge/blob/main/docs/agent-engineering.md
- https://github.com/dasomel/openforge/blob/main/docs/model-agnostic-agent-instructions.md
- https://github.com/dasomel/openforge/blob/main/docs/user-centric-validation.md
