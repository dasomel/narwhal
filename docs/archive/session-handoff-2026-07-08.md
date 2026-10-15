# IDP Session Handoff — 2026-07-08 (COMPLETE)

This work arc is **DONE**. narwhal pushed to public GitHub. No pending action.

## ✅ Shipped
- **narwhal → public GitHub** `github.com/dasomel/narwhal`, `main` @ **6cb7c9a** (fast-forward from 24bc758). Verified remote == local HEAD.
- **Portal → GHCR**: deploys from pinned `ghcr.io/dasomel/narwhal-portal:1.0.0`; Kaniko demoted to optional dev tool. Validated iter19 + iter20 (GHCR pull, no in-cluster build).
- **Harbor hardening** (commits 0e9a1fd + 6cb7c9a):
  - Externalized 3 plaintext shared secrets → `harbor-shared-secrets` Secret (existingSecret refs; 08-5 generate-if-not-exists, 16-char secretKey).
  - Pinned all harbor images `:latest`→`:v2.15.1` (exporter `:v2.15.0-build.32`; `metrics.enabled=false` → exporter not deployed).
  - Root cause of the arm64 `exec format error`: the `ubuntu-26.04-xfs` box bakes stale amd64 `:latest` harbor layers into containerd; a mutable tag is never re-fetched. Immutable pin forces a real arm64 pull. Recorded in narwhal/CLAUDE.md mistakes log (2026-07-07).
  - **v2.15.2 NOT usable**: no official Helm chart yet (latest chart 1.19.1 = app 2.15.1). The redis-photon→valkey-photon swap is a chart-level change awaiting a 2.15.2 chart.
- **Git history purged** (`git filter-repo`) of the 3 plaintext secrets before the public push — 0 occurrences in all history.

## ✅ iter20 clean-install validation (fully clean)
- 6/6 nodes Ready (master-2 present). 0 unhealthy pods (excl transient trivy scan).
- harbor: all pods Running on `:v2.15.1`, no `exec format error`; harbor came up USING the externalized `harbor-shared-secrets` (secretKey/secret/JOBSERVICE_SECRET) → externalize validated end-to-end. exporter not deployed.
- portal: 1/1, `ghcr.io/dasomel/narwhal-portal:1.0.0`, no Kaniko build.
- headlamp: 1/1 Running (self-healed after 11-3 created its OIDC secret).

## Open items (non-blocking, user-deferred)
- `02-containerd.sh` has NO apt-lock wait guard → a node can drop out during containerd install (master-2 hit this in iter19); up.sh self-heals via its per-attempt `vagrant provision` loop.
- `headlamp-oidc-secret "not found"` is a benign 08-3→11-3 ordering transient that self-heals (11-3 creates the secret after Keycloak is up + restarts headlamp). A permanent fix = defer headlamp deploy until after 11-3; left as-is per user.
