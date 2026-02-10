# OSS Versions

Components and versions used in this project.

## Core Components

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35 | Container orchestration |
| containerd | apt package | Container runtime |
| kubeadm | v1.35 | Cluster bootstrap tool |
| kubelet | v1.35 | Node agent |
| kubectl | v1.35 | CLI tool |

## Networking

| Component | Version | Description |
|-----------|---------|-------------|
| kube-vip | v1.0.4 | Virtual IP for control plane HA |
| Cilium | v1.19.0 | CNI plugin (default), kube-proxy replacement |
| Traefik | v39.0.0 (chart) / v3.6.7 (app) | Gateway API controller, rate limiting |
| MetalLB | v0.15.3 | Bare-metal LoadBalancer |
| Cilium CLI | v0.19.0 | Cilium command-line tool |
| Gateway API CRDs | v1.2.1 | Kubernetes Gateway API standard |
| Hubble | v1.19.0 | Cilium observability (UI + Relay) |
| Hubble CLI | v1.18.5 | Hubble command-line tool |

## Storage

| Component | Version | Description |
|-----------|---------|-------------|
| NFS Server | apt package | nfs-kernel-server |
| NFS Client | apt package | nfs-common |
| quota tools | apt package | quota, quotatool |
| csi-driver-nfs | v4.12.1 | CSI driver for NFS |
| nfs-quota-agent | v0.2.1 | NFS project quota enforcement |
| SeaweedFS | v4.10 (chart 4.0.410) | S3-compatible object storage (Apache 2.0) |

## Database

| Component | Version | Description |
|-----------|---------|-------------|
| CloudNative-PG | v1.28.1 (chart 0.27.1) | PostgreSQL Operator |
| PostgreSQL | 17 | Database (HA) |

## Identity & Access

| Component | Version | Description |
|-----------|---------|-------------|
| Keycloak | v26.5.3 | IAM / SSO (1 instance) |
| OAuth2 Proxy | v7.14.2 (chart 10.1.3) | Gateway authentication proxy |
| K8s OIDC | - | API Server OIDC integration |

## Security

| Component | Version | Description |
|-----------|---------|-------------|
| cert-manager | v1.19.3 | TLS certificate automation |
| OpenBao | v2.5.0 (chart 0.25.0) | Secret management (standalone) |
| Kyverno | v1.17.0 (chart 3.7.0) | Policy management |

## Backup

| Component | Version | Description |
|-----------|---------|-------------|
| Velero | v1.17.1 (chart 11.3.2) | Kubernetes backup & restore |
| velero-plugin-for-aws | v1.11.1 | S3-compatible storage plugin |

## Observability

| Component | Version | Description |
|-----------|---------|-------------|
| Prometheus | v0.88.1 (chart 81.5.1) | Metrics collection (kube-prometheus-stack) |
| Grafana | (bundled with prometheus-stack) | Visualization / Dashboard |
| Loki | v3.6.4 (chart 6.52.0) | Log aggregation |
| Promtail | v3.5.1 (chart 6.17.1) | Log collector |
| Tempo | v2.9.0 (chart 1.24.4) | Distributed tracing |

## Git / GitOps

| Component | Version | Description |
|-----------|---------|-------------|
| Gitea | v1.25.4 (chart 12.5.0) | Git server |
| ArgoCD | v3.3.0 | GitOps CD |

## Registry

| Component | Version | Description |
|-----------|---------|-------------|
| Harbor | latest (chart 1.18.2) | Container registry (ARM64: ghcr.io/dasomel/goharbor) |

## Dashboard

| Component | Version | Description |
|-----------|---------|-------------|
| Headlamp | v0.40.0 | Kubernetes UI |

## Addons

| Component | Version | Description |
|-----------|---------|-------------|
| Helm | v4.1.0 | Package manager |
| metrics-server | v0.8.1 | Resource metrics |

## Utilities

| Component | Version | Description |
|-----------|---------|-------------|
| jq | apt package | JSON processor |
| yq | v4.50.1 | YAML processor |
| sshpass | apt package | SSH password authentication |

## Base Infrastructure

| Component | Version | Description |
|-----------|---------|-------------|
| Vagrant | 2.4+ | VM management |
| VirtualBox | 7.1+ | Virtualization provider |
| VMware Fusion | 25H2 | Virtualization provider (alternative) |
| Ubuntu | 24.04 LTS | Base OS (dasomel/ubuntu-24.04 Box) |

## Version Update Policy

- Use stable versions for production readiness
- Check latest versions:
  - K8s: `curl -sL https://dl.k8s.io/release/stable.txt`
  - kube-vip: `curl -s https://api.github.com/repos/kube-vip/kube-vip/releases/latest | jq -r '.tag_name'`
  - Cilium: `curl -s https://api.github.com/repos/cilium/cilium/releases/latest | jq -r '.tag_name'`

## Compatibility Matrix

| K8s Version | Cilium | csi-driver-nfs |
|-------------|--------|----------------|
| v1.35 | v1.19+ | v4.12+ |
| v1.34 | v1.18+ | v4.11+ |
| v1.33 | v1.17+ | v4.10+ |
