# Narwhal - Claude Code Guide

> Vagrant-based Kubernetes Internal Developer Platform (IDP) cluster automated provisioning project

Provisions a full Kubernetes IDP stack (GitOps, SSO, monitoring, storage, backup) onto Vagrant
VMs. Substantive work follows the global `<procedural_completion>` doctrine.

---

## Plan Mode Guide (Shift+Tab x2)

Use Plan mode for: adding a new component, major modifications to existing scripts, changing the
GitOps app structure, and version upgrades.

---

## Recurring Rules

> Dated incident history lives in [`docs/common/lessons-log.md`](docs/common/lessons-log.md). Only rules that
> generalize past a single incident belong here — when a new incident yields one, add the rule
> here and the narrative there.

**Recording incidents (not optional)**
- Every fix writes a row to [`docs/common/lessons-log.md`](docs/common/lessons-log.md), in the
  section that matches the cause (Shell / Kubernetes-Helm / GitOps-ArgoCD / Vagrant-Infrastructure
  / Cloud), newest first. A fix nobody wrote down gets rediscovered from scratch — that is what
  this file exists to stop.
- **Mistakes made while fixing count too**, and are often the most useful rows: a guard that
  reported success unconditionally, a config written to a path nothing reads, a remedy documented
  before it was run. Three of the nineteen defects in the 2026-08 clean install were introduced by
  the repair work itself, and each hid the failure it was meant to surface. Record them in the same
  detail as the incident that prompted them — omitting them is how the same trap gets rebuilt.
- **Grep the file for the symptom before adding a row.** If a near-match exists, sharpen that row
  instead of appending a second one; parallel entries for one failure are worse than none, because
  the next reader trusts whichever they hit first.
- Record the **discriminator, not the conclusion**. "Loki CrashLoopBackOff → deleted the WAL" is
  useless; what earns its place is how to tell this cause from the ones it resembles, and which
  obvious fix is wrong (a healthy Loki also holds a 0-byte WAL segment, so deleting on size alone
  causes the corruption it was meant to prevent).
- A rule only graduates into the Recurring Rules above once it has bitten across more than one
  incident. One-off narratives stay in the log.

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
- Under `set -o pipefail` a pipeline aborts the script two ways, and `|| true` only covers
  one. A non-matching `grep` exits 1 — end the substitution with `|| true`. But a command
  that closes the pipe early *after succeeding* (`head -1`, `awk '{print; exit}'`) makes the
  still-writing upstream take SIGPIPE, which pipefail reports as failure (rc=141): `head -1`
  is a cause here, not a cure. Select the first match without exiting —
  `awk '$3 ~ v && !seen {print $3; seen=1}'` — or wrap the whole thing in `|| true`.
  Signature: the script dies with no message at all, immediately after a pipeline.
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

## Guardrails

- Freely editable: shell scripts under `scripts/`, YAML under `gitops/apps/` and
  `gitops/resources/`, `Vagrantfile` settings, and docs (`README.md`, `docs/`).
- `.vagrant/` is generated — never edit it by hand.
- Never hardcode a password, token, or kubeconfig credential into a script or manifest. Nothing
  in CI catches one.
- Every script keeps `set -euo pipefail`. CI runs shellcheck and an indent check, but neither
  catches a missing `set` line, so removing one fails silently.
- Shell and YAML both indent 2 spaces (CI blocks the shell case). `ENV_VAR` for environment
  names, `local_var` for locals.
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
