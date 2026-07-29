# In-cluster image builds with Kaniko (developer self-service)

The narwhal-portal is deployed from a **pinned GHCR image**
(`ghcr.io/dasomel/narwhal-portal:<ver>`) — you do **not** need to build anything to
run the cluster. This guide is for developers who want to build a **custom image
inside the cluster** (e.g. a WIP branch of the portal, or any other app) **without a
local Docker daemon**, and push it to the in-cluster Harbor registry.

Kaniko builds a container image from a Dockerfile **inside a Kubernetes pod** — no
privileged Docker socket, no local Docker needed.

## When to use this

| Situation | Use |
|-----------|-----|
| Just run the released portal | Nothing — ArgoCD pulls `ghcr.io/dasomel/narwhal-portal:<ver>` |
| Build & test a local/WIP portal change in the cluster | Kaniko (`kaniko-build.sh`, below) |
| Build any other app from source, no local Docker | Kaniko (adapt `kaniko-build.sh`) |
| Cut a real release for everyone | Push a `vX.Y.Z` git tag → GitHub Actions publishes to GHCR (see narwhal-portal `.github/workflows/docker-publish.yml`) |

## Prerequisites

- A running narwhal cluster with Harbor up (`08-5-registry.sh` done).
- The `narwhal-portal` source (host, or the repo's `scripts/kaniko-build.sh`).

## Build the portal in-cluster

From the `narwhal-portal` repo:

```bash
cd narwhal-portal
./scripts/kaniko-build.sh          # DOMAIN defaults to local.narwhal.internal
```

`kaniko-build.sh` is idempotent and does:

1. Verify `kubectl` / `git` connectivity.
2. Prepare the Kaniko auth Secret for Harbor (`harbor-kaniko-setup.sh`).
3. Push the portal source to the **in-cluster Gitea** (creates the
   `gitea-admin/narwhal-portal` repo via the Gitea API if missing).
4. Apply the Kaniko **Job** manifest (domain placeholders `sed`-substituted).
5. Wait for the Job (up to 20 min) and report success/failure.

Result: `harbor.local.narwhal.internal/library/narwhal-portal:latest` in Harbor.

Build notes (learned the hard way):
- Kaniko runs with `--compressed-caching=false` at a 4Gi limit — the Next.js
  standalone build OOMKills at 4Gi with compressed caching on.
- The build waits on a **Harbor readiness gate** (`kubectl rollout status`
  harbor-core + harbor-registry) before pushing, to avoid 502 on a mid-restart Harbor.

## Deploy your in-cluster-built image

The default deploy points at GHCR. To run **your** Harbor-built image instead, edit
the portal Deployment image in
`gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`:

```yaml
        - name: narwhal-portal
          image: harbor.local.narwhal.internal/library/narwhal-portal:latest
          imagePullPolicy: Always   # :latest is mutable — force re-pull
```

then push to Gitea (`scripts/gitops/push-to-gitea.sh`) so ArgoCD syncs it. (kubectl
edits get reverted by ArgoCD selfHeal — persistence requires a Gitea push.)

> Revert to the released image by restoring `ghcr.io/dasomel/narwhal-portal:<ver>`
> (`imagePullPolicy: IfNotPresent`) and pushing again.

## Airgap

The Kaniko executor (`gcr.io/kaniko-project/executor`) and `docker.io/alpine/git`
are kept in the airgap bundle (`scripts/airgap/images.txt`) precisely so this
developer build path also works offline. Images **built** in-cluster
(`harbor.local.narwhal.internal/library/narwhal-portal:*`) are intentionally NOT in
the pull-based bundle — they are produced, not pulled.
