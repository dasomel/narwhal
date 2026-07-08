# Narwhal

[![GitHub Release](https://img.shields.io/github/v/release/dasomel/narwhal)](https://github.com/dasomel/narwhal/releases/latest)
[![License](https://img.shields.io/github/license/dasomel/narwhal)](LICENSE)

[English](README.md) | 한국어

> **Narwhal**(일각고래) - 북극해에 서식하는 고래로, 머리에서 나선형으로 자라는 하나의 긴 엄니(tusk)가 특징입니다. "바다의 유니콘"이라 불리며, 이 프로젝트처럼 단일 클러스터에서 강력한 플랫폼을 제공합니다.

Vagrant 기반 Kubernetes Internal Developer Platform (IDP) 클러스터.

[dasomel/ubuntu-26.04-xfs](https://app.vagrantup.com/dasomel/boxes/ubuntu-26.04-xfs) Box 사용 (XFS 파일시스템, Project Quota 지원).

> **Base Box Source**: [kube-ready-box](https://github.com/dasomel/kube-ready-box) - Ubuntu 26.04 + K8s prerequisites 사전 설치된 Packer 기반 Box

## Features

- **Kubernetes v1.35** - 최신 안정 버전, HA Control Plane (3 masters, 1 fault tolerance)
- **GitOps** - ArgoCD + Gitea (App-of-Apps 패턴)
- **SSO** - Keycloak OIDC (APISIX openid-connect plugin 경유, 앱 연동: ArgoCD, Grafana, Gitea, Harbor, Headlamp)
- **Observability** - Prometheus, Grafana, Loki, Tempo, Hubble
- **Storage** - NFS (Block) + SeaweedFS (Object/S3) + [nfs-quota-agent](https://github.com/dasomel/nfs-quota-agent)
- **Backup** - Velero + CNPG barman
- **Service Mesh** - Istio ambient mode (mTLS, zero sidecars, ztunnel)
- **Security** - cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- **Networking** - Cilium (CNI), APISIX (API Gateway, OIDC), MetalLB (LoadBalancer), kube-vip (VIP HA)

## Requirements

- [Vagrant](https://developer.hashicorp.com/vagrant/install) 2.4+
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads) 7.1+ 또는 [VMware Fusion](https://www.vmware.com/products/fusion.html) 26H1
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
| Harbor | 1.19.1 | v2.15.1 (`:latest`; v2.15.2 게시됨 but `:latest` 미승격 — VERSIONS.md 참고) | Container registry (ARM64, ghcr.io/dasomel/goharbor) |
| OpenBao | 0.28.3 | v2.5.4 | Secret management |
| Kyverno | 3.8.1 | v1.18.1 | Policy engine |
| Headlamp | 0.42.0 | v0.42.0 | Kubernetes UI |
| APISIX | 2.13.0 | 3.15.0 | API Gateway (TLS + OIDC) |
| SeaweedFS | 4.34.0 | v4.34 | Object storage (S3) |
| Velero | 12.0.3 | v1.18.1 | Backup & Restore |

See [VERSIONS.md](VERSIONS.md) for full version list.

## Management Portal

클러스터에는 day-2 운영용 **[Narwhal IDP Portal](https://github.com/dasomel/narwhal-portal)** (Next.js 16 + React 19)이 포함됩니다 — 실시간 메트릭·GitOps 상태·보안·비용·셀프서비스. 설치 후 `https://portal.local.narwhal.internal`로 접속.

| 대시보드 | 아키텍처 |
| :---: | :---: |
| ![Portal — Dashboard](docs/images/portal-dashboard.png) | ![Portal — Architecture](docs/images/portal-architecture.png) |
| _실시간 메트릭·ArgoCD 앱·알럿_ | _노드·네임스페이스·서비스 그래프_ |

> 캡쳐를 [`docs/images/`](docs/images/)에 넣으세요 ([상세](docs/images/README.md)). 전체 UI 갤러리: [narwhal-portal](https://github.com/dasomel/narwhal-portal).

## Access Services

### DNS 접속 (권장)

APISIX API Gateway + cert-manager self-signed TLS를 통해 HTTPS 도메인으로 접속합니다.

DNS 설정: 클라이언트 DNS를 `192.168.56.10`으로 지정하거나 `/etc/hosts`에 추가.

| 서비스 | URL | 자격 증명 |
|--------|-----|-----------|
| ArgoCD | https://argocd.local.narwhal.internal | admin / (자동생성 시크릿) 또는 Keycloak SSO |
| Grafana | https://grafana.local.narwhal.internal | admin / admin 또는 Keycloak SSO |
| Gitea | https://gitea.local.narwhal.internal | gitea-admin / gitea-admin 또는 Keycloak SSO |
| Harbor | https://harbor.local.narwhal.internal | admin / Harbor12345 또는 Keycloak SSO |
| Keycloak | https://keycloak.local.narwhal.internal | temp-admin / (자동생성) |
| Headlamp | https://headlamp.local.narwhal.internal | Keycloak SSO |
| OpenBao | https://openbao.local.narwhal.internal | root token (`bao operator init`) |
| Hubble | https://hubble.local.narwhal.internal | - |

> **Note**: Self-signed 인증서 사용으로 브라우저에서 보안 경고가 표시됩니다. "고급" → "계속 진행"으로 접속하세요.

### Port-Forward 접속 (대안)

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

모든 앱이 Keycloak OIDC로 연동됩니다. (HTTPS 필수, K8s 1.35+)

| SSO 연동 앱 | Client ID | 인증 방식 |
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
| guest | - | - (웹 UI only) |

**Default Users:**
- `admin` / `admin` (cluster-admin)
- `dev` / `dev` (developer)
- `view` / `view` (viewer)
- `guest` / `guest` (guest)

자세한 내용: [docs/keycloak-accounts.md](docs/keycloak-accounts.md)

## Verification

클러스터 상태 검증:

```bash
# 전체 검증 (120+ checks)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"

# Phase 1만 (클러스터 인프라)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh --stage=phase1"

# Phase 2만 (플랫폼 앱)
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh --stage=phase2-apps"

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

`Vagrantfile` 변수:

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

### SSO 사용자 계정

narwhal realm 전체 사용자가 접근 가능 (openid-connect 플러그인은 인증만, 그룹 제한 없음):

| 계정 | 그룹 | 비밀번호 확인 |
|---------|-----------------|------------------------|
| `admin` | `cluster-admin` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' \| base64 -d` |
| `dev` | `developer` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.dev}' \| base64 -d` |
| `view` | `viewer` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.view}' \| base64 -d` |
| `guest` | `guest` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.guest}' \| base64 -d` |

## Documentation

- [VERSIONS.md](VERSIONS.md) - Component versions
- [docs/architecture.md](docs/architecture.md) - 아키텍처 상세
- [docs/keycloak-sso.md](docs/keycloak-sso.md) - Keycloak SSO 상세 설정
- [docs/keycloak-accounts.md](docs/keycloak-accounts.md) - Keycloak SSO 계정 및 설정 가이드
- [docs/dns-access.md](docs/dns-access.md) - DNS 설정 및 서비스 접근
- [docs/kubeconfig.md](docs/kubeconfig.md) - kubeconfig 및 OIDC 인증
- [docs/database.md](docs/database.md) - 데이터베이스(CNPG) 관리
- [docs/operations.md](docs/operations.md) - 운영 가이드
- [docs/rtk-token-compression-policy.md](docs/rtk-token-compression-policy.md) - RTK 토큰 압축 정책
- [docs/troubleshooting.md](docs/troubleshooting.md) - 트러블슈팅 가이드
- [docs/security.md](docs/security.md) - 보안 정책

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.
