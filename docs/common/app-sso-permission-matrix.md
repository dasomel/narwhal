# Application SSO and Permission Matrix

This matrix records how a user authenticates to each exposed application and where its
authorization is enforced. It separates **SSO authentication** (a valid Keycloak session)
from **application permission** (what the authenticated user can do). A role listed in
Narwhal Portal's tools catalog controls whether a link is shown; it does not by itself
grant or deny access in the target application.

The matrix is derived from the committed configuration referenced in each row. It does
not claim to describe live cluster state. “No group policy found” means the cited GitOps
configuration authenticates users but does not define an app-specific group authorization
rule; verify the application's own ACL and live configuration before treating that as an
access guarantee.

| Application | SSO / Keycloak client | App authorization by Keycloak group | Portal link visibility |
| --- | --- | --- | --- |
| Argo CD | Native OIDC; `argocd` | `cluster-admin` → ArgoCD `admin`; `developer` → sync only in `tenants/*`, plus app get/log get; `viewer` → readonly; `guest` → `role:none`, with an explicit deny for application get. | `cluster-admin`, `developer` |
| Gitea | APISIX OIDC gateway gate + Gitea OAuth2 (`keycloak`, zero-click via `/user/oauth2/keycloak`, D-GITEA-OAUTH) | `cluster-admin` → Gitea site admin (`--admin-group cluster-admin`); `developer` → `narwhal/Developers` team; `viewer` → `narwhal/Viewers` team; `guest` → restricted user (`--restricted-group guest`), re-synced on every OAuth login; team membership is two-way (`--group-team-map-removal`), so a user who leaves `developer` is removed from `narwhal/Developers` on the next login (a member added to `Developers`/`Viewers` by hand without the matching Keycloak group is removed the same way; `Owners` and unmapped teams are untouched). `ACCOUNT_LINKING=auto` with `USERNAME=preferred_username` links an existing unlinked local account of the same username on its first OAuth login and applies the group/admin sync from its second login on. The route has a git/automation bypass (`gitea-git-bypass`) and forwards browser `GET /user/login` to `/user/oauth2/keycloak` (`?direct=1` reaches the local password form for `gitea-admin`). | `cluster-admin`, `developer` |
| Harbor | Native OIDC; `harbor` | Keycloak group `cluster-admin` is configured as Harbor's OIDC admin group. Other Harbor project permissions are managed by Harbor; no other group mapping is configured here. | `cluster-admin`, `developer` |
| Grafana | Native OIDC; `grafana` | `cluster-admin` → Grafana `Admin`; `developer` → `Editor`; all other groups → `Viewer` through `role_attribute_path`. | `cluster-admin`, `developer`, `viewer` |
| Prometheus | APISIX OIDC; `prometheus` | Gateway login is enforced. No group-specific authorization rule appears in the cited route/configuration. | `cluster-admin` |
| Alertmanager | APISIX OIDC; `alertmanager` | Gateway login is enforced. No group-specific authorization rule appears in the cited route/configuration. | `cluster-admin` |
| Headlamp | Native OIDC; `headlamp`; zero-click via the gateway, which sends `/c/<cluster>/login` to `/oidc` (D-HEADLAMP-AUTOLOGIN) | The OIDC token is passed to Kubernetes API; permissions come from Kubernetes RBAC bindings for the token's groups. See the role map below. | `cluster-admin`, `developer` |
| Kubernetes Dashboard | Public OIDC client with PKCE; `kubernetes-dashboard` | The bootstrap exchanges the user's token for Dashboard login; Kubernetes API permissions come from Kubernetes RBAC bindings for the token's groups. See the role map below. | `cluster-admin`, `developer` |
| OpenBao | OIDC via `/sso` (direct root visits without a query string are redirected there); `openbao` | OIDC default role receives `default` policy; external group `cluster-admin` maps to the `cluster-admin` policy. The configured policy grants broad `*` path capabilities. | `cluster-admin` |
| Velero UI | App OAuth plus `/sso` bootstrap (direct `/` without a query string is redirected there); `velero-ui` | The SSO setup configures OAuth, but the cited configuration does not establish a Keycloak-group-to-application-role matrix. | `cluster-admin` |
| Hubble UI | APISIX OIDC; `hubble` | Gateway login is enforced. No group-specific authorization rule appears in the cited route/configuration. | `cluster-admin` |
| NFS Quota | APISIX OIDC; shares the `hubble` client | Gateway login is enforced. The route does not define group-specific authorization. The shared client also means it is not a distinct Keycloak client boundary. | `cluster-admin` |
| Narwhal Portal | Native OIDC; `narwhal-portal` | Portal maps the bare `groups` values to `cluster-admin`, `developer`, `viewer`, or `guest`; route/API checks and resource scope enforce Portal permissions. | Catalog links are separately filtered by each tool's `roles` list. |
| Keycloak | Identity-provider console; no external SSO client | The initial admin console account is provisioned from `iam/keycloak-initial-admin`. The cited setup does not map Narwhal groups to Keycloak console roles; console and realm-management privileges are Keycloak's own roles. | No Portal catalog link |

## Shared group and Kubernetes RBAC mapping

Keycloak emits these bare group names in the `groups` claim. Kubernetes API server adds the
`oidc:` prefix when evaluating RBAC; that prefix is not in the token.

| Keycloak group claim | Kubernetes subject / role | Summary |
| --- | --- | --- |
| `cluster-admin` | `oidc:cluster-admin` → `platform-admin` | Narwhal's platform admin role; it is not the built-in Kubernetes `cluster-admin` role. |
| `developer` | `oidc:developer` → `developer` | Cluster-wide read-only access; workload write access is separately bound within `dev`. |
| `viewer` | `oidc:viewer` → `platform-viewer` | Read-only platform role. |
| `guest` | No Kubernetes binding | Portal-only role; grants no Kubernetes RBAC permissions. |

## Sources of truth

- Group claim and Kubernetes/Portal role contract: [OIDC/RBAC contract](oidc-rbac-contract.md#2-그룹-매핑-표).
- Service inventory and SSO integration patterns: [service domains](../kakao/service-domains.md#2-서비스-도메인).
- Client creation and app configuration: [`11-3-keycloak-clients.sh`](../../scripts/cluster/11-3-keycloak-clients.sh) and [`lib.sh`](../../scripts/common/lib.sh) (`gitea_upsert_keycloak_source`).
- Gitea Helm values and OAuth2 source upsert: [`12-gitea.sh`](../../scripts/cluster/12-gitea.sh).
- Argo CD group policy: [`13-argocd.sh`](../../scripts/cluster/13-argocd.sh).
- Kubernetes role bindings: [`rbac-policies.yaml`](../../gitops/resources/rbac-policies.yaml).
- APISIX gateway authentication routes: [`apisix-routes.yaml`](../../gitops/charts/narwhal-platform/templates/apisix-routes.yaml).
- Grafana role expression: [`prometheus-stack.yaml`](../../gitops/charts/narwhal-apps/templates/prometheus-stack.yaml).
- Portal role mapping and link visibility: `portal/src/lib/auth.ts` and `portal/src/lib/tools.ts` in this repository.

The source files above are authoritative when configuration changes. Update this table in
the same change whenever a client's authentication flow, group mapping, app role, or Portal
link visibility changes.
