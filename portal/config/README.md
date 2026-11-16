# `role-filter.json`

What a signed-in user may see. Two mechanisms, and one of them is on its way out.

## The label is the source of truth

A namespace says who owns it:

```yaml
metadata:
  labels:
    narwhal.io/team: platform-team
```

The portal reads that label; the cluster reads the same label in the RoleBinding
each tenant file ships (`narwhal/resources/tenants/`). The portal writes it when a
namespace is requested. One fact, on the object being governed.

`teamMappings[].namespaces` is the **migration path**, not the model. Namespaces
created before the tenant flow carry no label, so the patterns still resolve them —
`resolveNamespaceScope` accepts either and records which one admitted each name.

### Migrating a team

1. Label its namespaces: `kubectl label ns <name> narwhal.io/team=<group>`
   — in the GitOps repo, not with `kubectl`, or ArgoCD's selfHeal reverts it.
2. Confirm the portal still shows them. `/api/my-apps` returns `scope.namespaces`,
   which is the resolved list.
3. Delete that team's `namespaces` array here. `argocdProjects` stays — ArgoCD
   projects are not namespaces and have no label to read.

Nothing needs a deploy in between: both sources resolve at every request.

## The shipped values are a sample

`frontend-team` maps `frontend-*`, and no such namespace exists in this platform.
Treat the file as an example to replace, not as configuration to preserve.

## Unmapped non-admin users fail closed

Visibility is resolved in code (`D-PORTAL-UNMAPPED-SCOPE`, narwhal#659;
`D-PORTAL-UNMAPPED-DENY`, follow-up): `cluster-admin` sees everything; other
users see matching `teamMappings` patterns and namespaces owned by their teams
through `narwhal.io/team`. Without either, non-admin users (including users with
no team claims, `guest`, or unrecognized groups) are redirected to
`/access-denied` without rendering portal navigation and receive `403`
(`code: "ACCESS_NOT_GRANTED"`) from data APIs until a platform administrator
adds a team mapping or namespace ownership label (label changes propagate within
the 30-second `k8s:namespaces` Valkey cache window).

If the Kubernetes namespace list cannot be read, label-only teams are denied (fail closed) until it recovers.

The shipped `roleDefaults` values remain unchanged (including developer/viewer
`namespaces: ["*"]`) for configuration compatibility and diagnostics. They no
longer grant non-admin visibility, even when explicitly set to a non-empty scope.
Changing these values cannot bypass the unmapped-team policy.

Write authorization remains separate: `getUserScope` uses only static mappings;
ArgoCD mutation authorization also checks namespace ownership. Visibility scope
must not be reused to grant writes.

## Shape

| Key | Meaning |
|---|---|
| `roleDefaults.<role>` | Retained configuration/diagnostic values; ignored by non-admin visibility resolution |
| `teamMappings[].group` | Keycloak group, matched against the user's teams |
| `teamMappings[].namespaces` | Legacy patterns. `*` suffix is a prefix match; `*` alone is everything |
| `teamMappings[].argocdProjects` | ArgoCD projects the team may see. No wildcard-by-label equivalent |

Precedence: `cluster-admin` sees everything; otherwise matching static mappings
plus team-owned namespace labels; otherwise nothing (fail-closed).
