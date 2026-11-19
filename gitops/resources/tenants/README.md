# Tenant namespaces

One file per requested namespace, grouped by team:

```text
resources/tenants/<team>/<namespace>.yaml
```

**Nothing here is written by hand in normal operation.** The portal opens a pull
request against this directory when a developer requests a namespace; a human
approves it; ArgoCD applies it. That path exists because the portal's Gitea
identity (`portal-gitops`) is deliberately absent from the `main` push whitelist,
so a pull request is the only way it can change this repository.

## What a tenant file must contain

- a `Namespace` carrying `narwhal.io/team: <team>` — this label is the source of
  truth for ownership. The portal filters its views by it and the RoleBinding
  below is what turns it into actual permission.
- a `RoleBinding` granting `developer-workload-admin` to `oidc:<team>` in that
  namespace. The cluster-wide `developer` role is read-only by design (see
  `resources/rbac-policies.yaml`), so without this binding the team can see the
  namespace and change nothing in it.
- a `ResourceQuota`. A namespace with no quota can starve the others; `dev` is
  the worked example.
- optional `narwhal.io/otlp-ingest: "true"` on the `Namespace` labels if workloads in
  that namespace need to send OTLP traces (`4317`/`4318`) to `k8s-monitoring-alloy-logs`.
  `alloy-logs-ingress-policy` blocks OTLP ingest from un-opted namespaces so arbitrary pods
  cannot flood the shared log collector (see `docs/common/unified-observability-contract.md`).

## Removal is deletion of the file

The Application syncing this directory runs with `prune: true`, so deleting a
file deletes the namespace and everything in it. That is the offboarding path,
and the reason it is safe enough to leave on is that `main` requires a reviewed
pull request — the destructive step cannot happen without someone approving it.
