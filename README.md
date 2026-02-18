# Narwhal

> **Narwhal**(일각고래) - 북극해에 서식하는 고래로, 머리에서 나선형으로 자라는 하나의 긴 엄니(tusk)가 특징입니다. "바다의 유니콘"이라 불리며, 이 프로젝트처럼 단일 클러스터에서 강력한 플랫폼을 제공합니다.

Vagrant 기반 Kubernetes Internal Developer Platform (IDP) 클러스터.

[dasomel/ubuntu-24.04-xfs](https://app.vagrantup.com/dasomel/boxes/ubuntu-24.04-xfs) Box 사용 (XFS 파일시스템, Project Quota 지원).

> **Base Box Source**: [kube-ready-box](https://github.com/dasomel/kube-ready-box) - Ubuntu 24.04 + K8s prerequisites 사전 설치된 Packer 기반 Box

## Features

- **Kubernetes v1.35** - 최신 안정 버전, HA Control Plane (3 masters, 1 fault tolerance)
- **GitOps** - ArgoCD + Gitea (App-of-Apps 패턴)
- **SSO** - Keycloak OIDC (6개 앱 연동: ArgoCD, Grafana, Gitea, Harbor, Headlamp, OAuth2-Proxy)
- **Observability** - Prometheus, Grafana, Loki, Tempo, Hubble
- **Storage** - NFS (Block) + SeaweedFS (Object/S3) + [nfs-quota-agent](https://github.com/dasomel/nfs-quota-agent)
- **Backup** - Velero + CNPG barman
- **Service Mesh** - Istio ambient mode (mTLS, zero sidecars, ztunnel)
- **Security** - cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- **Networking** - Cilium (CNI), Traefik (Gateway API), MetalLB (LoadBalancer), kube-vip (VIP HA)

## Requirements

- [Vagrant](https://developer.hashicorp.com/vagrant/install) 2.4+
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads) 7.1+ 또는 [VMware Fusion](https://www.vmware.com/products/fusion.html) 25H2
- 32GB+ RAM (권장 40GB+)
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
vagrant ssh master-1 -c "kubectl get nodes"

# Destroy
vagrant destroy -f
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                  Vagrant VMs                                         │
├──────────────────┬──────────────────┬──────────────────┬─────────────┬───────────────┤
│  master-1        │  master-2        │  master-3        │  worker-1   │  worker-2/3   │
│  192.168.56.10   │  192.168.56.11   │  192.168.56.12   │  .21        │  .22 / .23    │
│  2 CPU, 4GB      │  2 CPU, 4GB      │  2 CPU, 4GB      │  2CPU, 6GB  │  2CPU, 6GB    │
│  NFS, dnsmasq    │  dnsmasq         │  dnsmasq         │             │               │
└──────────────────┴──────────────────┴──────────────────┴─────────────┴───────────────┘
         │                   │                   │               │              │
         └───────────────────┼───────────────────┼───────────────┼──────────────┘
                             │                   │               │
              ┌──────────────┴───────────────────┘
              │  VIP: 192.168.56.100        │
              │       (kube-vip API HA)     │
              │  LB:  192.168.56.200        │
              │       (MetalLB/Traefik)     │
              │  DNS: 192.168.56.10:53      │
              │       (*.local.narwhal.io)  │
              └─────────────────────────────┘
```

## Components

### Base Infrastructure (Script-installed)

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35.1 | Container orchestration |
| Cilium | v1.19.0 | CNI + kube-proxy replacement |
| Hubble | v1.19.0 | Network observability |
| kube-vip | v1.0.4 | Control plane VIP HA |
| MetalLB | v0.15.3 | Bare-metal LoadBalancer |
| Traefik | v3.6.7 | Gateway API Controller |
| cert-manager | v1.19.3 | TLS automation |
| CloudNative-PG | v1.28.1 | PostgreSQL Operator |
| Keycloak | v26.5.3 | IAM / SSO (Operator) |
| Gitea | v1.25.4 | Git server |
| ArgoCD | v3.3.0 | GitOps CD |
| Istio | v1.29.0 | Service mesh (ambient mode) |

### IDP Apps (ArgoCD GitOps)

| Component | Chart Version | App Version | Description |
|-----------|---------------|-------------|-------------|
| Prometheus Stack | 81.5.1 | v0.88.1 | Monitoring (Prometheus + Grafana + Alertmanager) |
| Loki | 6.52.0 | 3.6.4 | Log aggregation |
| Promtail | 6.17.1 | 3.5.1 | Log collector |
| Tempo | 1.24.4 | 2.9.0 | Distributed tracing |
| Harbor | 1.18.2 | 2.14.2 | Container registry (ARM64) |
| OpenBao | 0.11.0 | v2.2.0 | Secret management |
| Kyverno | 3.7.0 | v1.17.0 | Policy engine |
| Headlamp | 0.40.0 | 0.40.0 | Kubernetes UI |
| OAuth2-Proxy | 10.1.3 | 7.14.2 | SSO Gateway Proxy |
| SeaweedFS | 4.0.407 | 4.07 | Object storage (S3) |
| Velero | 11.3.2 | 1.17.1 | Backup & Restore |

See [VERSIONS.md](VERSIONS.md) for full version list.

## Access Services

### DNS 접속 (권장)

Traefik Gateway + cert-manager self-signed TLS를 통해 HTTPS 도메인으로 접속합니다.

DNS 설정: 클라이언트 DNS를 `192.168.56.10`으로 지정하거나 `/etc/hosts`에 추가.

| 서비스 | URL | 자격 증명 |
|--------|-----|-----------|
| ArgoCD | https://argocd.local.narwhal.io | admin / (자동생성 시크릿) 또는 Keycloak SSO |
| Grafana | https://grafana.local.narwhal.io | admin / admin 또는 Keycloak SSO |
| Gitea | https://gitea.local.narwhal.io | gitea-admin / gitea-admin 또는 Keycloak SSO |
| Harbor | https://harbor.local.narwhal.io | admin / Harbor12345 또는 Keycloak SSO |
| Keycloak | https://keycloak.local.narwhal.io | temp-admin / (자동생성) |
| Headlamp | https://headlamp.local.narwhal.io | Keycloak SSO |
| OAuth2-Proxy | https://oauth2-proxy.local.narwhal.io | Keycloak SSO |
| OpenBao | https://openbao.local.narwhal.io | root token (`bao operator init`) |
| Hubble | https://hubble.local.narwhal.io | - |

> **Note**: Self-signed 인증서 사용으로 브라우저에서 보안 경고가 표시됩니다. "고급" → "계속 진행"으로 접속하세요.

### Port-Forward 접속 (대안)

```bash
# ArgoCD (GitOps)
kubectl port-forward svc/argocd-server -n argocd 8443:443
# https://localhost:8443 (admin / kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d)

# Keycloak (IAM)
kubectl port-forward svc/keycloak-service -n keycloak 8080:8080
# http://localhost:8080

# Grafana (Monitoring)
kubectl port-forward svc/prometheus-stack-grafana -n monitoring 3000:80
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

모든 앱이 Keycloak OIDC로 연동됩니다. (HTTPS 필수, K8s 1.35+)

| SSO 연동 앱 | Client ID | 인증 방식 |
|-------------|-----------|-----------|
| ArgoCD | `argocd` | OIDC config in argocd-cm |
| Grafana | `grafana` | grafana.ini auth.generic_oauth |
| Gitea | `gitea` | OAuth2 auth source (openidConnect) |
| Harbor | `harbor` | configureUserSettings OIDC |
| Headlamp | `headlamp` | OIDC config + CA cert mount |
| OAuth2-Proxy | `oauth2-proxy` | keycloak-oidc provider |

| Group | K8s Role | App Role |
|-------|----------|----------|
| cluster-admins | cluster-admin | Admin |
| developers | edit | Editor |
| viewers | view | Viewer |

**Default Users:**
- `k8s-admin` / `k8s-admin` (cluster-admins)
- `developer` / `developer` (developers)

자세한 내용: [docs/keycloak-accounts.md](docs/keycloak-accounts.md)

## Verification

클러스터 상태 검증:

```bash
# 전체 검증 (120+ checks)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/cluster/verify-cluster.sh"

# Phase 1만 (클러스터 인프라)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/cluster/verify-cluster.sh --stage=phase1"

# Phase 2만 (플랫폼 앱)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/cluster/verify-cluster.sh --stage=phase2-apps"

# SSO 테스트 (49 checks)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh"

# 빠른 확인
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
│   ├── oauth2-proxy.yaml
│   ├── openbao.yaml
│   ├── kyverno.yaml
│   ├── seaweedfs.yaml
│   ├── velero.yaml
│   ├── traefik.yaml
│   ├── istio-base.yaml
│   ├── istiod.yaml
│   ├── istio-cni.yaml
│   └── ztunnel.yaml
└── resources/               # K8s Resources
    ├── gitea-db.yaml
    ├── harbor-db.yaml
    ├── cnpg-backup.yaml
    ├── kyverno-policies.yaml
    ├── metallb-config.yaml
    ├── traefik-routes.yaml   # HTTPRoutes & Gateway
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

`Vagrantfile` 변수:

```ruby
K8S_VERSION = "1.35"           # Kubernetes version
MASTER_COUNT = 3               # Master nodes (HA, 1 fault tolerance)
WORKER_COUNT = 3               # Worker nodes
MASTER_MEMORY = 4096           # Master RAM (MB) - control-plane only (NoSchedule taint)
WORKER_MEMORY = 6144           # Worker RAM (MB) - platform apps run here
VIP_ADDRESS = "192.168.56.100" # Control plane VIP
```

## Commands

```bash
# Start cluster (Phase 1 + 2 자동 실행)
vagrant up --provider=vmware_desktop

# Start specific node
vagrant up master-1
vagrant up worker-1

# SSH access
vagrant ssh master-1

# Phase 2만 수동 실행 (클러스터 구성 후)
vagrant provision master-1 --provision-with phase2-platform

# Reprovision
vagrant provision master-1

# Halt
vagrant halt

# Destroy
vagrant destroy -f
```

## Documentation

- [VERSIONS.md](VERSIONS.md) - Component versions
- [docs/architecture.md](docs/architecture.md) - 아키텍처 상세
- [docs/KEYCLOAK-SSO.md](docs/KEYCLOAK-SSO.md) - Keycloak SSO 상세 설정
- [docs/keycloak-accounts.md](docs/keycloak-accounts.md) - Keycloak SSO 계정 및 설정 가이드
- [docs/DNS-ACCESS.md](docs/DNS-ACCESS.md) - DNS 설정 및 서비스 접근
- [docs/KUBECONFIG.md](docs/KUBECONFIG.md) - kubeconfig 및 OIDC 인증
- [docs/DATABASE.md](docs/DATABASE.md) - 데이터베이스(CNPG) 관리
- [docs/OPERATIONS.md](docs/OPERATIONS.md) - 운영 가이드
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - 트러블슈팅 가이드
- [docs/SECURITY.md](docs/SECURITY.md) - 보안 정책

## License

MIT
