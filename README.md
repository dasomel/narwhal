# Narwhal

Vagrant 기반 Kubernetes Internal Developer Platform (IDP) 클러스터.

[dasomel/ubuntu-24.04-xfs](https://app.vagrantup.com/dasomel/boxes/ubuntu-24.04-xfs) Box 사용 (XFS 파일시스템, Project Quota 지원).

> **Base Box Source**: [kube-ready-box](https://github.com/dasomel/kube-ready-box) - Ubuntu 24.04 + K8s prerequisites 사전 설치된 Packer 기반 Box

## Features

- **Kubernetes v1.35** - 최신 안정 버전
- **GitOps** - ArgoCD + Gitea
- **SSO** - Keycloak OIDC (모든 앱 연동)
- **Observability** - Prometheus, Grafana, Loki, Tempo
- **Storage** - NFS (Block) + SeaweedFS (Object/S3) + [nfs-quota-agent](https://github.com/dasomel/nfs-quota-agent)
- **Backup** - Velero + CNPG barman
- **Security** - cert-manager, OpenBao, Kyverno

## Requirements

- [Vagrant](https://developer.hashicorp.com/vagrant/install) 2.4+
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads) 7.1+ 또는 [VMware Fusion](https://www.vmware.com/products/fusion.html) 25H2
- 16GB+ RAM (권장 24GB+)
- VM당 30GB+ Disk (IDP 전체 배포 권장)

### VirtualBox 디스크 확장

VirtualBox에서 디스크 크기를 자동으로 확장하려면 `vagrant-disksize` 플러그인을 설치하세요:

```bash
vagrant plugin install vagrant-disksize
```

> **Note**: VMware Fusion은 `vmx` 설정으로 자동 처리됩니다.
> 1TB 씬 프로비전 템플릿을 사용합니다.

## Quick Start

```bash
# Clone
git clone https://github.com/dasomel/narwhal.git
cd narwhal

# Create cluster
vagrant up --provider=vmware_desktop

# Check status
vagrant ssh master -c "kubectl get nodes"

# Destroy
vagrant destroy -f
```

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        Vagrant VMs                             │
├──────────────────┬──────────────────┬──────────────────────────┤
│  master          │  worker-1        │  worker-2                │
│  192.168.56.10   │  192.168.56.21   │  192.168.56.22           │
│  2 CPU, 4GB      │  2 CPU, 4GB      │  2 CPU, 4GB              │
│  DNS (dnsmasq)   │                  │                          │
└──────────────────┴──────────────────┴──────────────────────────┘
         │                   │                    │
         └───────────────────┼────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │  VIP: 192.168.56.100        │
              │       (kube-vip API)        │
              │  LB:  192.168.56.200        │
              │       (MetalLB/Traefik)     │
              │  DNS: 192.168.56.10:53      │
              │       (*.local.narwhal.io)  │
              └─────────────────────────────┘
```

## Components

### Base Infrastructure (Script)

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35 | Container orchestration |
| Cilium | v1.18.6 | CNI + kube-proxy replacement |
| Traefik | v34.0.0 | Gateway API + Rate Limiting |
| MetalLB | v0.14.9 | Bare-metal LoadBalancer |
| Hubble | v1.18.6 | Network observability |
| kube-vip | v1.0.3 | Control plane HA |
| CloudNative-PG | v1.26.2 | PostgreSQL Operator |
| Keycloak | v26.5.2 | IAM / SSO |
| Gitea | v1.25.4 | Git server |
| ArgoCD | v3.2.6 | GitOps CD |

### IDP Apps (ArgoCD GitOps)

| Component | Version | Description |
|-----------|---------|-------------|
| cert-manager | v1.19.2 | TLS automation |
| Prometheus | v0.88.0 | Metrics |
| Grafana | v12.3.1 | Dashboard |
| Loki | v3.6.4 | Logs |
| Tempo | v2.9.1 | Traces |
| Harbor | v2.14.2 | Container registry |
| OpenBao | v2.4.4 | Secrets |
| Kyverno | v1.16.2 | Policy |
| Headlamp | v0.39.0 | K8s UI |
| SeaweedFS | v4.07 | Object storage (S3) |
| Velero | v1.16 | Backup |

See [VERSIONS.md](VERSIONS.md) for full version list.

## Access Services

### DNS 접속 (권장)

Traefik Gateway를 통해 도메인으로 접속합니다. DNS 설정: [docs/DNS-ACCESS.md](docs/DNS-ACCESS.md)

| 서비스 | URL | 자격 증명 |
|--------|-----|-----------|
| ArgoCD | http://argocd.local.narwhal.io | admin / (시크릿) |
| Grafana | http://grafana.local.narwhal.io | admin / admin |
| Gitea | http://gitea.local.narwhal.io | gitea-admin / gitea-admin |
| Harbor | http://harbor.local.narwhal.io | admin / Harbor12345 |
| Keycloak | http://keycloak.local.narwhal.io | admin / admin |
| Headlamp | http://headlamp.local.narwhal.io | Keycloak SSO |
| OpenBao | http://openbao.local.narwhal.io | root token |
| Traefik | http://traefik.local.narwhal.io | - |

### Port-Forward 접속 (대안)

```bash
# ArgoCD (GitOps)
kubectl port-forward svc/argocd-server -n argocd 8443:443
# https://localhost:8443 (admin / kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)

# Keycloak (IAM)
kubectl port-forward svc/keycloak-service -n keycloak 8080:8080
# http://localhost:8080 (admin / admin)

# Grafana (Monitoring)
kubectl port-forward svc/prometheus-grafana -n monitoring 3000:80
# http://localhost:3000 (admin / admin or Keycloak SSO)

# Gitea (Git)
kubectl port-forward svc/gitea-http -n gitea 3000:3000
# http://localhost:3000 (gitea-admin / gitea-admin)

# Harbor (Registry)
kubectl port-forward svc/harbor -n harbor 8080:80
# http://localhost:8080 (admin / Harbor12345)

# Headlamp (K8s UI)
kubectl port-forward svc/headlamp -n headlamp 4466:80
# http://localhost:4466 (Keycloak SSO)
```

## Keycloak SSO

모든 앱이 Keycloak OIDC로 연동됩니다.

| Group | K8s Role | App Role |
|-------|----------|----------|
| cluster-admins | cluster-admin | Admin |
| developers | edit | Editor |
| viewers | view | Viewer |

**Default Users:**
- `k8s-admin` / `k8s-admin` (cluster-admins)
- `developer` / `developer` (developers)

자세한 내용: [docs/KEYCLOAK-SSO.md](docs/KEYCLOAK-SSO.md)

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
│   ├── openbao.yaml
│   ├── kyverno.yaml
│   ├── headlamp.yaml
│   ├── seaweedfs.yaml
│   ├── velero.yaml
│   └── traefik.yaml         # Gateway API Controller
└── resources/               # K8s Resources
    ├── gitea-db.yaml
    ├── harbor-db.yaml
    ├── cnpg-backup.yaml
    ├── kyverno-policies.yaml
    └── traefik-routes.yaml   # HTTPRoutes & Middlewares
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

`Vagrantfile` 변수:

```ruby
K8S_VERSION = "1.35"       # Kubernetes version
WORKER_COUNT = 2           # Worker nodes
MASTER_MEMORY = 4096       # Master RAM (MB)
WORKER_MEMORY = 4096       # Worker RAM (MB)
DISK_SIZE_GB = 50          # VM disk size (GB)
VIP_ADDRESS = "192.168.56.100"  # Control plane VIP
```

## Commands

```bash
# Start cluster
vagrant up

# Start specific node
vagrant up master
vagrant up worker-1

# SSH access
vagrant ssh master

# Reprovision
vagrant provision master

# Halt
vagrant halt

# Destroy
vagrant destroy -f
```

## Documentation

- [VERSIONS.md](VERSIONS.md) - Component versions
- [docs/DNS-ACCESS.md](docs/DNS-ACCESS.md) - DNS 접속 가이드 (Traefik Gateway)
- [docs/KUBECONFIG.md](docs/KUBECONFIG.md) - Kubeconfig setup guide
- [docs/KEYCLOAK-SSO.md](docs/KEYCLOAK-SSO.md) - SSO & Authorization guide
- [.agent/AGENT.md](.agent/AGENT.md) - Project guide for AI assistants

## License

MIT
