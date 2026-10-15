# Tasks: Encrypt and authenticate the portal Valkey cache

Change Package: [`CHANGE.md`](CHANGE.md), package ID `narwhal-s5-valkey-2026-09`.

## Inspect and establish evidence

- [x] `T-001` (`REQ-001`–`REQ-004`) Confirm insecure provider settings and portal consumer contract.
- [x] `T-002` (`AC-001`) Confirm ClusterIssuer and CA distribution patterns.
- [x] `T-003` (`REQ-002`) Inspect actual cache, idempotency, and event-stream commands.

## Implement

- [x] `T-010` (`REQ-001`, `AC-001`) Add Cluster CA-signed Valkey service certificate and TLS-only listener.
- [x] `T-011` (`REQ-002`, `AC-001`) Generate/preserve dedicated password Secret and restricted ACL.
- [x] `T-012` (`REQ-003`, `AC-001`) Inject secure URL, TLS, username, and secret-backed password; remove bypass.
- [x] `T-013` (`REQ-004`, `AC-002`) Restrict Valkey ingress to the portal workload.
- [x] `T-014` (`AC-002`) Add static regression coverage and mistakes-log entry.

## Verify

- [x] `T-020` (`AC-001`) Run offline chart render and static contract checks.
- [ ] `T-021` (`AC-001`) Verify authenticated TLS cache/live-stream operations and anonymous/plaintext rejection on a cluster.
- [x] `T-022` (`AC-002`) Record exact commands/results and ensure no literal password is present.
- [x] `T-023` (`AC-002`) Add mutation verification for the regression checker.

## Synchronize durable truth

- [x] `T-030` Update package and mistakes log.
- [x] `T-031` ADR not required; existing platform trust boundary is reused.
- [x] `T-032` Record coordinated rollout, rollback, and password recovery in the package.
- [x] `T-033` Link the paired consumer work order; no shared-cluster status claimed.

## Completion review

- [ ] Shared-cluster runtime evidence remains pending; disposable local-container evidence is below.
- [ ] No package scope changes after acceptance.
- [x] Local verification output recorded below; no credential value is included.
- [ ] Remaining runtime check owned by paired rollout.

## Observed evidence

- `python3 scripts/test/lib/check-valkey-security-contract.py` — PASS; 21 mutations rejected (parsed flags/ACL; duplicate --port, commented default off, +@all/nopass/allcommands, second user line, raw `> password`), including unrestricted ingress, a broad peer selector, and both inline password wiring forms.
- `bash -n scripts/cluster/13-2-narwhal-portal-bindings.sh` — PASS.
- `helm template narwhal-platform gitops/charts/narwhal-platform --namespace devtools --set baseDomain=local.narwhal.internal > /tmp/s5-valkey-rendered.yaml` — PASS.
- `kubeconform -strict -ignore-missing-schemas /tmp/s5-valkey-rendered.yaml` — PASS.
- `make validate` — PASS; Vagrantfile syntax and repository GitOps YAML parsed.
- `make security` — PASS; no obvious hardcoded credentials found.
- `shellcheck --severity=warning scripts/cluster/13-2-narwhal-portal-bindings.sh` — PASS.
- `make test` — PASS; 282 passed, 0 failed, 5 warnings; R247 passed.
- `make lint` — ShellCheck phase passed, then stopped with `/bin/sh: yamllint: command not found`.
- `uvx --from yamllint yamllint -d '{extends: default, rules: {line-length: {max: 200}, truthy: disable}}' gitops/apps/*.yaml gitops/resources/*.yaml gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml` — the modified chart has no lint finding; repository-wide lint reports existing issues in untouched `gitops/resources/ghost-pod-reaper.yaml` and `gitops/resources/velero-server-rbac.yaml`.
- Disposable Docker test using `docker.io/valkey/valkey:8-alpine` (reported Valkey 8.1.10): INFO,
  authenticated TLS GET/SET and live ring commands passed; subscribed client received the event;
  anonymous TLS returned `NOAUTH`, plaintext failed TLS negotiation, CONFIG and an unlisted
  Pub/Sub channel returned `NOPERM`. The throwaway password and certificates stayed in temporary
  files and were not printed. This does not verify Kubernetes NetworkPolicy, cert-manager issuance,
  bootstrap behavior against a live cluster, or the actual portal deployment.
- Review follow-up (D4/D5): on Docker Valkey 8.1.10 `AUTH portal <hex>\n` (old generation) returned
  WRONGPASS while `AUTH portal <hex>` returned OK; the fixed generation (`| tr -d '\n'`, 64-hex
  validation) authenticates with the exact Secret bytes. `tls-auto-reload-interval` does not exist
  in 8.1.10, so the Certificate has explicit `duration`/`renewBefore` and CHANGE.md documents the restart.
