# Multi-Cluster Control Plane Credential Export and Readiness

This document is the Narwhal-side first increment for [narwhal#6](https://github.com/dasomel/narwhal/issues/6). It is deliberately a credential and identity contract, not a claim that the present Vagrant/Kakao bootstrap can run a fleet. The Kakao cluster is destroyed, so no live registration, rotation, health aggregation, or three-cluster E2E result exists yet.

## Cluster identity

Every provisioned cluster that will be registered has one operator-selected, stable `CLUSTER_ID`. It is an RFC 1123 label (`[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?`), unique within the Portal registry, and is never inferred from the current kubeconfig context, VIP, DNS name, or provider account. Select it at provision time and record it in the environment/inventory that invokes the cluster bootstrap; for the existing cluster use a deliberate value such as `primary`, not an implicit default.

`CLUSTER_ID` is the Portal `Cluster.id` and determines the non-secret environment-variable names in the export bundle:

| Cluster ID | API-server variable | token variable | CA variable |
| --- | --- | --- | --- |
| `prod-seoul-1` | `K8S_PROD_SEOUL_1_API_SERVER` | `K8S_PROD_SEOUL_1_SA_TOKEN` | `K8S_PROD_SEOUL_1_CA_BUNDLE` |

The additional Portal fields are operator-declared: `name`, `environment` (`production`, `staging`, `development`, or `sandbox`), `provider` (`kakao-cloud`, `aws`, `gcp`, `azure`, `on-prem`, or `other`), and optional `region`. Capability statuses begin as `unavailable` unless a live preflight proves otherwise; an absent integration is not a healthy one.

The eventual durable declaration should be a `narwhal-cluster-identity` ConfigMap in `kube-system`, stamped from these provisioning inputs and labelled with `app.kubernetes.io/part-of: narwhal`. This pass does **not** create it: creating a new GitOps-owned object while its ownership and reconciliation path are undecided would make a static convention look operational. Until that follow-up lands, the provisioner inventory is authoritative and the exporter requires the ID explicitly.

## Export and registration procedure

Run this on an operator workstation or a control-plane host whose current `kubectl` context targets exactly the cluster being exported. The command does not accept an endpoint override, so it cannot silently package another cluster's URL. The active kubeconfig must carry either embedded `certificate-authority-data` or a readable `certificate-authority` file; both are exported and the script never substitutes insecure TLS.

```bash
scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh \
  --cluster-id prod-seoul-1 \
  --cluster-name "Production Seoul 1" \
  --environment production \
  --provider kakao-cloud \
  --region kr-central-2 \
  --output-dir /secure/operator-controlled/prod-seoul-1
```

The exporter verifies the existing `devtools/narwhal-portal` ServiceAccount and its `narwhal-portal` ClusterRoleBinding, then creates a 24-hour TokenRequest token for that same ServiceAccount. It does not apply RBAC, create a second ServiceAccount, create a long-lived Secret, or broaden permissions. The source RBAC is the GitOps manifest `gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`; it remains the single least-privilege definition for the portal's cluster reader.

The output directory is created with `0700`; `credentials.env` and `kubeconfig` are `0600`; `registration.json` is `0644` and contains no credential bytes. The exporter never prints token or CA values. Treat the entire output directory as secret-bearing because `kubeconfig` and `credentials.env` are credentials; keep it out of Git, CI logs, chat, and ticket attachments.

`registration.json` is the body shape for Portal `POST /api/settings/clusters`: it includes `credentialRef.apiServerEnvVar` and `credentialRef.tokenEnvVar`, not their values. The Portal code currently has no CA-bundle field in `ClusterCredentialRef`; `credentials.env` retains the CA bundle for a TLS-verifying credential sink and `kubeconfig` consumes it now. Do not discard the CA or fall back to insecure TLS simply because Portal #21 currently stores only the API-server and token variable names.

Before calling the Portal registration API, an operator must inject the API-server and token values into the Portal deployment's secret store under the names in `registration.json`; inject the CA only once the Portal's per-cluster client accepts its CA variable. That secret-delivery mechanism is intentionally outside this repository's GitOps tree: committing raw values would violate the Portal contract. On registration, use a privileged Portal management session and submit `registration.json`; verify its preflight against this cluster before considering it registered.

### Rotation and removal

For rotation, before the 24-hour TokenRequest expires, export the same `CLUSTER_ID` again, replace the Portal secret-store values atomically, restart or reload the Portal only through its approved deployment path, and run Portal preflight. The Portal #21 registry currently resolves static environment values and has **no automatic renewal path**; this procedure is therefore a manual, operator-owned rotation runbook, not an implemented controller. For emergency invalidation, delete/recreate the ServiceAccount only after coordinating the impact on the local Portal connection. A live rotation run remains unverified.

For removal, first remove the cluster from the Portal registry, remove its secret-store values, then remove its Portal RBAC only when that cluster no longer serves any Portal. Never delete the shared `narwhal-portal` binding from a still-managed cluster merely because another cluster was removed.

## Multi-cluster readiness audit

This is an evidence-based inventory of the current single-cluster footprint. It is not a promise that changing one variable creates a fleet.

| Area | Current assumption | Multi-cluster follow-up |
| --- | --- | --- |
| `scripts/cluster/02-init-cluster.sh` | Bootstrap is explicitly `master-1`; it writes local/admin kubeconfigs, VIP-specific configs, join commands, and one cluster's pod/service CIDRs. | Parameterize cluster identity, topology inventory, endpoint, and CIDRs at provisioning time; stamp the identity ConfigMap after API availability. |
| `scripts/cluster/02-join-worker.sh` | Reads the one join command delivered by the bootstrap control plane. | Feed a selected cluster inventory and per-cluster join artifact; keep workers unable to cross-join. |
| `scripts/cluster/02-join-control-plane.sh` | Consumes one control-plane join command/certificate key and assumes the initial control plane. | Carry per-cluster endpoint/cert artifacts and prohibit accidental reuse across clusters. |
| `scripts/cluster/03-cni-install.sh`, `04-addons.sh`, `06-phase2-start.sh` through `15-narwhal-portal.sh` | Run from one master using `/home/vagrant/.kube/config-local`; the phase order has one platform instance. | Introduce an inventory-driven per-cluster runner, explicit context guard, and a central-vs-member component classification. |
| `scripts/cluster/13-2-narwhal-portal-bindings.sh` | Mints one token into the local Portal secret and hardcodes one `K8S_API_SERVER`/`CLUSTER_NAME`. | Remains the current-cluster bootstrap path; this pass adds an explicit per-cluster exporter instead of changing it into a cross-cluster secret distributor. |
| `scripts/cluster/13-argocd.sh`, `14-gitops-bootstrap.sh` | Configure and push one in-cluster ArgoCD/Gitea installation. | Decide fleet topology (central ArgoCD vs per-cluster ArgoCD) and namespace/project isolation before adding cross-cluster credentials. |
| `gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml` | Defines exactly one local Portal ServiceAccount/ClusterRoleBinding and injects a single global secret. | Keep the RBAC source of truth, but add a dedicated secret-manager or Portal credential delivery mechanism for multiple named env vars; do not put member-cluster tokens in Git. |

The proof-of-concept implementation is the exporter, not a bootstrap rewrite. It lets an operator package cluster N with the exact existing portal RBAC while preserving a non-secret registration record. Parameterizing the full bootstrap, Portal multi-cluster reads, aggregated health/ArgoCD status, tenant scoping, offline fleet replay, and failure isolation require a live multi-cluster environment and are still open.

## Static verification boundary

Static checks establish that the exporter exists, uses restrictive file permissions, references the canonical binding, and preserves Portal credential references rather than secrets. They cannot establish API reachability, CA trust, token authorization, credential rotation, or isolation when one member API is down. Those require the issue's T2/T3 environment with at least three live clusters.
