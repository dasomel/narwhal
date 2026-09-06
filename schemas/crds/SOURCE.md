# Vendored CRD schemas

Source: https://github.com/datreeio/CRDs-catalog
Pinned commit: `866b2653a5334db9aed20ad74701e20fd464471b`
Fetch date: 2026-09-06

These schemas back the `kubeconform` CRD validation step in
`.github/workflows/lint.yml` (job `kubeconform`). They replace fetching from the
mutable `main` branch at CI runtime with a vendored, integrity-checked copy, and
allow removing `-ignore-missing-schemas` so a genuinely missing schema fails the
job instead of silently skipping validation.

Layout mirrors the upstream catalog so the `-schema-location` template stays
`schemas/crds/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json`
(kubeconform lowercases `{{.ResourceKind}}` when rendering this template —
verified locally: fetching from the upstream mutable URL with `-verbose`
resolves every non-core kind below with 0 skipped).

Derived from `yq eval '.apiVersion + " " + .kind' gitops/resources/*.yaml | sort -u`
(core Kubernetes kinds — NetworkPolicy, Namespace, ResourceQuota, LimitRange,
ServiceAccount, ClusterRole(Binding), Role(Binding), CronJob, ConfigMap, Secret,
Service, PodDisruptionBudget — are covered by `-schema-location default` and
need no vendored file).

## Files (sha256)

| File | sha256 |
|------|--------|
| `monitoring.coreos.com/alertmanagerconfig_v1alpha1.json` | `c2446ba14cdae46c7c3169eb5d10568363aaf1325d94811bd026688e5eced6ee` |
| `monitoring.coreos.com/podmonitor_v1.json` | `a33fd47f73be948090327c6d2076f4739217111e1caf8a2999a5562c59eade9c` |
| `monitoring.coreos.com/prometheusrule_v1.json` | `9cd71d75b714b541a0be960b46bd961dbf1f0106071381cd88f54dfe80b8bd9d` |
| `argoproj.io/appproject_v1alpha1.json` | `5b3f7b75f59e5821d06cedc6b0667b3f5a0d5efd2c9b6cb606cd4da40755a121` |
| `postgresql.cnpg.io/scheduledbackup_v1.json` | `975bdd33e518a9bf45c50703cff4c6843daa62d68fc5ea164f69c4618d344657` |
| `postgresql.cnpg.io/cluster_v1.json` | `b58695a447c9be7cc5e49faeba0280ec13a34a7b067ce26ed9f8d616717df960` |
| `kyverno.io/clusterpolicy_v1.json` | `c9909052d5f3f48972d1b3474c482a59b074721452c1f82494e149703e6de7c1` |
| `aquasecurity.github.io/clustercompliancereport_v1alpha1.json` | `595a939e9cc33872e04f8555c383152e3198c9ed3eeb3384f7ce4ba03d5b12e6` |
| `metallb.io/ipaddresspool_v1beta1.json` | `af960060185517d3478fd46103709e8864885f06ab97e6bb34ce103b25fd5c41` |
| `metallb.io/l2advertisement_v1beta1.json` | `b46a5b24012be1d5486efc25d75fefed39b40b325a11c178708538fca0dac27f` |

## CRD kinds found vs. vendored

All CRD kinds present in `gitops/resources/*.yaml` have a catalog schema — no
kind was left without a schema, so no minimal permissive schema was needed and
`-ignore-missing-schemas` was safely removed.

`gitops/resources/gitea-db.yaml` and `harbor-db.yaml` are comment-only
(deprecated placeholders) and contain no resources.

## Refresh procedure

1. Re-derive the CRD kind list: `yq eval '.apiVersion + " " + .kind' gitops/resources/*.yaml | sort -u`.
2. Get the current `main` HEAD SHA: `curl -fsSL https://api.github.com/repos/datreeio/CRDs-catalog/commits/main | jq -r .sha`.
3. For each `{group}/{kind}` (kind lowercased) not covered by `-schema-location default`, fetch
   `https://raw.githubusercontent.com/datreeio/CRDs-catalog/<sha>/<group>/<kind>_<version>.json`
   into `schemas/crds/<group>/<kind>_<version>.json`.
4. Update the sha256 table and pinned commit/date above.
5. Re-run the exact `kubeconform` command from `.github/workflows/lint.yml` and confirm
   `Skipped: 0`.
