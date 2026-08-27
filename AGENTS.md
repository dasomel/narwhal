# AGENTS.md

Narwhal follows the OpenForge context-efficient agent engineering model.

Read `README.md`, architecture/design documents, repository-local instructions, and the relevant issue/spec before editing. Use documented Makefile/scripts/tests as the source of truth for verification.

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

Do not claim completion without relevant executable evidence. State exactly which checks ran and their scope.

## Convergence

End substantive work as A) complete and verified, B) meaningful verified progress with the next blocker isolated, or C) stop because further work requires unjustified scope, fragile patches, unsupported assumptions, or unacceptable risk.

Do not keep patching when the work is no longer converging.

Reference standard: https://github.com/dasomel/openforge/blob/main/docs/agent-engineering.md
