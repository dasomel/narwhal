# AGENTS.md

Narwhal follows the OpenForge agent engineering model: [change-management](https://github.com/dasomel/openforge/blob/main/docs/change-management.md) (risk-scaled change classes, convergence) and [agent-engineering](https://github.com/dasomel/openforge/blob/main/docs/agent-engineering.md). Narwhal deviates only where stated below.

## Load on demand

- Non-obvious shell, GitOps, image, Kyverno, Istio, Keycloak, secret and constrained-VM rules: `docs/common/agent-operational-rules.md`, before editing the relevant path. Dated failure history, and the rule that every fix records a row: `docs/common/lessons-log.md`.
- Project skills (`.agents/skills/<name>/SKILL.md`):
  - `narwhal-component-lifecycle`: add or materially change a platform component
  - `narwhal-version-upgrade`: component/chart/image version upgrade
  - `narwhal-cluster-debug`: live cluster, provisioning, GitOps, SSO/network/storage diagnosis
  - `narwhal-verification`: final completion/evidence check

## Boundaries

- Preserve Kubernetes/platform layer boundaries, GitOps ownership and security boundaries. Treat exported APIs, RBAC/permission widening, destructive operations, cluster topology changes and source-of-truth changes as design changes. Determine GitOps ownership from the current charts/resources before touching live objects.
- Shared/production/destructive/release/credential/permission/external mutations need explicit authorization; local disposable work within the requested scope does not.
- `scripts/up.sh` owns the full platform Phase 2 path; bare `vagrant up` is not equivalent. `.vagrant/` is generated.
- Mocked/unit evidence does not substitute for real cluster verification when the defect depends on Kubernetes, networking, storage, GitOps, identity or external services.

## Verification

`make lint` (shellcheck + yamllint), `make validate` (Vagrantfile + GitOps YAML parse) and `make test` (static regression suite) mirror CI; `make e2e` and the `scripts/test/` live checks need a running cluster. Do not auto-fix unrelated findings; report them.
