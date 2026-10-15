# AGENTS.md

Narwhal follows the OpenForge agent engineering model: [change-management](https://github.com/dasomel/openforge/blob/main/docs/change-management.md) (risk-scaled change classes, convergence) and [agent-engineering](https://github.com/dasomel/openforge/blob/main/docs/agent-engineering.md). Narwhal deviates only where stated below.

## Load on demand

- Non-obvious shell, GitOps, image, Kyverno, Istio, Keycloak, secret and constrained-VM rules: `docs/common/agent-operational-rules.md`, before editing the relevant path. Dated failure history, and the rule that every fix records a row: `docs/common/lessons-log.md`.
- Project skills (`.agents/skills/<name>/SKILL.md`):
  - `narwhal-component-lifecycle`: add or materially change a platform component
  - `narwhal-version-upgrade`: component/chart/image version upgrade
  - `narwhal-cluster-debug`: live cluster, provisioning, GitOps, SSO/network/storage diagnosis
  - `narwhal-verification`: final completion/evidence check

## `portal/` (Narwhal IDP Portal, imported into this repo)

- `portal/` has its own `portal/AGENTS.md` and `CLAUDE.md` bridge; read them before editing it. Run portal commands (`pnpm`, `make`) from `portal/`, not the root.
- Git tags are `portal/vX.Y.Z`; the image name stays `ghcr.io/dasomel/narwhal-portal`. The GitOps pin is a published release, so it can lag `portal/` HEAD; seam checks (R55, R117, R211, R216) read the in-repo tree, never a tag or a sibling checkout.
- Lessons-log: a fix's row goes in the log of the area it touches (`portal/docs/lessons-log.md` or `docs/common/lessons-log.md`); a fix spanning both goes in narwhal's. Decision record: `docs/adr/0001-consolidate-narwhal-portal-into-monorepo.md`.
- Issue/PR numbers: a bare `#N` in `portal/` docs, code comments, lessons-log rows or commit messages written before PR #347 merged (2026-10-15) refers to a `dasomel/narwhal-portal` issue or PR, not a narwhal one (the two repos number issues and PRs independently, so the same `#N` can exist in both). The 27 open portal issues were transferred to narwhal #356-#382 (mapping table: the comment on narwhal#356, label `area:portal`); URLs of those 27 transferred issues redirect (closed issues and PRs stay in the old repo and do not). Qualify new cross-repo references as `dasomel/narwhal-portal#N`.

## Boundaries

- Preserve Kubernetes/platform layer boundaries, GitOps ownership and security boundaries. Treat exported APIs, RBAC/permission widening, destructive operations, cluster topology changes and source-of-truth changes as design changes. Determine GitOps ownership from the current charts/resources before touching live objects.
- Shared/production/destructive/release/credential/permission/external mutations need explicit authorization; local disposable work within the requested scope does not.
- `scripts/up.sh` owns the full platform Phase 2 path; bare `vagrant up` is not equivalent. `.vagrant/` is generated.
- Mocked/unit evidence does not substitute for real cluster verification when the defect depends on Kubernetes, networking, storage, GitOps, identity or external services.
- Merge requires an `independent-review` success commit status on the PR head SHA, posted by the independent reviewer (never the author lane) after a PASS via `python3 scripts/ci/mark-review-pass.py <pr> --sha <full-40-hex-reviewed-sha>` (the reviewer passes the full SHA it actually reviewed; any other head is refused). Any later push is a new SHA with no status, so it invalidates the review. Procedural control: any writer can post the status, it is not identity-proof. Escape hatch is an admin merge, stated in the PR body.

## Verification

`make lint` (shellcheck + yamllint), `make validate` (Vagrantfile + GitOps YAML parse) and `make test` (static regression suite) mirror CI; `make e2e` and the `scripts/test/` live checks need a running cluster. Do not auto-fix unrelated findings; report them.
