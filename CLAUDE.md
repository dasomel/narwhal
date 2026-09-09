@AGENTS.md

# Narwhal Claude adapter

Narwhal repository-wide rules live in `AGENTS.md`. Durable project-specific operational knowledge lives in `docs/common/agent-operational-rules.md`; dated failure history lives in `docs/common/lessons-log.md`. Do not duplicate those rules here.

## Project skill routing

Load the canonical project skill that matches the task:

| Task | Skill |
|---|---|
| add or materially change a platform component | `.agents/skills/narwhal-component-lifecycle/SKILL.md` |
| component/chart/image version upgrade | `.agents/skills/narwhal-version-upgrade/SKILL.md` |
| live cluster, provisioning, GitOps, SSO/network/storage diagnosis | `.agents/skills/narwhal-cluster-debug/SKILL.md` |
| final completion/evidence check | `.agents/skills/narwhal-verification/SKILL.md` |

The legacy `.claude/skills/narwhal-ops` entry is a compatibility router only.

## Claude-only workflow

- Use Plan mode for new components, major script/GitOps structure changes, and version upgrades when planning materially reduces risk.
- Project slash commands live in `.claude/commands/`.
- Ralph/OMC workflows use `.claude/templates/PROMPT.md` when explicitly invoked.
- Claude-specific agent-team/model routing belongs under `.claude/rules/` or the active harness, not in portable project skills.

## Source-of-truth reminders

- `scripts/up.sh` owns the full platform Phase 2 path; bare `vagrant up` is not equivalent.
- GitOps ownership must be determined from the current repository charts/resources before changing live objects.
- `.vagrant/` is generated.
- For non-obvious Narwhal shell, GitOps, image, Kyverno, Istio, Keycloak, secret, and constrained-VM rules, read `docs/common/agent-operational-rules.md` before editing the relevant path.
