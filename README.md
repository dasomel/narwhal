# Narwhal

[![GitHub Release](https://img.shields.io/github/v/release/dasomel/narwhal)](https://github.com/dasomel/narwhal/releases/latest)
[![License](https://img.shields.io/github/license/dasomel/narwhal)](LICENSE)

English | [한국어](README_ko.md)

> **Narwhal** - A whale inhabiting the Arctic Ocean, characterized by a single long spiral tusk growing from its head. Called the "unicorn of the sea," it provides a powerful platform in a single cluster, just like this project.

Vagrant-based Kubernetes Internal Developer Platform (IDP) cluster.

Uses the [dasomel/ubuntu-26.04-xfs](https://app.vagrantup.com/dasomel/boxes/ubuntu-26.04-xfs) Box (XFS filesystem, project quota support).

> **Base Box Source**: [kube-ready-box](https://github.com/dasomel/kube-ready-box) - Packer-based Box with Ubuntu 26.04 and K8s prerequisites pre-installed

## Features

- **Kubernetes v1.35** - Latest stable version, HA Control Plane (3 masters, 1 fault tolerance)
- **GitOps** - ArgoCD + Gitea (App-of-Apps pattern)
- **SSO** - Keycloak OIDC (via APISIX openid-connect plugin, App integration: ArgoCD, Grafana, Gitea, Harbor, Headlamp)
- **Observability** - Prometheus, Grafana, Loki, Tempo, Hubble
- **Storage** - NFS (Block) + SeaweedFS (Object/S3) + [nfs-quota-agent](https://github.com/dasomel/nfs-quota-agent)
- **Backup** - Velero + CNPG barman
- **Service Mesh** - Istio ambient mode (mTLS, zero sidecars, ztunnel)
- **Security** - cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- **Networking** - Cilium (CNI), APISIX (API Gateway, OIDC), MetalLB (LoadBalancer), kube-vip (VIP HA)

## Requirements

- [Vagrant](https://developer.hashicorp.com/vagrant/install) 2.4+
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads) 7.1+ or [VMware Fusion](https://www.vmware.com/products/fusion.html) 26H1
- 32GB+ RAM (40GB+ recommended)
- 30GB+ Disk per VM (recommended for full IDP deployment)

### VirtualBox Disk Expansion

To automatically expand disk size in VirtualBox, install the `vagrant-disksize` plugin:

```bash
vagrant plugin install vagrant-disksize
```

> **Note**: VMware Fusion is automatically handled via `vmx` settings.
> A 1TB thin-provisioned template is used.

## Quick Start

```bash
# Clone
git clone https://github.com/dasomel/narwhal.git
cd narwhal

# Create cluster
vagrant up --provider=vmware_desktop

# Check status
vagrant ssh master-1 -c "kubectl get nodes"

# Destroy
vagrant destroy -f
```

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    Vagrant VMs                   │
├──────────────────┬─────────────┬─────────────────┤
│  master-1        │  master-2/3 │ worker-1/2/3    │
│  192.168.56.10   │  .11 / .12  │ .21 / .22 / .23 │
│  2 CPU, 4GB      │  2 CPU, 4GB │ 2CPU, 6GB       │
│  NFS, dnsmasq    │  dnsmasq    │                 │
└──────────────────┴─────────────┴─────────────────┘
         │                │            │
         └────────────────┼────────────┤
                           │            │
            ┌──────────────┴────────────┴────┐
            │  VIP: 192.168.56.100           │
            │       (kube-vip API HA)        │
            │  LB:  192.168.56.200           │
            │       (MetalLB/APISIX)         │
            │  DNS: 192.168.56.10:53         │
            │  (*.local.narwhal.internal)    │
            └────────────────────────────────┘
```

## Components

### Base Infrastructure (Script-installed)

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35.5 | Container orchestration |
| Cilium | v1.19.4 | CNI + kube-proxy replacement |
| Hubble | v1.19.4 | Network observability |
| kube-vip | v1.1.2 | Control plane VIP HA |
| MetalLB | v0.16.1 | Bare-metal LoadBalancer |
| APISIX | 3.15.0 | API Gateway (TLS + OIDC via openid-connect plugin) |
| cert-manager | v1.20.2 | TLS automation |
| CloudNative-PG | v1.29.1 | PostgreSQL Operator |
| Keycloak | 26.5.7 | IAM / SSO (Operator) |
| Gitea | v1.26.2 | Git server |
| ArgoCD | v3.4.4 | GitOps CD |
| Istio | v1.30.1 | Service mesh (ambient mode) |

### IDP Apps (ArgoCD GitOps)

| Component | Chart Version | App Version | Description |
|-----------|---------------|-------------|-------------|
| Prometheus Stack | 86.2.3 | v0.91.0 | Monitoring (Prometheus + Grafana + Alertmanager) |
| Loki | 18.4.0 (grafana-community) | 3.7.3 | Log aggregation |
| Grafana Alloy (k8s-monitoring) | 4.2.0 | v1.17.0 | Log collector (replaces Promtail, EOL 2026-03-02) |
| Tempo | 2.2.3 (grafana-community) | 2.9.0 (image.tag pinned pending vParquet2 audit) | Distributed tracing |
| Harbor | 1.19.1 | v2.15.1 (`:latest`; v2.15.2 published but `:latest` not yet promoted — see VERSIONS.md) | Container registry (ARM64, ghcr.io/dasomel/goharbor) |
| OpenBao | 0.28.3 | v2.5.4 | Secret management |
| Kyverno | 3.8.1 | v1.18.1 | Policy engine |
| Headlamp | 0.42.0 | v0.42.0 | Kubernetes UI |
| APISIX | 2.13.0 | 3.15.0 | API Gateway (TLS + OIDC) |
| SeaweedFS | 4.34.0 | v4.34 | Object storage (S3) |
| Velero | 12.0.3 | v1.18.1 | Backup & Restore |

See [VERSIONS.md](VERSIONS.md) for full version list.

## Management Portal

The cluster ships with the **[Narwhal IDP Portal](https://github.com/dasomel/narwhal-portal)** (Next.js 16 + React 19) for day-2 operations — real-time metrics, GitOps status, security, cost, and self-service. Reachable at `https://portal.local.narwhal.internal` after install.

| Dashboard | Architecture |
| :---: | :---: |
| ![Dashboard](docs/images/portal-dashboard.png) | ![Architecture](docs/images/portal-architecture.png) |
| _Real-time metrics, ArgoCD apps & alerts_ | _Nodes, namespaces & service graph_ |
| **Security** | **Cost** |
| ![Security](docs/images/portal-security.png) | ![Cost](docs/images/portal-cost.png) |
| _Trivy vulnerability reports_ | _Namespace cost breakdown_ |
| **Governance** | **Catalog** |
| ![Governance](docs/images/portal-governance.png) | ![Catalog](docs/images/portal-catalog.png) |
| _Scorecard, DORA & distribution_ | _Self-service app catalog_ |

> Drop captures into [`docs/images/`](docs/images/) ([details](docs/images/README.md)). Full UI gallery: [narwhal-portal](https://github.com/dasomel/narwhal-portal).

## Access Services

### DNS Access (Recommended)

Access services via HTTPS domains using APISIX API Gateway and cert-manager self-signed TLS.

DNS Configuration: Configure the client's DNS to `192.168.56.10` or add entries to `/etc/hosts`.

| Service | URL | Credentials |
|--------|-----|-----------|
| ArgoCD | https://argocd.local.narwhal.internal | admin / (auto-generated secret) or Keycloak SSO |
| Grafana | https://grafana.local.narwhal.internal | admin / admin or Keycloak SSO |
| Gitea | https://gitea.local.narwhal.internal | gitea-admin / gitea-admin or Keycloak SSO |
| Harbor | https://harbor.local.narwhal.internal | admin / Harbor12345 or Keycloak SSO |
| Keycloak | https://keycloak.local.narwhal.internal | temp-admin / (auto-generated) |
| Headlamp | https://headlamp.local.narwhal.internal | Keycloak SSO |
| OpenBao | https://openbao.local.narwhal.internal | root token (`bao operator init`) |
| Hubble | https://hubble.local.narwhal.internal | - |

> **Note**: Due to the use of self-signed certificates, a security warning will be displayed in your browser. Access the service by clicking "Advanced" → "Proceed".

### Port-Forward Access (Alternative)

```bash
# ArgoCD (GitOps)
kubectl port-forward svc/argocd-server -n devtools 8443:443
# https://localhost:8443 (admin / kubectl -n devtools get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)

# Keycloak (IAM)
kubectl port-forward svc/keycloak-service -n iam 8080:8080
# http://localhost:8080

# Grafana (Monitoring)
kubectl port-forward svc/prometheus-stack-grafana -n monitoring 3000:80
# http://localhost:3000 (admin / admin or Keycloak SSO)

# Gitea (Git)
kubectl port-forward svc/gitea-http -n devtools 3000:3000
# http://localhost:3000 (gitea-admin / gitea-admin)

# Harbor (Registry)
kubectl port-forward svc/harbor -n devtools 8080:80
# http://localhost:8080 (admin / Harbor12345)

# Headlamp (K8s UI)
kubectl port-forward svc/headlamp -n devtools 4466:80
# http://localhost:4466 (Keycloak SSO)
```

## Keycloak SSO

All apps are integrated with Keycloak OIDC. (HTTPS required, K8s 1.35+)

| SSO Integrated App | Client ID | Authentication Method |
|-------------|-----------|-----------|
| ArgoCD | `argocd` | OIDC config in argocd-cm |
| Grafana | `grafana` | grafana.ini auth.generic_oauth |
| Gitea | `gitea` | OAuth2 auth source (openidConnect) |
| Harbor | `harbor` | configureUserSettings OIDC |
| Headlamp | `headlamp` | OIDC config + CA cert mount |

| Group | K8s Role | App Role |
|-------|----------|----------|
| cluster-admin | cluster-admin | Admin |
| developer | edit (dev NS) | Editor |
| viewer | view | Viewer |
| guest | - | - (Web UI only) |

**Default Users:**
- `admin` / `admin` (cluster-admin)
- `dev` / `dev` (developer)
- `view` / `view` (viewer)
- `guest` / `guest` (guest)

For details, refer to: [docs/keycloak-accounts.md](docs/keycloak-accounts.md)

## Verification

Cluster status verification:

```bash
# Full verification (120+ checks)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"

# Phase 1 only (Cluster infrastructure)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh --stage=phase1"

# Phase 2 only (Platform apps)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh --stage=phase2-apps"

# SSO tests (49 checks)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh"

# Quick verification
vagrant ssh master-1 -c "kubectl get nodes && kubectl get pods -A | grep -v Running"
```

## GitOps Structure

```
gitops/
├── apps/                    # ArgoCD Applications
│   ├── app-of-apps.yaml
│   ├── cert-manager.yaml
│   ├── prometheus-stack.yaml
│   ├── loki.yaml
│   ├── tempo.yaml
│   ├── harbor.yaml
│   ├── headlamp.yaml
│   ├── metallb.yaml
│   ├── apisix.yaml
│   ├── apisix-routes.yaml
│   ├── openbao.yaml
│   ├── kyverno.yaml
│   ├── k8s-monitoring.yaml
│   ├── seaweedfs.yaml
│   ├── velero.yaml
│   ├── istio-base.yaml
│   ├── istiod.yaml
│   ├── istio-cni.yaml
│   └── ztunnel.yaml
└── resources/               # K8s Resources
    ├── gitea-db.yaml
    ├── grafana-datasources.yaml
    ├── harbor-db.yaml
    ├── cnpg-backup.yaml
    ├── kyverno-policies.yaml
    ├── metallb-config.yaml
    ├── narwhal-db.yaml
    ├── apisix-routes.yaml    # ApisixRoute & ApisixTls
    └── istio-ambient-policies.yaml
```

## Backup

| Target | Method | Storage | Schedule |
|--------|--------|---------|----------|
| PostgreSQL | CNPG barman | SeaweedFS S3 | Daily 00:00 |
| PVC (all) | Velero Kopia | SeaweedFS S3 | Daily 02:00 |

```bash
# Manual backup
velero backup create my-backup --include-namespaces=default

# Restore
velero restore create --from-backup my-backup

# List backups
velero backup get
```

## Configuration

`Vagrantfile` variables:

```ruby
K8S_VERSION = "1.35"           # Kubernetes version
MASTER_COUNT = 3               # Master nodes (HA, 1 fault tolerance)
WORKER_COUNT = 3               # Worker nodes
MASTER_MEMORY = 6144           # Master RAM (MB) - control-plane + DaemonSets headroom
WORKER_MEMORY = 6144           # Worker RAM (MB) - platform apps run here
VIP_ADDRESS = "192.168.56.100" # Control plane VIP
```

## Commands

```bash
# Start cluster (Phase 1 + 2 run automatically)
vagrant up --provider=vmware_desktop

# Start specific node
vagrant up master-1
vagrant up worker-1

# SSH access
vagrant ssh master-1

# Manual execution of Phase 2 only (after cluster creation)
vagrant provision master-1 --provision-with phase2-platform

# Reprovision
vagrant provision master-1

# Halt
vagrant halt

# Destroy
vagrant destroy -f
```

### SSO User Accounts

All users in the narwhal realm can access (the openid-connect plugin handles authentication only — no group restrictions):

| User | Group | Password Verification |
|---------|-----------------|------------------------|
| `admin` | `cluster-admin` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' \| base64 -d` |
| `dev` | `developer` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.dev}' \| base64 -d` |
| `view` | `viewer` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.view}' \| base64 -d` |
| `guest` | `guest` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.guest}' \| base64 -d` |

## Documentation

- [VERSIONS.md](VERSIONS.md) - Component versions
- [docs/architecture.md](docs/architecture.md) - Architecture details
- [docs/cloud-deployment.md](docs/cloud-deployment.md) - Kakao Cloud deployment (topology, egress, airgap, provider-aware GitOps)
- [docs/keycloak-sso.md](docs/keycloak-sso.md) - Keycloak SSO detailed configuration
- [docs/keycloak-accounts.md](docs/keycloak-accounts.md) - Keycloak SSO account and configuration guide
- [docs/dns-access.md](docs/dns-access.md) - DNS settings and service access
- [docs/kubeconfig.md](docs/kubeconfig.md) - kubeconfig and OIDC authentication
- [docs/database.md](docs/database.md) - Database (CNPG) management
- [docs/operations.md](docs/operations.md) - Operations guide
- [docs/rtk-token-compression-policy.md](docs/rtk-token-compression-policy.md) - RTK token compression policy
- [docs/troubleshooting.md](docs/troubleshooting.md) - Troubleshooting guide
- [docs/security.md](docs/security.md) - Security policy

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.
