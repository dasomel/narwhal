# OSS Versions

Components and versions used in this project.

## Core Components

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35.1 | Container orchestration |
| containerd | 1.7.28 | Container runtime (Ubuntu apt) |
| kubeadm | v1.35.1 | Cluster bootstrap tool |
| kubelet | v1.35.1 | Node agent |
| kubectl | v1.35.1 | CLI tool |

## Networking

| Component | Version | Description |
|-----------|---------|-------------|
| Cilium | v1.19.0 | CNI plugin (default), kube-proxy replacement |
| Cilium CLI | v0.19.0 | Cilium command-line tool |
| APISIX | 3.11.0 (app) / 2.9.0 (chart) | API Gateway, OIDC authentication |
| APISIX Ingress Controller | 1.8.0 | K8s CRD controller for APISIX |
| APISIX Dashboard | 3.0.0 (app) / 0.9.0 (chart) | APISIX management UI |
| etcd (APISIX) | 3.5.21 (`registry.k8s.io/etcd:3.5.21-0`) | APISIX configuration store |
| MetalLB | v0.15.3 (chart) | Bare-metal LoadBalancer |
| Gateway API CRDs | v1.4.0 (experimental) | Kubernetes Gateway API standard |
| Hubble | v1.19.0 | Cilium observability (UI + Relay) |
| Hubble CLI | v1.18.5 | Hubble command-line tool |
| kube-vip | v1.0.4 | Control plane VIP (ARP, leader election) |

## Service Mesh

| Component | Version | Description |
|-----------|---------|-------------|
| Istio | v1.29.0 | Service mesh (ambient mode, zero sidecars) |
| ztunnel | v1.29.0 | Node proxy DaemonSet (HBONE mTLS) |
| istio-cni | v1.29.0 | CNI node agent (ambient traffic redirect) |

## Storage

| Component | Version | Description |
|-----------|---------|-------------|
| NFS Server | apt package | nfs-kernel-server |
| NFS Client | apt package | nfs-common (pre-installed in box) |
| quota tools | apt package | quota, quotatool |
| csi-driver-nfs | v4.12.1 (chart) | CSI driver for NFS |
| nfs-quota-agent | v0.2.1 | NFS project quota enforcement |
| SeaweedFS | v4.07 (chart 4.0.407) | S3-compatible object storage (Apache 2.0) |

## Database

| Component | Version | Description |
|-----------|---------|-------------|
| CloudNative-PG | v1.28.1 (chart 0.27.1) | PostgreSQL Operator |
| PostgreSQL | 18.2 | Database (HA, unified narwhal-db) |

## Identity & Access

| Component | Version | Description |
|-----------|---------|-------------|
| Authentik | 2025.4.0 | IAM / SSO (Helm, REST API 구성) |
| Valkey | 8 (8-alpine) | Authentik용 Redis 대체 (docker.io/valkey/valkey:8-alpine) |
| K8s OIDC | - | API Server OIDC integration |

## Security

| Component | Version | Description |
|-----------|---------|-------------|
| cert-manager | v1.19.3 (chart) | TLS certificate automation |
| OpenBao | v2.2.0 (chart 0.25.0) | Secret management (standalone) |
| Kyverno | v1.17.0 (chart 3.7.0) | Policy management |

## Backup

| Component | Version | Description |
|-----------|---------|-------------|
| Velero | v1.17.1 (chart 11.3.2) | Kubernetes backup & restore |
| velero-plugin-for-aws | v1.11.1 | S3-compatible storage plugin |

## Observability

| Component | Version | Description |
|-----------|---------|-------------|
| Prometheus | chart 81.5.1 | Metrics collection (kube-prometheus-stack) |
| Grafana | (bundled with prometheus-stack) | Visualization / Dashboard |
| Loki | v3.6.4 (chart 6.52.0) | Log aggregation |
| Promtail | v3.5.1 (chart 6.17.1) | Log collector |
| Tempo | v2.9.0 (chart 1.24.4) | Distributed tracing |

## Git / GitOps

| Component | Version | Description |
|-----------|---------|-------------|
| Gitea | v1.25.4 (chart 12.5.0) | Git server |
| ArgoCD | v3.3.0 | GitOps CD (manifest install) |

## Registry

| Component | Version | Description |
|-----------|---------|-------------|
| Harbor | latest (chart 1.18.2) | Container registry (ARM64: ghcr.io/dasomel/goharbor) |

## Dashboard

| Component | Version | Description |
|-----------|---------|-------------|
| Headlamp | v0.40.0 (chart) | Kubernetes UI |

## Addons

| Component | Version | Description |
|-----------|---------|-------------|
| Helm | v4.1.0 | Package manager |
| metrics-server | v0.8.1 | Resource metrics |

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
| Ubuntu | 24.04 LTS | Base OS (dasomel/ubuntu-24.04-xfs v0.2.2) |

## Version Update Policy

- Use stable versions for production readiness
- All versions must be pinned in installation scripts
- Check latest versions:
  - K8s: `curl -sL https://dl.k8s.io/release/stable.txt`
  - Cilium: `curl -s https://api.github.com/repos/cilium/cilium/releases/latest | jq -r '.tag_name'`

## Compatibility Matrix

| K8s Version | Cilium | csi-driver-nfs |
|-------------|--------|----------------|
| v1.35 | v1.19+ | v4.12+ |
| v1.34 | v1.17+ | v4.11+ |
| v1.33 | v1.16+ | v4.10+ |
