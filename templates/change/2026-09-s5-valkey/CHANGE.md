# Change: Encrypt and authenticate the portal Valkey cache

**Merge only together with a portal image that supports VALKEY_USERNAME (not 1.0.19).**

- Change class: `D`
- Owner: narwhal
- Related issue: `S5-VALKEY-INSECURE-PRODUCTION`
- Package ID: `narwhal-s5-valkey-2026-09`
- Status: `Implementing`
- Accepted by / date: User request “남은 이슈 처리 후 머지” authorizes the remaining S5 work / 2026-09-30

## Problem

The portal cache accepted unauthenticated plaintext connections, while production portal
configuration enabled a bypass for its TLS/password readiness guard.

## Intent

The portal talks to its dedicated Valkey over TLS using one ACL identity. The password is
stored in a dedicated Kubernetes Secret, and only portal pods can reach the cache Service.

## Scope

- In scope: Valkey TLS certificate, TLS-only server listener, command-scoped ACL identity,
  dedicated generated Secret, portal env wiring, cache ingress NetworkPolicy, regression
  checks, and mistakes-log record.
- Affected users/systems: Narwhal portal cache and its live event stream.

## Non-goals

- Deploying to a shared cluster, rotating unrelated credentials, changing cache data model,
  or modifying portal source (owned by the linked consumer work order).

## Requirements

- `REQ-001` — Valkey serves TLS only with a `narwhal-ca-issuer` certificate for its service DNS.
- `REQ-002` — Only ACL user `portal` can issue the cache and live-stream commands it needs;
  the default user is disabled.
- `REQ-003` — The portal receives `rediss://narwhal-portal-valkey.devtools.svc.cluster.local:6379`,
  `VALKEY_TLS=true`, `VALKEY_USERNAME=portal`, and password from the dedicated Secret; no bypass.
- `REQ-004` — Cache ingress permits only portal pods on TCP 6379; credentials never appear in Git.

## Acceptance scenarios

### `AC-001` — Portal uses encrypted authenticated cache

- Covers: `REQ-001`, `REQ-002`, `REQ-003`
- Given the GitOps chart is applied and the dedicated auth Secret exists
- When the portal connects to Valkey
- Then TLS validates through the mounted Narwhal CA and the portal ACL identity can run cache
  and live-stream operations while anonymous and plaintext clients are rejected.

### `AC-002` — Cache is private and secret-free in Git

- Covers: `REQ-004`
- Given rendered source manifests and bootstrap scripts
- When the static regression check runs
- Then only portal-selected pods are allowed to connect and no credential value is committed.

## Architecture and decisions

- ADR threshold result: `not required` — existing ClusterIssuer, CA distribution, Service, and
  Secret bootstrap patterns are reused; no platform topology change.
- `D1` — Generate a URL-safe random password once and preserve it on bootstrap reruns, writing
  it only to a dedicated Secret; cost is one additional Secret; recovery is rerunning bootstrap
  to recreate the ACL file from that Secret without rotating credentials.
- `D2` — Use the existing Narwhal CA and only TLS on port 6379; cost is cert-manager readiness
  gating the pod; recovery is rollback of the linked portal/Valkey manifests as one change.
- `D4` — The password file feeds both Secret key `password` (portal env, used untrimmed) and the
  `users.acl` token, so it is generated with `openssl rand -hex 32 | tr -d '\n'` and both generated
  and preserved values must match `^[0-9a-f]{64}$` or bootstrap aborts. Reproduced on Valkey 8.1.10:
  `AUTH portal <hex>\n` returns WRONGPASS, `AUTH portal <hex>` returns OK. Cost: a malformed
  existing Secret blocks bootstrap; escape: delete that Secret deliberately and roll portal+Valkey.
- `D6` — Secret existence is probed separately from its password; an existing Secret without a
  valid 64-hex `password` aborts and is never regenerated (a running Valkey still holds the old ACL).
- `D7` — First creation uses plain `kubectl create` so concurrent first runs cannot overwrite each
  other; the AlreadyExists loser re-reads the winner's password. Later runs keep apply.
- `D5` — Certificate sets `duration: 8760h` / `renewBefore: 720h` because Valkey 8.1 has no
  `tls-auto-reload-interval` (absent from `CONFIG GET tls-*`); cost: one planned restart per year.
- `D3` — Restrict Valkey to explicit cache/live-stream commands and portal-only ingress; cost is
  future portal Valkey command additions require an ACL update; recovery is a reviewed ACL
  expansion limited to the required command/key/channel.

## Change impact

| Area | Impact / evidence needed |
|---|---|
| Source / API / command | Portal cache env contract and Valkey ACL commands |
| Dependencies / lockfiles | N/A — no dependency change |
| Runtime / toolchain | cert-manager issues Valkey service certificate |
| CI / CD | Static seam regression contract |
| Release / packaging | N/A — existing image and chart packaging |
| Generated output | Secret generated in cluster, never checked in |
| Security / supply chain | TLS, ACL, default user disabled, ingress scoped |
| Offline / air-gap | N/A — no image or package added |
| Documentation / operations | Package and mistakes log updated |
| Portfolio / downstream repositories | Linked portal work order must land in the same rollout |

## Verification plan

| Acceptance ID | Verification method | Environment | Expected evidence |
|---|---|---|---|
| `AC-001` | Chart render and static Valkey contract checks | Offline worktree | TLS-only listener, ACL and portal env references render |
| `AC-001` | Authenticated TLS cache/ring/PubSub, INFO readiness, anonymous/plaintext rejection, admin/channel denial | Disposable Docker Valkey 8.1.10 | Passed locally; this does not replace shared-cluster runtime evidence |
| `AC-001` | Authenticated TLS operations and anonymous/plaintext rejection | Shared cluster | Not run; deployment was not authorized |
| `AC-002` | Regression contract and secret-literal scan | Offline worktree | Check passes and rejects insecure source mutations |

## Rollout, rollback and recovery

- Rollout sequence: run `13-2-narwhal-portal-bindings.sh` to create/preserve the auth Secret;
  sync the Narwhal CA-signed certificate and TLS-only Valkey; deploy the linked portal change
  together after certificate and Secret readiness.
- Apply ORDER: run `13-2-narwhal-portal-bindings.sh` BEFORE ArgoCD syncs this chart. The Valkey
  pod mounts Secret `narwhal-portal-valkey-auth`; if the chart syncs first the pod stays in
  `ContainerCreating` until the Secret exists (self-heals once bootstrap runs).
- If the Secret holds `password` but no `users.acl`, bootstrap rebuilds `users.acl` from the
  preserved password (the ACL is always regenerated from the password file on every run).
- Rollback trigger and procedure: if TLS/authenticated portal readiness or live events fail,
  roll back both linked workloads as one operation and retain the dedicated Secret for retry.
- Data/configuration recovery: cache is ephemeral; recreate cert-manager certificate and ACL
  Secret from bootstrap if missing. Password rotation requires a coordinated portal rollout.

## Known limits

- Valkey 8.1 does not reload its TLS certificate. After cert-manager renews
  `narwhal-portal-valkey-tls` (30 days before the 1-year expiry), run
  `kubectl -n devtools rollout restart deploy/narwhal-portal-valkey`, or TLS clients fail at
  expiry. No reloader is installed on purpose. The yearly restart is prose only: there is no
  automation or alert for it. Suggested follow-up: open an issue to add a cert-expiry alert or a
  restart job.
- The R247 static check reads only this chart and the 13-2 script; it cannot see runtime state
  (live Secret contents, running pod flags) or other manifests that might also touch the cache.
- Live TLS handshake with the cluster CA and portal connectivity are not verified; only a
  disposable Docker Valkey 8.1.10 was exercised (AUTH byte-exactness, ACL).

## Evidence and durable synchronization

- Evidence location/format: exact command outcomes are recorded in `TASKS.md`; render output was
  temporary and contains references only, no credentials.
- Tests or checks that become durable regression controls: `scripts/test/lib/check-valkey-security-contract.py`.
- Documentation to update: this package and `docs/common/lessons-log.md`.
- ADR/evidence/portfolio records to update: no ADR required; linked portal work order remains the
  cross-repo rollout record.

## Review record

- Accepted scope/requirements: User's explicit remaining-issues-and-merge request authorizes S5
  provider hardening; implementation must not deploy to a shared cluster.
- Material changes after acceptance and re-review: none.
