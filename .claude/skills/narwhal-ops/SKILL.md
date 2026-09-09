---
name: narwhal-ops
description: Claude compatibility router for Narwhal infrastructure work. Use when an existing Claude workflow invokes narwhal-ops; route the task to the canonical project skill for component lifecycle, version upgrade, cluster debugging, or verification.
license: Apache-2.0
compatibility: Claude adapter; canonical project workflows live under .agents/skills/.
metadata:
  openforge-scope: project
  openforge-owner: dasomel/narwhal
  openforge-maturity: deprecated
  openforge-version: "2"
---

# Narwhal Ops Compatibility Router

Do not maintain infrastructure workflows in this file. Load the matching canonical project skill:

| Task | Canonical skill |
|---|---|
| add/change a platform component | `../../../.agents/skills/narwhal-component-lifecycle/SKILL.md` |
| version/chart/image upgrade | `../../../.agents/skills/narwhal-version-upgrade/SKILL.md` |
| live cluster/provisioning diagnosis | `../../../.agents/skills/narwhal-cluster-debug/SKILL.md` |
| completion/evidence validation | `../../../.agents/skills/narwhal-verification/SKILL.md` |

Claude-specific subagent orchestration, if used, belongs in `.claude/rules/` or the caller harness rather than in the portable project skill.

The existing `references/` directory is retained temporarily for migration/history; new canonical workflow knowledge should go to repository docs or the owning project skill.
