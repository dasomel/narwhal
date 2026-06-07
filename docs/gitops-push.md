# GitOps Push Runbook — narwhal → Gitea → ArgoCD

How to make a change to a GitOps-managed resource actually take effect on the
live cluster. Use this whenever you edit anything under `gitops/` (apps or
resources) and need ArgoCD to apply it.

## Why a plain `kubectl apply` or local commit is NOT enough

ArgoCD watches the **in-cluster Gitea repo**, not this GitHub repo and not your
local working copy:

```
repoURL: http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git
path:    resources   (and a second app for path: apps)
sync:    automated { prune: true, selfHeal: true }
```

Consequences of `selfHeal: true`:

| Action | Result |
|--------|--------|
| `kubectl apply` / `kubectl patch` a managed resource | ⚠️ **Reverted** by ArgoCD selfHeal within minutes |
| Edit `gitops/**` + local `git commit` (no push) | ❌ ArgoCD never sees it (reads Gitea, not local/GitHub) |
| **Push the change into Gitea** | ✅ ArgoCD syncs it — durable |

> Recorded mistake (`CLAUDE.md`): *"ArgoCD selfHeal reverts kubectl apply changes —
> Must push to Gitea repo for persistence."*

## Structure mapping (do not get this wrong)

The Gitea `narwhal-gitops` repo's **root** is the **contents of `gitops/`**:

```
narwhal repo            gitea narwhal-gitops repo (ArgoCD root)
---------------------   ---------------------------------------
gitops/apps/*       ->  apps/*          (app-of-apps, path=apps)
gitops/resources/*  ->  resources/*     (path=resources)
```

So `gitops/resources/narwhal-portal-k8s.yaml` becomes `resources/narwhal-portal-k8s.yaml`
in Gitea. **Never** `git push` this whole repo to the `gitea` remote — the
`gitops/` prefix would not match ArgoCD's `path: resources`.

## The procedure (what the script automates)

1. Resolve the Gitea admin password from the cluster secret
   (`devtools/gitea-admin` → `admin-password`).
2. `kubectl port-forward svc/gitea-http -n devtools 13000:3000` (background).
3. `git clone` the `narwhal-gitops` repo fresh into a temp dir.
4. Copy the changed file(s) from `gitops/<rel>` → `<rel>` (root) in the clone.
5. `git commit` + `git push origin HEAD:main` to Gitea.
6. Annotate the ArgoCD Application with `argocd.argoproj.io/refresh=hard` so it
   re-reads Gitea immediately; `automated` sync then applies it.

## Usage

```bash
# Push only specific file(s) — recommended (minimal blast radius):
ARGOCD_APP=narwhal-portal scripts/gitops/push-to-gitea.sh \
  "fix(portal-rbac): grant metrics.k8s.io read" \
  resources/narwhal-portal-k8s.yaml

# Mirror everything under gitops/ (use sparingly):
scripts/gitops/push-to-gitea.sh "chore: sync all gitops"
```

Env overrides: `ARGOCD_APP` (Application to refresh, default `narwhal-portal`),
`GITEA_LOCAL_PORT` (default `13000`).

## Verify it actually applied

```bash
# ArgoCD picked it up
kubectl -n devtools get application <app> -o jsonpath='{.status.sync.status}{"\n"}'

# The resource reflects the change (example: portal RBAC)
kubectl get clusterrole narwhal-portal \
  -o jsonpath='{range .rules[?(@.apiGroups[0]=="metrics.k8s.io")]}{.resources} {.verbs}{"\n"}{end}'

# End-to-end from the consuming pod's own SA token
kubectl -n devtools exec deploy/narwhal-portal -- sh -c \
  'wget -q -O /dev/null -S --header="Authorization: Bearer $K8S_SA_TOKEN" \
   --no-check-certificate "$K8S_API_SERVER/apis/metrics.k8s.io/v1beta1/nodes" 2>&1 \
   | grep "HTTP/" | tail -1'   # expect: HTTP/1.1 200 OK
```

## Worked example — portal Architecture page showed blank CPU/memory (2026-06-07)

- **Symptom:** node CPU/memory blank/0% on the portal Architecture page; pods,
  status, version all fine.
- **Root cause (two layers):**
  1. `metrics-server` liveness probe too tight (`timeoutSeconds:1`) → flapping →
     `metrics.k8s.io` APIService `MissingEndpoints` → fixed in
     `scripts/cluster/04-addons.sh` (probe loosened).
  2. ClusterRole `narwhal-portal` lacked `metrics.k8s.io` → portal SA got **403**
     on the metrics API (cluster-admin kubectl worked, but the portal pod uses
     its own SA). Fixed in `gitops/resources/narwhal-portal-k8s.yaml`.
- **Activation:** pushed the RBAC change to Gitea with this script →
  ArgoCD selfHeal applied in ~5s → portal SA `metrics.k8s.io` → **200 OK**.
