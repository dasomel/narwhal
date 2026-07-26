# Narwhal GitOps

> 🇰🇷 한국어: [README_ko.md](./README_ko.md)

This repository is the GitOps source of truth for the **Narwhal Kubernetes Internal Developer Platform (IDP)**. Every platform component running on the cluster — networking, service mesh, observability, storage, security, identity, and the developer portal itself — is declared here as Kubernetes/Helm manifests and reconciled continuously by ArgoCD using an app-of-apps pattern. Nothing is meant to be applied by hand: if it isn't in this repo, it doesn't run on the cluster (and if you `kubectl apply` a change directly, ArgoCD's `selfHeal` will revert it).

> This directory mirrors 1:1 to the in-cluster Gitea repository `narwhal-gitops` (`http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git`), which is the actual repo ArgoCD watches.

---

## Table of Contents

- [Repository Layout](#repository-layout)
- [How It Deploys](#how-it-deploys)
- [Components](#components)
  - [Networking](#networking)
  - [Service Mesh](#service-mesh)
  - [Observability](#observability)
  - [Storage & Backup](#storage--backup)
  - [Security](#security)
  - [IAM & SSO](#iam--sso)
  - [Registry & Developer UI](#registry--developer-ui)
  - [Portal](#portal)
- [Conventions](#conventions)

---

## Repository Layout

```text
gitops/
├── apps/
│   └── app-of-apps.yaml           # Root ArgoCD Application (the single entry point)
├── charts/
│   ├── narwhal-apps/              # One ArgoCD Application per upstream Helm chart
│   │   └── templates/
│   │       ├── metallb.yaml, apisix.yaml, cert-manager.yaml        # networking
│   │       ├── istio-base.yaml, istiod.yaml, istio-cni.yaml, ztunnel.yaml  # service mesh
│   │       ├── prometheus-stack.yaml, loki.yaml, tempo.yaml, k8s-monitoring.yaml  # observability
│   │       ├── seaweedfs.yaml, openbao.yaml, velero.yaml, velero-ui.yaml  # storage/backup
│   │       ├── trivy-operator.yaml, kyverno.yaml                  # security
│   │       ├── harbor.yaml, headlamp.yaml                         # registry/UI
│   │       ├── falco.yaml                                         # DISABLED (see file header)
│   │       ├── narwhal-platform.yaml                               # meta-app -> charts/narwhal-platform
│   │       └── ghost-pod-reaper.yaml, openbao-unseal.yaml, istiod-pdb.yaml,
│   │           istio-telemetry-monitors.yaml, headlamp-policy.yaml # small support Applications
│   └── narwhal-platform/          # Platform-owned raw manifests (one chart, six templates)
│       └── templates/
│           ├── keycloak-cr.yaml           # Keycloak CR + login theme ConfigMap
│           ├── narwhal-portal-k8s.yaml    # Portal Deployment/Service/RBAC/Valkey
│           ├── apisix-routes.yaml         # ApisixRoute/ApisixUpstream definitions
│           ├── apisix-infra.yaml          # APISIX's own etcd + supporting infra
│           ├── istio-ambient-policies.yaml# PeerAuthentication (mTLS) policies
│           └── argocd-config.yaml         # ArgoCD OIDC/RBAC/diff-ignore configuration
└── resources/                     # Extra raw-YAML resources referenced by "small support" apps above
    ├── keycloak-theme/            # Custom Narwhal Keycloak login theme assets
    ├── network-policies.yaml, rbac-policies.yaml, kyverno-policies.yaml, ...
    └── ...
```

`apps/` holds the single root Application. `charts/narwhal-apps` is a thin Helm chart whose only job is to template out one ArgoCD `Application` per platform component (mostly pointing at third-party Helm charts). `charts/narwhal-platform` is a second Helm chart that templates the manifests Narwhal itself owns and authors (Keycloak CR, the portal, APISIX routing, mesh policy, ArgoCD config) — these aren't upstream charts, they're first-party YAML. `resources/` holds extra standalone YAML (NetworkPolicies, RBAC, Kyverno policies, the Keycloak theme, etc.) that small "support" Applications in `narwhal-apps` point at via `directory.include`.

---

## How It Deploys

ArgoCD bootstraps from a single root Application, `idp-apps`, defined in `apps/app-of-apps.yaml`. That Application points at `charts/narwhal-apps`, which renders one child `Application` per platform component — including `narwhal-platform`, itself a meta-app that renders the six `charts/narwhal-platform` manifests as its own children. The result is a three-level app-of-apps tree:

```
idp-apps (root, apps/app-of-apps.yaml)
 └─ narwhal-apps (chart, one Application per platform component)
     ├─ metallb, apisix, cert-manager, istio-*, prometheus-stack, loki, tempo,
     │  k8s-monitoring, seaweedfs, openbao, velero, velero-ui, trivy-operator,
     │  kyverno, harbor, headlamp, (falco — disabled), + small support apps
     └─ narwhal-platform (chart, renders raw platform manifests)
         └─ keycloak-cr, narwhal-portal-k8s, apisix-routes, apisix-infra,
            istio-ambient-policies, argocd-config
```

All Applications use `syncPolicy.automated` with `prune: true` and `selfHeal: true`. **This means any change made directly with `kubectl` (or via the ArgoCD UI's "Edit") will be reverted automatically** the next time ArgoCD reconciles — the only durable way to change cluster state is to edit the YAML in this repo and push it to the Gitea remote. There is no local dev loop that bypasses Git.

---

## Components

### Networking

#### MetalLB
| Chart | Version | Namespace |
|---|---|---|
| `metallb` (metallb.github.io) | `0.16.1` | `platform-system` |

MetalLB provides bare-metal LoadBalancer IP address allocation (LB IPAM) for a Vagrant/on-prem cluster that has no cloud provider to hand out external IPs. It assigns the platform's single external VIP (`192.168.56.200`) to the APISIX gateway Service, which is how every `*.local.narwhal.internal` hostname becomes reachable from outside the cluster. Without it, `type: LoadBalancer` Services would stay `<pending>` forever.

#### APISIX
| Chart | Version | Namespace |
|---|---|---|
| `apisix` (charts.apiseven.com) | `2.13.0` | `platform-system` |

Apache APISIX is the platform's single API gateway / ingress controller. Every external hostname (Keycloak, ArgoCD, Grafana, Harbor, the portal, OpenBao, velero-ui, Hubble, Gitea, and more) is routed through it via `ApisixRoute`/`ApisixUpstream` CRDs (defined in `apisix-routes.yaml`), which the bundled `apisix-ingress-controller` (v1.8.0) syncs to the APISIX admin API backed by its own etcd (`apisix-infra.yaml`). It terminates TLS, injects the `openid-connect` plugin for OIDC-protected apps, and trusts the internal Narwhal root CA for backend TLS verification.

#### cert-manager
| Chart | Version | Namespace |
|---|---|---|
| `cert-manager` (charts.jetstack.io) | `v1.20.2` | `platform-system` |

cert-manager automates issuance and renewal of TLS certificates cluster-wide via `ClusterIssuer`/`Certificate` CRDs, backing the internal Narwhal root CA and the wildcard certificate used for `*.local.narwhal.internal`. It's deployed with CRDs enabled and Prometheus metrics on; ArgoCD is configured to ignore drift on its webhook `caBundle` fields since `cainjector` mutates those at runtime.

### Service Mesh

All four components below are pinned to Istio **1.30.1** and share the `istio-system` namespace, running in **ambient mode** (no sidecars).

#### Istio Base
| Chart | Version | Namespace |
|---|---|---|
| `base` (istio-release GCS charts) | `1.30.1` | `istio-system` |

Installs Istio's CRDs (VirtualService, PeerAuthentication, etc.) and cluster-wide RBAC — a prerequisite for every other Istio component. Synced first (`sync-wave: -10`).

#### Istiod
| Chart | Version | Namespace |
|---|---|---|
| `istiod` (istio-release GCS charts) | `1.30.1` | `istio-system` |

The Istio control plane — configuration distribution, certificate issuance for mTLS, and service discovery for the mesh. Runs the `ambient` profile with 2 replicas and pod anti-affinity for HA, tolerating control-plane taints so it can run on the (tainted) masters.

#### Istio CNI
| Chart | Version | Namespace |
|---|---|---|
| `cni` (istio-release GCS charts) | `1.30.1` | `istio-system` |

A CNI plugin (chained after Cilium) that redirects pod traffic into the ambient mesh data plane without sidecar injection. Configured with `cni.exclusive=false` so it coexists with Cilium instead of overwriting its CNI config.

#### ztunnel
| Chart | Version | Namespace |
|---|---|---|
| `ztunnel` (istio-release GCS charts) | `1.30.1` | `istio-system` |

The per-node ambient dataplane proxy (DaemonSet) that transparently secures pod-to-pod traffic over mTLS/HBONE (port 15008) for every mesh-enrolled namespace — the actual traffic-handling component of Istio ambient mode, as opposed to sidecar Envoy.

### Observability

#### Prometheus Stack
| Chart | Version | Namespace |
|---|---|---|
| `kube-prometheus-stack` (prometheus-community) | `86.2.3` | `monitoring` |

The metrics backbone — Prometheus (7-day retention, `nfs-csi`-backed PVC), Alertmanager, Grafana, and the full suite of ServiceMonitor/PrometheusRule CRDs. `kubeProxy`/`kubeEtcd` component monitors are disabled (Cilium replaces kube-proxy; etcd metrics aren't exposed on this cluster), and `ruleSelector`/`ruleNamespaceSelector` are wide open so any namespace can ship its own `PrometheusRule`.

#### Loki
| Chart | Version | Namespace |
|---|---|---|
| `loki` (grafana-community) | `18.4.0` | `monitoring` |

Log aggregation backend, running in `Monolithic` deployment mode with S3-compatible object storage (SeaweedFS, bucket `loki`) as its chunk/ruler/admin store. Ingests logs from Grafana Alloy and, when enabled, Falcosidekick events.

#### Tempo
| Chart | Version | Namespace |
|---|---|---|
| `tempo` (grafana-community) | `2.2.3` (app pinned to `2.9.0`) | `monitoring` |

Distributed tracing backend, storing traces in the same SeaweedFS S3 bucket family. The container image tag is explicitly pinned to `2.9.0` (overriding the chart's default `2.10.7`) because newer Tempo builds dropped vParquet2 block-encoding support that the SeaweedFS `tempo` bucket may still contain.

#### k8s-monitoring (Grafana Alloy)
| Chart | Version | Namespace |
|---|---|---|
| `k8s-monitoring` (grafana.github.io) | `4.2.0` | (chart default) |

Deploys Grafana Alloy as a log-shipping DaemonSet, replacing the deprecated Promtail (EOL 2026-03-02). Collects container logs cluster-wide and pushes them to Loki via `podLogsViaLoki`.

### Storage & Backup

#### SeaweedFS
| Chart | Version | Namespace |
|---|---|---|
| `seaweedfs` (seaweedfs.github.io) | `4.34.0` | `storage` |

An S3-compatible distributed object store, self-hosted as the platform's storage backend for everything that needs a bucket — Loki chunks, Tempo traces, Velero backups, and CNPG WAL archives. All persistent components use `nfs-csi`-backed PVCs.

#### OpenBao
| Chart | Version | Namespace |
|---|---|---|
| `openbao` (openbao.github.io) | `0.28.3` | `storage` |

OpenBao (the open-source HashiCorp Vault fork) is the platform's central secrets manager — dynamic secrets, Kubernetes-auth-based injection, and a UI for platform operators. Runs single-instance (`ha.enabled: false`) with TLS enabled on the HTTPS listener; a companion `openbao-unseal` support app handles automatic unsealing after restarts.

#### Velero
| Chart | Version | Namespace |
|---|---|---|
| `velero` (vmware-tanzu) | `12.0.3` | `storage` |

Cluster backup/restore tool. Uses the AWS S3 plugin pointed at the SeaweedFS S3 endpoint (`s3ForcePathStyle`) as its backup storage location, so it needs no real cloud account — SeaweedFS masquerades as S3.

#### Velero UI
| Chart | Version | Namespace |
|---|---|---|
| `velero-ui` (github.com/otwld/velero-ui) | `v0.10.1` | `storage` |

A web UI/API for triggering and inspecting Velero backups/restores without kubectl, protected by Keycloak OIDC (server-side token exchange trusts the internal CA via `NODE_EXTRA_CA_CERTS`).

### Security

#### Trivy Operator
| Chart | Version | Namespace |
|---|---|---|
| `trivy-operator` (aquasecurity) | `0.27.0` | `security-system` |

Continuous vulnerability, misconfiguration, exposed-secret, and compliance scanning for every workload and image on the cluster, running in lightweight `Standalone` mode (Trivy CLI runs per-scan-Job, no shared server) to fit the resource-constrained worker nodes. Feeds the portal's Cluster Security page via `VulnerabilityReport`/`ConfigAuditReport`/etc. CRDs.

#### Kyverno
| Chart | Version | Namespace |
|---|---|---|
| `kyverno` (kyverno.github.io) | `3.8.1` | `platform-system` |

A Kubernetes-native policy engine enforcing admission-time and background policies (e.g. restricting portal-created Jobs, generating NetworkPolicies) across the cluster. Runs with multiple replicas per controller (admission/background/cleanup/reports) for resilience; it is fail-closed, so its own admission webhook availability is on the critical path for all pod scheduling.

> **Falco** (runtime security / syscall-level threat detection) is defined in `templates/falco.yaml` but currently **disabled** (`{{- if false }}`) — Falco 0.39.2's modern_eBPF driver fails `scap_init` on the Ubuntu 26.04 / kernel 7.0 nodes used by this cluster, and the 0.43+ upgrade needed to fix it is a breaking migration. Runtime coverage in the meantime comes from Trivy Operator + Kyverno + NetworkPolicy + STRICT mTLS. See the file header and `CLAUDE.md` mistakes log (2026-07-08) for details.

### IAM & SSO

#### Keycloak
| Manifest | Managed by | Namespace |
|---|---|---|
| `keycloak-cr.yaml` (Keycloak CR + login theme) | Keycloak Operator (installed by `scripts/cluster/11-keycloak.sh`) | `iam` |

Keycloak is the platform's Single Sign-On / OIDC Identity Provider — every SSO-integrated app (ArgoCD, Grafana, Harbor, Headlamp, OpenBao, velero-ui, Gitea, the K8s API server itself, and the portal) authenticates against it. It's not deployed via a generic Helm chart but as a `Keycloak` CR reconciled by the upstream Keycloak Operator, with a custom Narwhal-branded login theme (`keycloak-theme-narwhal` ConfigMap, bilingual EN/KO messages) mounted in. CPU/memory requests must live under `spec.resources` (the Operator ignores resources set under `unsupported.podTemplate`), while health-probe tuning goes the opposite way, through `unsupported.podTemplate`.

### Registry & Developer UI

#### Harbor
| Chart | Version | Namespace |
|---|---|---|
| `harbor` (helm.goharbor.io) | `1.19.1` | `devtools` |

The platform's private container registry — image storage, vulnerability scanning integration, and OIDC-based access control (auto-onboards Keycloak users via a dedicated `harbor_username` attribute, since Harbor reserves its built-in `admin` account). Uses an external CloudNative-PG-backed PostgreSQL database rather than the chart's bundled one.

#### Headlamp
| Chart | Version | Namespace |
|---|---|---|
| `headlamp` (kubernetes-sigs.github.io/headlamp) | `0.42.0` | `devtools` |

A web-based Kubernetes dashboard/UI for cluster operators, authenticated via Keycloak OIDC. Opted out of the ambient mesh (`istio.io/dataplane-mode: none`) to avoid ztunnel interfering with its SSO cookies, with probe-timeout tuning applied separately by a companion Kyverno policy (`headlamp-policy.yaml`).

### Portal

#### Narwhal Portal
| Manifest | Image | Namespace |
|---|---|---|
| `narwhal-portal-k8s.yaml` (Deployment/Service/RBAC/Valkey) | `ghcr.io/dasomel/narwhal-portal:1.0.16` (public GHCR, SemVer-pinned) | `devtools` |

The Narwhal management portal (Next.js) — the developer-facing UI for this entire IDP: cluster architecture view, ArgoCD app status, security/vulnerability reports, node metrics, logs/traces, backup status, and a component "scorecard" (checks ArgoCD sync health, image provenance, PDB/NetworkPolicy presence). Deployed with a least-privilege `ServiceAccount` (cluster-wide read-only RBAC plus a namespace-scoped Role for its own self-service Jobs), a dedicated Valkey cache Deployment, and the internal root CA mounted via `NODE_EXTRA_CA_CERTS` so its server-side calls to Keycloak/ArgoCD/OpenBao/K8s API trust the cluster's TLS. The image tag is an immutable SemVer pin (not `:latest`) so ArgoCD can actually detect upgrades as a manifest diff.

---

## Conventions

- **SemVer-pinned images and charts.** Mutable tags like `:latest` are avoided in GitOps manifests — ArgoCD diffs the rendered manifest string, so a re-published `:latest` looks identical and silently fails to trigger a sync (see the portal image, which learned this the hard way and is now pinned to `1.0.1`). Chart `targetRevision` is always an explicit version.
- **GitOps-only changes.** Every persistent change goes through this repo → push to Gitea → ArgoCD auto-syncs. `kubectl apply`/`edit` against a managed resource is reverted by `selfHeal` on the next reconcile loop; it's only useful for throwaway debugging.
- **Namespace overview:**

| Namespace | Purpose |
|---|---|
| `platform-system` | Shared infra plane: MetalLB, APISIX, cert-manager, Kyverno (PERMISSIVE mTLS exception — talks to non-mesh clients) |
| `istio-system` | Service mesh control plane and dataplane (istiod, istio-cni, ztunnel) |
| `monitoring` | Observability stack: Prometheus, Grafana, Alertmanager, Loki, Tempo (PERMISSIVE mTLS exception for the opted-out portal) |
| `storage` | SeaweedFS, OpenBao, Velero, Velero UI |
| `security-system` | Trivy Operator (and, when re-enabled, Falco) |
| `iam` | Keycloak (intentionally NOT mesh-enrolled) |
| `devtools` | ArgoCD, Gitea, Harbor, Headlamp, the Narwhal Portal (PERMISSIVE mTLS exception scoped to Harbor's mixed-enrollment workloads) |
| `database` | CloudNative-PG PostgreSQL clusters (PERMISSIVE mTLS exception — plain-TCP calls from non-mesh Keycloak) |

---

<sub>Generated to describe the state of `gitops/apps` and `gitops/charts` as of the versions pinned in each manifest — always trust the manifests over this document if they diverge. See `../CLAUDE.md` and `../VERSIONS.md` for provisioning scripts and version history.</sub>
