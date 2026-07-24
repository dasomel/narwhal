# Narwhal - Claude Code Guide

> Vagrant-based Kubernetes Internal Developer Platform (IDP) cluster automated provisioning project

## Quick Overview

An infrastructure project that automatically provisions a complete Kubernetes IDP stack (GitOps, SSO, Monitoring, Storage, Backup) using Vagrant VMs in a local environment.

> **Working procedure:** follow the global `<procedural_completion>` doctrine (`~/.claude/CLAUDE.md`) on substantive tasks — goal → decompose → execute → verify → risk (five principles + completion gate + escalation). Trivial one-shots answer directly.

---

## Plan Mode Guide (Shift+Tab x2)

When Plan mode is needed in Narwhal:
- Adding a new component
- Major modifications to existing scripts
- Changing GitOps app structure
- Version upgrades

---

## Recurring Rules

> Dated incident history lives in [`docs/lessons-log.md`](docs/lessons-log.md). Only rules that
> generalize past a single incident belong here — when a new incident yields one, add the rule
> here and the narrative there.

**Images & registries**
- Never trust a mutable tag on the pre-baked Vagrant box: `:latest` layers are baked into the
  box's content store and are never re-fetched, so an arm64 node silently runs amd64 binaries.
  Pin immutable version tags.
- ArgoCD syncs on a manifest *diff*, so a re-published `:latest` is byte-identical and never
  redeploys. Upgrade = bump the tag. A tag bump must also update `scripts/airgap/images.txt`.
- Distroless images (`registry.k8s.io/kubectl`, etcd, kube-vip) have no shell — use
  `docker.io/alpine/k8s` or call the binary via `kubectl exec -- <bin>` directly.
- "Is version X actually running?" is answered by the app's own version API only — never inferred
  from manifest arch support or chart appVersion.

**GitOps / ArgoCD**
- selfHeal reverts `kubectl apply`; persist changes by pushing through
  `scripts/gitops/push-to-gitea.sh`. Never push the narwhal repo directly to the gitea remote
  (the trees differ — gitea root = contents of `gitops/`), and never embed credentials in a
  remote URL.
- App Synced but live differs from git → grep `argocd-cm`
  `resource.customizations.ignoreDifferences` before blaming sync machinery.

**Istio ambient**
- ztunnel HBONE corrupts SSO/web cookies and breaks plain-HTTP kubelet probes: opt those
  workloads out with the `istio.io/dataplane-mode: none` pod label — and opt out their backing
  services too (e.g. argocd-redis), or STRICT mTLS drops the non-mesh clients.

**Kyverno**
- Enforce `validate` patterns must wrap optional fields in `=()` anchors, otherwise every pod
  that simply omits the field is denied at admission.
- Enforce bites on the *next* create, not on running pods. Before upgrading or rescheduling a
  privileged / hostPID / hostNetwork / hostPort workload, confirm its namespace is in the
  policy's `exclude` list — a running pod is not proof the policy allows it.

**Editing manifests & values**
- Edit YAML with `yq`, never `sed`.
- Before adding a key to a Helm values block, grep the block and MERGE into it — duplicate map
  keys resolve last-wins silently. Validate with `helm template`, not YAML syntax alone.
- `--set` on a boolean needs `--set-string`.

**Shell**
- Under `set -o pipefail` a non-matching `grep` aborts the script — use `... | head -1 || true`.
- Send function logs to `>&2`; `$(func)` captures stdout and will otherwise absorb log lines
  into secret variables.

**Keycloak**
- The Operator (26.6.x) drops resources set under `unsupported.podTemplate` — put CPU/memory in
  first-class `Keycloak.spec.resources`. Probe overrides there ARE honored.
- It creates a StatefulSet: `kubectl exec -n iam keycloak-0 -c keycloak`, not `deploy/keycloak`.
- Every OIDC client needs an audience mapper, or its `aud` claim carries only `["account"]`.

---

## Core Flows

- Provisioning order is encoded in filename prefixes: `scripts/common/0*.sh` then `scripts/cluster/00-15*.sh` (Phase 1 = cluster infra, Phase 2 = platform apps; `scripts/up.sh` is the SOLE Phase 2 driver — bare `vagrant up` does not auto-run Phase 2).
- GitOps apps: `gitops/charts/narwhal-apps/templates/` (+ `gitops/charts/narwhal-platform/` for domain-bearing resources); raw manifests in `gitops/resources/`; app-of-apps root at `gitops/apps/app-of-apps.yaml`.

## Development Commands

Standard `vagrant` workflow (`up/halt/destroy/ssh`); non-obvious bits only:

```bash
# Run Phase 2 (platform apps) manually — bare `vagrant up` does NOT auto-run it
vagrant provision master-1 --provision-with phase2-platform
```

Ralph loop: `/ralph` (OMC) with `.claude/templates/PROMPT.md`. Project slash commands live in `.claude/commands/`.

## Permissions

### Allowed Operations
- Modify shell scripts in scripts/ folder
- Modify YAML files in gitops/apps/, gitops/resources/
- Change Vagrantfile settings
- Update documentation (README.md, docs/)

### Forbidden Operations
- Do not directly modify .vagrant/ folder
- Do not hardcode sensitive information (passwords, tokens)
- Do not remove `set -euo pipefail` from shell scripts
- **Bitnami images/charts are banned** (exception only when absolutely no alternative exists)
  - Risk of image deletion/inaccessibility due to Bitnami commercialization
  - DB: Use official images or Operators (CloudNative-PG, etc.)
  - Other: Prefer upstream official images, Alpine-based community images as fallback
- **Minimize docker.io (Docker Hub) usage** (allow only when no alternative exists)
  - Rate limit issues (anonymous 100pulls/6h, authenticated 200pulls/6h)
  - Registry priority: `ghcr.io` > `registry.k8s.io` > `quay.io` > `docker.io`
  - Use `ghcr.io/dasomel/` for custom images
  - When docker.io usage is unavoidable, add a comment explaining the reason

## Code Style

- **Shell Script**: `set -euo pipefail` required, 2 spaces indentation (CI blocks both:
  shellcheck + the indent check in `.github/workflows/lint.yml`)
- **YAML**: 2 spaces indentation
- **Variable names**: ENV_VAR (environment), local_var (local)
- **Filenames**: Numeric prefix for execution order (00-, 01-, ...)

## Infrastructure Resource Safety

- **Limit parallel cluster modifications to 2-3 max** -- concurrent operations cause OOM in Master 4GB / Worker 6GB environment
- Do not restart/modify multiple pods simultaneously -- modify one -> verify stability -> next modification
- Check resource availability with `kubectl top nodes` between cluster modification tasks before proceeding
- After applying infrastructure/cluster changes, **verify from actual user perspective** (e.g., curl endpoint, kubectl exec test, DNS resolve)

---
