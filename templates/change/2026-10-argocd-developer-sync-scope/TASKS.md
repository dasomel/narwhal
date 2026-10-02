# Tasks: Align developer Argo CD sync scope

Link: [`CHANGE.md`](CHANGE.md)

## Inspect and establish evidence

- [x] `T-001` (`REQ-001`) Confirmed bootstrap allows only `tenants/*` while GitOps allows `*/*`.
- [x] `T-002` (`AC-001`) Captured source-level mismatch from latest `origin/main` (`283a270`).
- [x] `T-003` Reviewed separate `portal-reader` service-group permissions; keep unchanged.

## Implement

- [ ] `T-010` (`REQ-001`, `REQ-002`) Align GitOps developer sync scope with bootstrap.
- [ ] `T-011` (`REQ-003`) Add focused consistency and negative-mutation regression coverage.
- [ ] `T-012` (`REQ-001`) Correct the app SSO permission matrix and record the lesson.

## Verify

- [ ] `T-020` (`AC-001`) Run focused source checker and inspect rendered chart policy.
- [ ] `T-021` (`AC-002`) Verify broad and divergent policy mutations fail.
- [ ] `T-022` Capture actual command output and note that no live cluster rollout occurred.

## Synchronize durable truth

- [ ] `T-030` Update normative SSO permission matrix.
- [ ] `T-031` Add mistake-log row after checking for a near-match.
- [ ] `T-032` Merge scoped change locally without pushing.

## Completion review

- [ ] Requirements map to acceptance scenarios and verification evidence.
- [ ] Review finding resolved; cluster authorization still recorded as unverified.
