# Tasks: Align developer Argo CD sync scope

Link: [`CHANGE.md`](CHANGE.md)

## Inspect and establish evidence

- [x] `T-001` (`REQ-001`) Confirmed bootstrap allows only `tenants/*` while GitOps allows `*/*`.
- [x] `T-002` (`AC-001`) Captured source-level mismatch from latest `origin/main` (`283a270`).
- [x] `T-003` Reviewed separate `portal-reader` service-group permissions; keep unchanged.

## Implement

- [x] `T-010` (`REQ-001`, `REQ-002`) Align GitOps developer sync scope with bootstrap; `portal-reader` is preserved.
- [x] `T-011` (`REQ-003`) R242 checks both developer policies and group maps, both read-only defaults and groups scopes, plus the full GitOps `portal-reader` policy and group binding; 16 mutation cases reject privilege widening or drift.
- [x] `T-012` (`REQ-001`) Corrected the normative ArgoCD mapping and recorded both the source drift and checker-coverage lesson.

## Verify

- [x] `T-020` (`AC-001`) `helm template narwhal-platform gitops/charts/narwhal-platform --namespace devtools` exited 0; rendered policy has the tenant-scoped developer rule, unchanged `portal-reader` rules and bindings, and the D1 comment remains outside the `policy.csv` scalar.
- [x] `T-021` (`AC-002`) `python3 scripts/test/lib/check-argocd-developer-sync-scope.py --mutation-verify` passed and rejected all 16 mutations, including developer→admin group union, admin `policy.default` in either/both sources, altered OIDC scopes, and every portal-reader permission/binding change.
- [x] `T-022` `scripts/test/regression-check-kakao.sh --static` and serial `make test` each passed: 285 passed, 0 failed, 5 warnings. Warnings are existing absent portal sibling checks (`R117`, `R117b`, `R55`, `R216`) and absent airgap bundle (`R19`). No live cluster rollout was run.

## Synchronize durable truth

- [x] `T-030` Updated `docs/common/oidc-rbac-contract.md` and `docs/common/test-strategy.md` with the complete human/service policy contract and R242.
- [x] `T-031` Added Mistakes Log rows for source drift, incomplete inherited-role coverage, and overlapping mutation-suite execution.
- [ ] `T-032` Merge scoped change locally without pushing.

## Completion review

- [x] Requirements map to acceptance scenarios and verification evidence above.
- [x] Review finding resolved; cluster authorization still remains unverified because no live cluster change was made.

## Follow-up review evidence

- The first overlapping `--static` and `make test` run produced one R229 failure while its mutation checker temporarily edited shared checkout files. The R229 checker was rerun and passed; then `make test` was rerun by itself and passed 285/0. R242 also passed in each static suite.
- Follow-up review also identified that `policy.default: role:admin` bypassed the explicit developer mapping. R242 now asserts exact `role:readonly` defaults and `scopes: '[groups]'` in both writers and rejects each single-source or both-source mutation; the focused checker passes with 16 rejected mutations. The follow-up source changes are committed separately after verification; local merge is pending lead coordination.
