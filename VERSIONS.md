# OSS Versions

Components and versions used in this project.

> **Airgap refresh 2026-06-15:** versions bumped to latest K8s-1.35-compatible / ARM64
> stable. Components that could not move safely this cycle are listed under
> **Frozen this cycle** at the bottom with the reason. After changing this file,
> regenerate the airgap image list from a running cluster:
> `scripts/airgap/01-generate-image-list.sh --live scripts/airgap/images.txt`
> (the `--live` mode is authoritative — static source-scan misses chart-default images).

## Core Components

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35.5 | Container orchestration. **Not the newest minor** — 1.36 is current upstream (1.36.2, 2026-06-09). 1.35 stays on a supported branch until 2027-02-28; the platform deliberately trails one minor so the 35 integrated components have released compatible versions. Branch latest is 1.35.6 |
| containerd | 1.7.x (24.04) / repo default 2.x (26.04, 2.3.x LTS available) | Container runtime (Ubuntu apt; 02-containerd.sh pins 1.7.* then falls back to repo default) |
| kubeadm | v1.35.5 | Cluster bootstrap tool |
| kubelet | v1.35.5 | Node agent |
| kubectl | v1.35.5 | CLI tool |

## Networking

| Component | Version | Description |
|-----------|---------|-------------|
| Cilium | v1.19.4 | CNI plugin (default), kube-proxy replacement |
| Cilium CLI | v0.19.4 | Cilium command-line tool (0.19.3 has no binaries — skip) |
| APISIX | 3.15.0 (app) / 2.13.0 (chart) | API Gateway, OIDC authentication (NOT 3.16: openid-connect ssl_verify default flip breaks airgap private-CA OIDC) |
| APISIX Ingress Controller | 1.8.0 | K8s CRD controller for APISIX (**frozen** — v2.x is a major break) |
| etcd (APISIX) | 3.5.31 (`registry.k8s.io/etcd:3.5.31-0`) | APISIX configuration store (stay on 3.5.x) |
| MetalLB | v0.16.1 (chart) | Bare-metal LoadBalancer (L2 mode; default backend FRR→FRR-K8s, no impact on L2) |
| Gateway API CRDs | v1.5.1 (standard) | Kubernetes Gateway API standard (no TLSRoutes in repo → standard channel safe) |
| Hubble | v1.19.4 | Cilium observability (UI + Relay) |
| Hubble CLI | v1.19.4 | Hubble command-line tool |
| kube-vip | v1.1.2 | Control plane VIP (ARP, leader election) (avoid v1.2.0 — iptables egress SNAT removed) |

## Service Mesh

| Component | Version | Description |
|-----------|---------|-------------|
| Istio | v1.30.1 | Service mesh (ambient mode, zero sidecars). Upgrade order: istiod→istio-cni→ztunnel. XDS debug auth default-on |
| ztunnel | v1.30.1 | Node proxy DaemonSet (HBONE mTLS) |
| istio-cni | v1.30.1 | CNI node agent (ambient traffic redirect) |

## Storage

| Component | Version | Description |
|-----------|---------|-------------|
| NFS Server | apt package | nfs-kernel-server |
| NFS Client | apt package | nfs-common (pre-installed in box) |
| quota tools | apt package | quota, quotatool |
| csi-driver-nfs | 4.13.2 (chart) | CSI driver for NFS (SECURITY: CVE-2026-3864 path traversal fixed; chart drops `v` prefix) |
| nfs-quota-agent | v0.2.1 | NFS project quota enforcement |
| SeaweedFS | v4.34 (chart 4.34.0) | S3-compatible object storage (Apache 2.0); image tag scheme = chart appVersion ("4.34"); ARM64 needs nodeSelector override |

## Database

| Component | Version | Description |
|-----------|---------|-------------|
| CloudNative-PG | v1.29.1 (chart 0.28.3) | PostgreSQL Operator (metrics exporter RBAC grant needed — CVE-2026-44477) |
| PostgreSQL | 18.3 | Database (HA, unified narwhal-db) |

## Identity & Access

| Component | Version | Description |
|-----------|---------|-------------|
| Keycloak | 26.5.7 | IAM / SSO (Operator, kcadm.sh 구성). Capped below 26.6.x (regression #48030 + v2beta1 CRD migration); stays v2alpha1 |
| Keycloak Operator | 26.5.7 | Keycloak CR lifecycle management |
| K8s OIDC | - | API Server OIDC integration (Keycloak issuer) |

## Security

| Component | Version | Description |
|-----------|---------|-------------|
| cert-manager | v1.20.2 (chart) | TLS certificate automation (container UID/GID now 65532/65532 — PSA check) |
| OpenBao | v2.5.4 (chart 0.28.3) | Secret management (standalone). ARM64 confirmed on quay.io + docker.io. config: disable_unauthed_rekey_endpoints default→true |
| Kyverno | v1.18.1 (chart 3.8.1) | Policy management (CVE-2026-4789 / -41323 fixed) |

## Backup

| Component | Version | Description |
|-----------|---------|-------------|
| Velero | v1.18.1 (chart 12.0.3) | Kubernetes backup & restore (keep upgradeCRDs:false for ARM64; chart 12 init image = registry.k8s.io/kubectl) |
| velero-plugin-for-aws | v1.14.1 | S3-compatible storage plugin (strict pairing w/ Velero 1.18) |
| velero-ui | v0.10.1 (chart 0.14.0) | Velero Web UI (otwld/velero-ui, ARM64). **Frozen** — no newer upstream (inactive since 2024-10) |

## Observability

| Component | Version | Description |
|-----------|---------|-------------|
| Prometheus | chart 86.2.3 | Metrics collection (kube-prometheus-stack); SSA for CRDs |
| Grafana | 12.x (bundled with prometheus-stack) | Visualization / Dashboard (major bump — review dashboards/auth) |
| Loki | v3.7.3 (chart 18.4.0, grafana-community) | Log aggregation. In-place `helm upgrade` from grafana/loki — no data migration needed, S3/TSDB storage read as-is |
| Grafana Alloy (k8s-monitoring) | chart 4.2.0 / alloy-operator 0.5.11 / Alloy app v1.17.0 | Log collector — replaces Promtail (EOL 2026-03-02). Logs-only (`podLogsViaLoki`); all metrics/events/exporter features left at their default `false` to avoid duplicating prometheus-stack |
| Tempo | v2.9.0 (chart 2.2.3, grafana-community) | Distributed tracing. Chart source moved; app version pinned at 2.9.0 pending a live vParquet2 block audit before allowing the chart's default 2.10.7 |

## Git / GitOps

| Component | Version | Description |
|-----------|---------|-------------|
| Gitea | v1.26.2 (chart 12.6.0) | Git server (env_to_ini → config edit-ini) |
| ArgoCD | v3.4.4 | GitOps CD (manifest install, SSA for CRDs; ApplicationSet ClusterGenerator label format changed). Security note 2026-07-06: the 2026-05 advisories (CVE-2026-42880 ServerSideDiff secret leak Critical, CVE-2026-45737/45738) were all patched at v3.4.2 — v3.4.3+ already safe; v3.4.4 is bugfix-only (RBAC regression, SSD error handling) |

## Registry

| Component | Version | Description |
|-----------|---------|-------------|
| Harbor | latest (chart 1.19.1) — `:latest` currently resolves to app **v2.15.1** (harbor-core digest match, 2026-07-07); v2.15.2 published but not yet promoted to `:latest` | Container registry (ARM64: ghcr.io/dasomel/goharbor, multi-arch amd64+arm64). Deploys track `:latest` by policy. To move to v2.15.2, the custom rebuild pipeline must promote `:latest`→v2.15.2 for the 6 versioned components (core, jobservice, registry-photon, registryctl, portal, nginx-photon); redis-photon has no v2.15.2 (stays v2.15.1) and harbor-exporter is unversioned (`:latest` only) |

## IDP Portal

| Component | Version | Description |
|-----------|---------|-------------|
| IDP Portal | 1.0.17 | Custom Next.js developer portal (Next.js 16.2.1). Deployed from `ghcr.io/dasomel/narwhal-portal:1.0.17` (pinned, public GHCR — was in-cluster Kaniko→Harbor `:latest`). Upgrade = bump the tag in `gitops/.../narwhal-portal-k8s.yaml`. In-cluster Kaniko build kept as optional dev tool (`docs/common/developer-kaniko-builds.md`) |
| Next.js | 16.2.1 | React framework (App Router, standalone output) |
| NextAuth.js | 5.0.0-beta.30 | OIDC authentication (Keycloak provider) |
| Valkey (portal) | 8-alpine | Portal-dedicated cache (docker.io/valkey/valkey:8-alpine) |
| TanStack Query | 5.x | Server state management / polling |

## Dashboard

| Component | Version | Description |
|-----------|---------|-------------|
| Headlamp | v0.42.0 (chart) | Kubernetes UI (path-traversal fix) |

## Addons

| Component | Version | Description |
|-----------|---------|-------------|
| Helm | v4.2.1 | Package manager |
| metrics-server | v0.8.1 (chart 3.13.1) | Resource metrics (app already latest) |

## Utilities

| Component | Version | Description |
|-----------|---------|-------------|
| jq | apt package | JSON processor (pre-installed in box) |
| yq | apt package | YAML processor (pre-installed in box) |
| sshpass | apt package | SSH password authentication (pre-installed in box) |

## Base Infrastructure

| Component | Version   | Description |
|-----------|-----------|-------------|
| Vagrant | 2.4+      | VM management |
| VirtualBox | 7.1+      | Virtualization provider |
| VMware Fusion | 26H1      | Virtualization provider (alternative) |
| Ubuntu | 26.04 LTS | Base OS (dasomel/ubuntu-26.04-xfs v0.1.0, Resolute, kernel 7.0) |

## Frozen this cycle (airgap refresh 2026-06-15)

These were intentionally NOT upgraded — each needs dedicated migration work, not an in-place bump:

| Component | Held at | Reason |
|-----------|---------|--------|
| APISIX Ingress Controller | 1.8.0 | v2.x major break: etcd-free arch, new CRD schema, annotation overhaul |
| velero-ui | app v0.10.1 / chart 0.14.0 | no newer upstream release (consider seriohub/vui) |

**Resolved this cycle:** APISIX Dashboard (standalone chart, app 3.0.0 / chart 0.9.0) removed
2026-07-05 — upstream deprecated it in favor of a console built directly into APISIX itself,
served on the Admin API port (`/ui/`, see apisix.apache.org/docs/apisix/next/dashboard/). Not
exposed via an external route (would require exposing the Admin API, a separate security
tradeoff) — access locally via `kubectl port-forward svc/apisix-admin 9180:9180` if ever needed.
Routes are managed via GitOps `ApisixRoute`/`ApisixUpstream` CRDs, so `kubectl get apisixroute -A`
covers day-to-day inspection without a UI.

Harbor bumped to chart 1.19.1 2026-07-05 — verified via `docker manifest inspect` that all 8
`ghcr.io/dasomel/goharbor/*:latest` component images now carry both amd64 and arm64 manifests
(the prior freeze reason, ARM64 images missing for the 2.15.x line, no longer applies). Image
tags stay pinned to `:latest` as before (not version-tagged). **Correction (2026-07-06):** the
running app version was assumed to be v2.15.2 (the source release that prompted this work) but
was NOT independently verified at the time — live check via `/api/v2.0/systeminfo` on the
deployed cluster showed `harbor_version: v2.15.0-f585d00b`. `docker manifest inspect` confirms
multi-arch support but says nothing about which app version is behind the tag.
**Update (2026-07-07):** the custom rebuild pipeline has since published explicit **v2.15.2**
tags (amd64+arm64) for 6 of the 8 components — core, jobservice, registry-photon, registryctl,
portal, nginx-photon — verified via `crane ls`. `redis-photon` tops out at v2.15.1 (no v2.15.2
build) and `harbor-exporter` carries no versioned tags (`:latest` only). However `:latest` itself
has only advanced to **v2.15.1**, NOT v2.15.2 (confirmed by digest match: `harbor-core:latest` ==
`harbor-core:v2.15.1`, ≠ `v2.15.2`). Per the `:latest`-auto-track policy (decision D-harbor-latest:
keep `:latest`, do NOT version-pin harbor.yaml — reason: custom rebuild republishes to `:latest`
for automatic pickup; cost: `:latest` is a moving target; escape hatch: pin explicit version tags
if reproducibility becomes required), **the action to reach v2.15.2 is on the registry side, not
this repo**: promote `:latest`→v2.15.2 for the 6 versioned components in the
`ghcr.io/dasomel/goharbor` rebuild pipeline. Once promoted, the cluster picks it up automatically
(ArgoCD selfHeal + image pull) — no narwhal repo change. Chart 1.19.1 remains the correct pin.

Loki and Tempo chart source moved to grafana-community 2026-07-05 (both `grafana/loki` and
`grafana/tempo` are past the GEL-only cutover date, per each chart's own README migration
notice). Loki: in-place `helm upgrade` to chart 18.4.0 (app v3.7.3), `deploymentMode` renamed
`SingleBinary` -> `Monolithic`, no data migration needed (S3/TSDB storage read as-is). Tempo:
chart 2.2.3, but `image.tag` pinned to 2.9.0 pending a live vParquet2 block audit before allowing
the chart's default app v2.10.7 (2.10 removes vParquet2 read support entirely, with no
auto-migration for any block that used it).

## Version Update Policy

- Use stable versions for production readiness
- All versions must be pinned in installation scripts
- Check latest versions:
  - K8s: `curl -sL https://dl.k8s.io/release/stable.txt`
  - Cilium: `curl -s https://api.github.com/repos/cilium/cilium/releases/latest | jq -r '.tag_name'`

## Compatibility Matrix

| K8s Version | Cilium | csi-driver-nfs | Istio |
|-------------|--------|----------------|-------|
| v1.35 | v1.19+ | v4.13+ | v1.29–v1.30 |
| v1.34 | v1.17+ | v4.11+ | v1.28–v1.30 |
| v1.33 | v1.16+ | v4.10+ | v1.27–v1.29 |
