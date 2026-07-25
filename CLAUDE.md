# Narwhal - Claude Code Guide

> Vagrant-based Kubernetes Internal Developer Platform (IDP) cluster automated provisioning project

Provisions a full Kubernetes IDP stack (GitOps, SSO, monitoring, storage, backup) onto Vagrant
VMs. Substantive work follows the global `<procedural_completion>` doctrine.

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

- `.vagrant/` is generated — never edit it by hand.
- Every script keeps `set -euo pipefail`. CI runs shellcheck and an indent check, but neither
  catches a missing `set` line, so removing one fails silently.
- **Bitnami images and charts are banned.** Bitnami's commercialization means a tag can vanish
  from under a running cluster. Reach for the upstream official image or an Operator (CNPG for
  Postgres, etc.); an Alpine-based community image is the next fallback. Only when genuinely no
  alternative exists is Bitnami acceptable.
- **Registry preference: `ghcr.io` > `registry.k8s.io` > `quay.io` > `docker.io`.** Docker Hub
  rate-limits anonymous pulls at 100/6h, which is enough to break a full provision run. Custom
  images go to `ghcr.io/dasomel/`. When docker.io is unavoidable, leave a comment saying why.

## Infrastructure Resource Safety

The VMs (sizes in the Vagrantfile) are tight enough that concurrency is the main way to take this
cluster down. Change one thing, confirm it settled — `kubectl top nodes` between steps — then
change the next; 2-3 parallel cluster modifications is the practical ceiling, and simultaneous pod
restarts will OOM a node. Confirm from the user's side (curl the endpoint, resolve the name)
rather than trusting that the apply succeeded.

---
