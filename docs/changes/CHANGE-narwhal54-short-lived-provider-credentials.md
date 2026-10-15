# Change: short-lived, rotatable credentials for portal↔cluster providers

- Change class: `D`
- Owner: (unassigned)
- Related issue: narwhal-portal#54 (cluster-side half); prior work: narwhal-portal#134 (ArgoCD/Gitea read-path typed errors), narwhal-portal#176 (K8s 401/403 → `K8sCredentialError`)
- Status: `Draft`
- Accepted by / date: —

## Problem

`scripts/cluster/13-2-narwhal-portal-bindings.sh` provisions three provider credentials for the
portal as long-lived, static secrets:

- **ArgoCD**: `ARGOCD_TOKEN` minted via `POST /api/v1/account/portal-main/token` with
  `{"expiresIn":0}` — literally no expiry (line ~487).
- **Keycloak admin**: `KEYCLOAK_ADMIN_CLIENT_SECRET` is a client-credentials secret generated
  once by `ensure_keycloak_client` and written into `narwhal-portal-secrets` (line ~583). The
  *access token* the portal exchanges it for is already short-lived and refreshed at 80% of its
  life client-side (portal-side gap analysis, 2026-09-29) — the secret itself never rotates.
- **APISIX admin key**: `apisix-admin-key` is a static value baked into APISIX's own config via
  `gitops/charts/narwhal-apps/templates/apisix.yaml:208` (`admin_key:`) and read by
  `scripts/cluster/08-1-networking.sh:317`. Rotating it means changing APISIX's own admin-API
  auth, not just the portal's copy.
- **Gitea**: portal#54's gap analysis found no narwhal-side Gitea token mint/injection at all —
  the access path (if any) needs a separate provisioning review before this change can include it.

A leaked or long-retained value here grants standing access with no rotation path short of
re-running cluster bootstrap, which is the least-privilege / rotation gap narwhal-portal#54 calls
out. This repo owns issuance for all four; the portal can only consume what this side provisions
(see narwhal-portal AGENTS.md: portal treats this repo as read-only for provider identity).

## Intent

Every provider credential the portal receives from this cluster has a bounded lifetime, an
explicit rotation mechanism that does not require restarting portal or re-running full cluster
bootstrap, and a documented owner for *how* rotation happens (script re-run, CronJob, sidecar,
provider-native rotation). Where a provider's own admin surface cannot support short-lived
credentials without further work (APISIX today), that limitation is recorded, not hidden behind a
green check.

## Scope

- In scope: ArgoCD account token issuance/renewal; Keycloak admin client secret rotation;
  APISIX admin key rotation feasibility and, if supported without a larger APISIX change, its
  implementation; Gitea token provisioning review (mint or explicitly declare "no portal-facing
  Gitea credential exists" and remove the dead assumption from portal's gap analysis).
- Affected users/systems: `scripts/cluster/13-2-narwhal-portal-bindings.sh`, `08-1-networking.sh`,
  `narwhal-portal-secrets` (multiple writers — see lessons-log on #243/#253), ArgoCD account
  config, Keycloak realm client `narwhal-portal-admin`, APISIX admin API config, any narwhal-portal
  code that reads these secrets (consumer-side changes land in that repo, not here).

## Non-goals

- Kubernetes API credentials (already projected-SA-token based; portal-side typed-error work is
  narwhal-portal#176, already merged).
- Changing what the portal *does* with a credential once obtained (retry/refresh logic lives in
  narwhal-portal).
- Introducing a general secrets-manager (OpenBao already exists and is out of scope for portal's
  provider credentials specifically — the portal's OpenBao *client* credential is already
  projected/short-lived per the 2026-09-29 gap analysis).
- Rewriting APISIX's authentication model beyond what's needed to rotate one static key.

## Requirements

- `REQ-001` — ArgoCD tokens issued to the portal carry a finite `expiresIn` and are renewed before
  expiry without a portal restart.
- `REQ-002` — Keycloak's `narwhal-portal-admin` client secret can be regenerated and propagated to
  the running portal without restarting unrelated portal components.
- `REQ-003` — APISIX admin key rotation is either implemented, or explicitly documented as
  deferred with the concrete blocker (e.g. requires an APISIX config reload path this repo doesn't
  yet have) and a follow-up issue filed.
- `REQ-004` — Gitea's actual credential-provisioning state is determined and documented; if a
  portal-facing Gitea credential should exist but doesn't, provisioning is added; if none is
  required, the assumption in narwhal-portal#54 is corrected there.
- `REQ-005` — No rotation path requires manual `kubectl` intervention as its only recovery; each
  has an idempotent script entry point re-runnable via the existing bootstrap tooling.
- `REQ-006` — Rotation failure is distinguishable from rotation success in script output/logs
  (matches the existing `ARGOCD_TOKEN_OK` pattern at line 517-520 — extend, don't replace).

## Acceptance scenarios

### `AC-001` — ArgoCD token has a bounded lifetime and renews itself

- Covers: `REQ-001`, `REQ-006`
- Given the portal's ArgoCD account token is within a defined renewal window of expiry
- When the rotation mechanism runs (script re-run or scheduled job — decided during implementation)
- Then a new token with a fresh `expiresIn` is minted, verified against the ArgoCD API (same
  pattern as the existing verification at line ~489-501), and written to `narwhal-portal-secrets`
  without deleting keys other writers own (see #243/#253 lessons-log entry on `create|apply`
  erasing sibling keys)

### `AC-002` — Keycloak admin secret rotates without a portal restart

- Covers: `REQ-002`, `REQ-006`
- Given a rotation is triggered for the `narwhal-portal-admin` client secret
- When the new secret is generated via Keycloak's admin API and written to the Secret
- Then the portal's next credential read (per its existing per-call/refresh pattern, not
  module-load caching) picks up the new secret without a pod restart, and the old secret is
  invalidated at Keycloak

### `AC-003` — APISIX key rotation is implemented or its blocker is on record

- Covers: `REQ-003`
- Given APISIX's admin key is defined by its own static config today
- When this change is implemented
- Then either rotation works end-to-end (new key issued, APISIX reloaded, portal secret updated,
  old key rejected), or a filed follow-up issue names the specific missing capability (e.g. no
  APISIX config-reload automation in this repo yet) and this AC is marked `deferred` with that
  issue linked

### `AC-004` — Gitea credential state is resolved, not assumed

- Covers: `REQ-004`
- Given no narwhal-side Gitea token provisioning was found for the portal
- When this change is implemented
- Then either a Gitea token is minted/injected following the same verified-write pattern as
  ArgoCD, or the review concludes none is needed and narwhal-portal#54's gap analysis is corrected
  with a comment linking this decision

## Architecture and decisions

- Relevant ADR/design links: none yet — this Change Package doubles as the design record until
  implementation surfaces something that needs its own ADR.
- ADR threshold result: `not required` — rationale: rotation of existing credential types via
  existing provider APIs, not a new architectural pattern; revisit if APISIX rotation (`AC-003`)
  turns out to need a new reload/control-plane mechanism.
- Alternatives and important trade-offs:
  - **ArgoCD**: mint with a short TTL (e.g. 1h, matching the OpenBao role TTL pattern already used
    at line ~329-340) plus a renewal script entry, vs. a long TTL (e.g. 24h) that reduces renewal
    frequency but widens the exposure window. Lean toward the shorter TTL — renewal is already a
    solved pattern in this script (`ensure_keycloak_client`-style idempotent helpers).
  - **Keycloak**: regenerate-in-place (breaks any credential minted before rotation immediately)
    vs. dual-secret overlap window (old secret valid until a grace period expires). Keycloak's
    admin API supports immediate regeneration only for client secrets AFAIK — an overlap window
    would need application-side handling the portal doesn't have; default to immediate
    regeneration and accept a brief portal-side 401→refresh cycle (already handled per the
    2026-09-29 gap analysis: 403 forces one refresh/retry).
  - **APISIX**: rotating the admin key requires either an APISIX hot-reload of its static config
    (`admin_key` in `apisix.yaml`) or moving to APISIX's own key-rotation feature if the deployed
    version supports it — needs a version/feature check before committing to an approach.

## Change impact

| Area | Impact / evidence needed |
|---|---|
| Source / API / command | `scripts/cluster/13-2-narwhal-portal-bindings.sh`, `08-1-networking.sh`; possible new rotation entry point (script flag or separate script) |
| Dependencies / lockfiles | N/A — bash/kubectl/curl, no new deps expected |
| Runtime / toolchain | ArgoCD/Keycloak/APISIX admin API calls at rotation time; must not require cluster downtime |
| CI / CD | New/extended regression check(s) for rotation idempotency, mirroring `R219`/`R220`-style checks from #243/#253 |
| Release / packaging | N/A |
| Generated output | `narwhal-portal-secrets` gains no new *keys* (existing ones get new values) — verify against every other writer of that Secret (13-2 and 14; see AGENTS.md-adjacent lessons-log) |
| Security / supply chain | This *is* the security change — reduces standing-credential exposure; must not log/print any secret value (matches existing `${#ARGOCD_TOKEN} chars` length-only logging convention) |
| Offline / air-gap | Rotation must work against the in-cluster ArgoCD/Keycloak/APISIX, not an external service — no new air-gap dependency expected |
| Documentation / operations | `docs/common/agent-operational-rules.md` gains an entry if a new durable rule emerges; runbook note on how/when rotation runs (manual re-run vs. scheduled) |
| Portfolio / downstream repositories | narwhal-portal#54 tracks the consumer-side AC; this Change Package is the producer-side prerequisite it's currently blocked on |

## Verification plan

| Acceptance ID | Verification method | Environment | Expected evidence |
|---|---|---|---|
| `AC-001` | Live re-run of the rotation path against the running cluster; verify old token rejected, new token accepted by ArgoCD API | Live 6-VM cluster (vagrant) | Before/after `curl` against ArgoCD API with each token; script log showing verified issuance (matching existing `attempt` retry pattern) |
| `AC-002` | Live rotation; portal pod NOT restarted; portal's next API call succeeds with new secret | Live cluster + running portal pod | Portal request log / `K8sCredentialError`-style typed result showing no manual restart occurred; Keycloak admin API confirms old secret invalid |
| `AC-003` | Either live rotation end-to-end, or documented deferral with linked follow-up issue | Live cluster (if implemented) | Follow-up issue link if deferred; otherwise same before/after pattern as AC-001 |
| `AC-004` | Repository + live review; either a working mint/verify cycle or a recorded "not needed" decision | Static (grep/read) + live if a mint path is added | Comment on narwhal-portal#54 linking the resolution either way |

Static/unit checks alone are insufficient for AC-001/002/003 — this is exactly the class of change
where a green regression suite has previously masked a live-only failure in this repo (see
lessons-log: #243 seam-writer collision, #251 live RBAC gaps, #261 skopeo tag+digest rejection).
Every AC above requires live evidence, not just a new static check.

## Rollout, rollback and recovery

- Rollout sequence: implement and live-verify one provider at a time (ArgoCD → Keycloak → APISIX
  → Gitea, in dependency order — ArgoCD's pattern is the template the others adapt), each as its
  own PR per the existing per-issue PR convention, not one combined change.
- Rollback trigger and procedure: if a rotated credential breaks the portal's provider connectivity
  live, re-run the prior static-provisioning step (the script's existing non-rotating path stays
  available until rotation is proven, per Reversibility — build on what exists rather than
  replacing it outright) to restore a working static credential while the rotation path is fixed.
- Data/configuration recovery: `narwhal-portal-secrets` is namespaced and re-appliable; no data
  loss risk beyond a transient credential mismatch, recoverable by re-running the provisioning
  script.
- Compatibility or migration obligations: none for existing portal versions — rotation is
  transparent to a portal that already re-reads credentials per call (K8s/OpenBao pattern);
  confirm ArgoCD/Keycloak/APISIX client code in narwhal-portal does the same before implementing,
  or that becomes a portal-side prerequisite PR first.

## Evidence and durable synchronization

- Evidence location/format: `.agents/evals/traces/` behavior-contract trace per provider PR (this
  touches `scripts/cluster/**`, which is a high-risk path under `.agents/evals/risk-policy.json`).
- Tests or checks that become durable regression controls: static idempotency checks per provider
  in `scripts/test/regression-check-kakao.sh`, next free R-id per provider PR.
- Documentation to update: this file's Status/Review record as each AC lands; `docs/common/
  agent-operational-rules.md` if a generalizable rule emerges (e.g. "rotation scripts must verify
  against the live provider API before writing the Secret," matching the existing ArgoCD
  verification pattern).
- ADR/evidence/portfolio records to update: narwhal-portal#54 comment thread, cross-linking each
  provider PR as its AC closes.

## Review record

- Accepted scope/requirements: *(pending — this is a Draft; needs explicit acceptance before
  broad implementation per this repo's Class D workflow)*
- Material changes after acceptance and re-review: —
- Open questions or blockers:
  - Does the deployed ArgoCD version support token renewal without deleting/recreating the
    account token object, or does renewal mean issuing a second token and revoking the first?
    (Affects whether `AC-001`'s renewal is atomic or has an overlap window.)
  - What Keycloak admin-API capability actually exists for *secret* rotation vs. just *token*
    refresh — needs a version check against the deployed Keycloak realm before implementation.
  - APISIX admin key rotation feasibility is genuinely unknown pending a look at the deployed
    APISIX version's reload/hot-config capabilities — `AC-003` may resolve to `deferred`.
  - Gitea: is there *any* portal-facing Gitea credential today? The portal-side gap analysis found
    none; confirm before assuming this needs new provisioning rather than a corrected assumption.
