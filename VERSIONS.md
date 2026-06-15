# OSS Versions

Components and versions used in this project.

> **Airgap refresh 2026-06-15:** versions bumped to latest K8s-1.35-compatible / ARM64
> stable. Components that could not move safely this cycle are listed under
> **Frozen this cycle** at the bottom with the reason. After changing this file,
> regenerate the airgap image list: `scripts/airgap/01-generate-image-list.sh images.txt`.

## Core Components

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35.5 | Container orchestration |
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
| APISIX Dashboard | 3.0.0 (app) / 0.9.0 (chart) | APISIX management UI (**frozen** — chart deprecated upstream) |
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
| Loki | v3.6.4 (chart 6.52.0) | Log aggregation. **Frozen** — grafana/loki is now GEL-only; OSS moved to grafana-community/loki 17.x (re-install, separate work) |
| Promtail | v3.5.1 (chart 6.17.1) | Log collector. **Frozen** — EOL 2026-03-02; migrate to Grafana Alloy (separate work) |
| Tempo | v2.9.0 (chart 1.24.4) | Distributed tracing. **Frozen** — grafana-community migration + vParquet2 removal in 2.10 needs block audit (separate work) |

## Git / GitOps

| Component | Version | Description |
|-----------|---------|-------------|
| Gitea | v1.26.2 (chart 12.6.0) | Git server (env_to_ini → config edit-ini) |
| ArgoCD | v3.4.3 | GitOps CD (manifest install, SSA for CRDs; ApplicationSet ClusterGenerator label format changed) |

## Registry

| Component | Version | Description |
|-----------|---------|-------------|
| Harbor | latest (chart 1.18.2) | Container registry (ARM64: ghcr.io/dasomel/goharbor). **Frozen** — chart 1.19.1 (v2.15.1) ARM64 images not published in ghcr.io/dasomel/goharbor (only `latest` is multi-arch) |

## IDP Portal

| Component | Version | Description |
|-----------|---------|-------------|
| IDP Portal | 0.1.0 | Custom Next.js developer portal (Next.js 16.2.1) |
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

| Component | Version | Description |
|-----------|---------|-------------|
| Vagrant | 2.4+ | VM management |
| VirtualBox | 7.1+ | Virtualization provider |
| VMware Fusion | 25H2 | Virtualization provider (alternative) |
| Ubuntu | 26.04 LTS | Base OS (dasomel/ubuntu-26.04-xfs v0.1.0, Resolute, kernel 7.0) |

## Frozen this cycle (airgap refresh 2026-06-15)

These were intentionally NOT upgraded — each needs dedicated migration work, not an in-place bump:

| Component | Held at | Reason |
|-----------|---------|--------|
| APISIX Ingress Controller | 1.8.0 | v2.x major break: etcd-free arch, new CRD schema, annotation overhaul |
| APISIX Dashboard | app 3.0.0 / chart 0.9.0 | chart deprecated upstream; assess APISIX built-in console |
| Loki | app v3.6.4 / chart 6.52.0 | grafana/loki → grafana-community/loki 17.x repo split (re-install) |
| Tempo | app v2.9.0 / chart 1.24.4 | grafana-community migration + vParquet2 removed in 2.10 (block audit) |
| Promtail | app v3.5.1 / chart 6.17.1 | EOL 2026-03-02 → migrate to Grafana Alloy |
| Harbor | latest / chart 1.18.2 | ghcr.io/dasomel/goharbor lacks v2.15.1 ARM64 images |
| velero-ui | app v0.10.1 / chart 0.14.0 | no newer upstream release (consider seriohub/vui) |

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
