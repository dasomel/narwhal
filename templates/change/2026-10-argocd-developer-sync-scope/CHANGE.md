# Change: Align developer Argo CD sync scope

- Change class: `D`
- Owner: `narwhal-ops`
- Related issue: SSO/RBAC matrix review finding (2026-10-02)
- Status: `Implementing`
- Accepted by / date: User request to handle the remaining issues and merge / 2026-10-02

## Problem

The bootstrap script restricts the human `developer` group's Argo CD sync permission to
`tenants/*`, but the GitOps-managed `argocd-rbac-cm` grants that group sync on `*/*`.
GitOps reconciliation can therefore restore wider deployment authority than the bootstrap
configuration intends. The separate `narwhal-portal` service group has its own policy and
must not be changed by this fix.

## Intent

Keep the human `developer` role's Argo CD sync permission limited to tenant applications in
both bootstrap and GitOps desired state, with a regression check that detects future drift.

## Scope

- In scope: developer sync policy in the GitOps chart and bootstrap script, focused static
  regression coverage, this package, the SSO/RBAC matrix, and a lessons-log entry.
- Affected users/systems: Narwhal developers and Argo CD authorization configuration.

## Non-goals

- Do not change the separate `narwhal-portal` service-account group's read/list/log/sync
  policy.
- Do not apply configuration to a shared cluster as part of this change.
- Do not change Argo CD read visibility or Kubernetes RBAC roles.

## Requirements

- `REQ-001` — Human `developer` may sync only `tenants/*`; both bootstrap and GitOps
  configuration must encode the same project scope.
- `REQ-002` — Preserve the separate `narwhal-portal` service-group permissions unchanged.
- `REQ-003` — A durable regression check must fail if either source broadens or diverges.

## Acceptance scenarios

### `AC-001` — Restrict and align human developer sync

- Covers: `REQ-001`, `REQ-002`
- Given the GitOps-managed ConfigMap and bootstrap-generated ConfigMap
- When their policies are inspected or rendered
- Then `developer` sync is `tenants/*` in both and the `narwhal-portal` policy is unchanged.

### `AC-002` — Detect future scope drift

- Covers: `REQ-003`
- Given a mutation that changes either developer sync rule to `*/*` or makes the two scopes
  differ
- When the focused regression check runs
- Then it fails with a specific mismatch/broad-scope diagnostic.

## Architecture and decisions

- Relevant ADR/design links: OIDC/RBAC contract and Argo CD policy in `13-argocd.sh`.
- ADR threshold result: `not required` — this aligns two existing declarations to the
  bootstrap's documented least-privilege boundary without introducing a new role model.
- `D1`: Preserve Portal service-account sync while narrowing the human developer group.
  Reason: the service identity and human group have distinct consumers and needs. Cost:
  developers cannot directly sync platform applications. Escape hatch: a separately
  reviewed, narrowly scoped grant with matching GitOps and bootstrap policy updates.

## Change impact

| Area | Impact / evidence |
|---|---|
| Source / API / command | Narwhal Argo CD RBAC ConfigMap; no public API change. |
| Dependencies / lockfiles | N/A — static YAML/shell/test change. |
| Runtime / toolchain | Argo CD authorization changes on a later cluster rollout; no live rollout here. |
| CI / CD | Focused RBAC source consistency regression check. |
| Release / packaging | N/A — GitOps configuration is applied through the existing flow. |
| Generated output | N/A — no generated artifact changes. |
| Security / supply chain | Reduces human developer deployment scope; Portal service role remains unchanged. |
| Offline / air-gap | N/A — static local checks only. |
| Documentation / operations | SSO permission matrix and lessons log updated. |
| Portfolio / downstream repositories | No Portal source change; the user-group contract is unchanged. |

## Verification plan

| Acceptance ID | Verification method | Environment | Expected evidence |
|---|---|---|---|
| `AC-001` | Focused static policy check and rendered chart inspection | Clean local checkout | Both policies use `tenants/*`; Portal policy unchanged. |
| `AC-002` | Focused checker plus broad/divergent negative mutations | Local disposable copies | Mutations fail; unchanged source passes. |

Live Argo CD authorization and cluster sync are outside this change because no shared-cluster
rollout was requested.

## Rollout, rollback and recovery

- Rollout sequence: merge desired-state change; apply only through the normal GitOps flow in
  a separately authorized rollout.
- Rollback trigger and procedure: if a tenant developer can no longer sync intended tenant
  apps, restore the last known-good policy through GitOps and reconcile both declarations.
- Data/configuration recovery: N/A — no data migration.
- Compatibility or migration obligations: verify existing tenant sync remains allowed before
  a live rollout.

## Evidence and durable synchronization

- Evidence location/format: focused checker output and rendered chart evidence recorded in
  `TASKS.md` or the change's review.
- Tests or checks that become durable regression controls: checker enforcing exact matching
  developer sync scope across both configurations.
- Documentation to update: `docs/common/app-sso-permission-matrix.md` and lessons log.
- ADR/evidence/portfolio records to update: N/A — existing policy contract is clarified.

## Review record

- Accepted scope/requirements: D1 and `REQ-001`–`REQ-003` accepted under the user's request
  to resolve remaining issues and merge on 2026-10-02.
- Material changes after acceptance and re-review: none.
- Open questions or blockers: live Argo CD authorization remains unverified until a later
  authorized cluster rollout.

## Known limits

LOW residuals of the R246 checker (`scripts/test/lib/check-argocd-developer-sync-scope.py`), accepted and not covered:

- A `kubectl patch`/`create`/`replace` of `argocd-rbac-cm` outside the pinned heredocs of `13-argocd.sh` is not inspected.
- Other charts or files declaring `argocd-rbac-cm` are out of scope (only the platform chart template and `13-argocd.sh`).
- Other `argocd-cm` settings that widen access (for example `users.anonymous.enabled`) are not pinned.
- The `argocd-rbac-cm` `metadata.namespace` is not pinned.
- Helm rendering is not verified in CI: `{{ }}` expressions are pinned textually, and `helm template` was checked by hand only (rendered data keys `policy.csv`, `policy.default`, `scopes`).
