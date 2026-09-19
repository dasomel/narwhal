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

## Risk-scaled change workflow

- Class A documentation-only changes use the Issue/PR as the change record.
- Class B internal behavior changes require explicit acceptance criteria; use a Change Package when the work is complex, cross-component, or operationally risky.
- Class C dependency/runtime/toolchain/build-contract changes and Class D release/deployment/security-boundary changes require an accepted Change Package before broad implementation.
- For Class C/D or complex Class B work, load `.agents/skills/change-package-workflow/SKILL.md` and use `templates/change/CHANGE.md` plus `templates/change/TASKS.md` when a versioned working artifact is useful.
- Keep requirement → acceptance scenario → task → evidence traceability. Material scope changes require package update and re-review.
- At completion, synchronize durable truth into code/tests, normative docs, ADRs, evidence, and portfolio/status records; do not maintain a duplicate long-lived specification tree.

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
