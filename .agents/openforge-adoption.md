# OpenForge adoption

This repository follows these canonical OpenForge standards:

- Model-agnostic agent instruction design: https://github.com/dasomel/openforge/blob/main/docs/model-agnostic-agent-instructions.md
- User-centric validation: https://github.com/dasomel/openforge/blob/main/docs/user-centric-validation.md
- Agent engineering: https://github.com/dasomel/openforge/blob/main/docs/agent-engineering.md

Keep repository-specific invariants local. Do not copy canonical policy into model-specific prompt forks.

For user-facing, install/configuration, upgrade, integration, auth/RBAC, networking, storage, GitOps, or other high-risk changes, select validation proportional to risk and user impact. A green CI run is necessary evidence but not sufficient proof of the real user path. Prefer a clean/fresh environment, independently derived expected behavior, failure/recovery paths, and regression evidence for confirmed user-visible defects.

Safe local and disposable inspect/edit/build/test/fix/retest work within the requested scope may proceed without repeated approval. Production/shared-environment mutation, destructive external actions, release/publish, credential/permission widening, or unrelated external mutation requires explicit authorization unless already granted.
