# Narwhal

[![GitHub Release](https://img.shields.io/github/v/release/dasomel/narwhal)](https://github.com/dasomel/narwhal/releases/latest)
[![License](https://img.shields.io/github/license/dasomel/narwhal)](LICENSE)

[English](README.md) | 한국어

> **Narwhal**(일각고래) - 북극해에 서식하는 고래로, 머리에서 나선형으로 자라는 하나의 긴 엄니(tusk)가 특징입니다. "바다의 유니콘"이라 불리며, 이 프로젝트처럼 단일 클러스터에서 강력한 플랫폼을 제공합니다.

**오픈소스로 만드는 재현 가능한 Kubernetes Internal Developer Platform.**

Narwhal은 Kubernetes 위에 GitOps, IAM/SSO, 서비스 메시, 관측성, 레지스트리, 스토리지, 백업, 정책,
API Gateway, 관리 포털을 통합하여 **설치부터 검증·운영까지 하나의 재현 가능한 플랫폼**으로
제공합니다. 노트북, 클라우드, 온프레미스, 완전 폐쇄망에서 동일한 플랫폼 모델을 구축할 수 있습니다.

> **Narwhal은 Kubernetes 설치 도구가 아닙니다.**
> Kubernetes를 올리는 것은 쉬운 부분입니다. 어려운 것은 서로 독립적으로 개발된 프로젝트들이 DNS,
> 인증서, 아이덴티티, 네트워크, 기동 순서, 버전 호환성에서 함께 동작하도록 만들고, 업그레이드
> 때마다 그 통합을 다시 검증하는 일입니다.
>
> **Narwhal은 바로 그 통합과 운영을 오픈소스로 제공합니다.**

## 왜 Narwhal인가

아래 표의 컴포넌트는 하나하나가 훌륭합니다. 비용은 개별 컴포넌트가 아니라 **그 사이의 이음매**에서
발생합니다.

- API 서버가 받아들이는 `aud` 클레임이 무엇인지
- 서비스 메시가 조용히 손상시키는 SSO 쿠키가 무엇인지
- 네트워크를 끊는 순간 도달 불가가 되는 차트 저장소가 어디인지
- 어제 잘 돌던 파드를 내일 거부하게 될 정책이 무엇인지

Narwhal은 이 이음매를 산출물로 취급합니다. 전부 스크립트화하고 CI에서 검증하며, 버리지 않습니다.

### 통합 경험을 테스트로

Narwhal은 통합 과정에서 발생한 실패를 폐기하지 않습니다. 전부 기록으로 남기며 —
[`lessons-log.md`](docs/common/lessons-log.md)에 날짜별 **263개 사건** — 각 행은 조치 이상을 담습니다.

- 실제 원인이 무엇이었는가
- **비슷해 보이는 원인과 어떻게 구분하는가** (판별자)
- 손이 가는 해결책 중 실제로는 통하지 않는 것은 무엇이며 왜인가

그리고 이 행들은 다시 검사 항목이 됩니다. 이 연결은 의도된 것입니다.

```
사건(Incident) → 교훈(Lesson) → 판별자(Discriminator) → 회귀 테스트(Regression Test)
```

스위트의 검사 항목이 날짜별 사건에 매핑돼 있어, 한 번 해결한 통합 문제가 다음 Kubernetes 또는
컴포넌트 업그레이드에서 조용히 재발하지 못합니다. 커밋 수가 아니라 **이 루프**가 이 프로젝트의
유지보수 방식입니다.

### 프로젝트 현황

| | |
|---|---|
| 활동 | 2026-02-08 이후 483 커밋, 태그 릴리스 4건 (최신 [v1.2.0](CHANGELOG.md)) |
| 검증 | 푸시마다 CI에서 51개 회귀 검사 실행, 그 외 클러스터·SSO·백업·격리 테스트 스크립트 |
| 통합 지식 | [263개 사건 기록](docs/common/lessons-log.md), 최신순, 각 행에 판별자 포함 |
| 배포 대상 | Vagrant (ARM64) · Kakao Cloud (AMD64) · 완전 폐쇄망 |
| 오프라인 설치 | 컨테이너 이미지 105개, Helm 차트 27개, 바이너리·원격 매니페스트·OS 패키지를 아키텍처별 번들로 제공 |
| 통합 컴포넌트 | GitOps로 관리되는 애플리케이션 35개 |

[dasomel/ubuntu-26.04-xfs](https://app.vagrantup.com/dasomel/boxes/ubuntu-26.04-xfs) Box 기반(XFS
파일시스템, Project Quota 지원)이며, 이 Box는 Ubuntu 26.04와 Kubernetes 사전 요구사항을 미리 설치한
Packer 빌드 [kube-ready-box](https://github.com/dasomel/kube-ready-box)의 산출물입니다.

## Features

- **Kubernetes v1.35** - 업스트림 지원 릴리스 브랜치, HA Control Plane (3 masters, 1대 장애 허용)
- **GitOps** - ArgoCD + Gitea (App-of-Apps 패턴)
- **SSO** - Keycloak OIDC (APISIX openid-connect plugin 경유, 앱 연동: ArgoCD, Grafana, Gitea, Harbor, Headlamp)
- **Observability** - Prometheus, Grafana, Loki, Tempo, Hubble
- **Storage** - NFS 퍼시스턴트 스토리지, `nfs.csi.k8s.io` 기반 RWX + SeaweedFS (Object/S3) + [nfs-quota-agent](https://github.com/dasomel/nfs-quota-agent)
- **Backup** - Velero + CNPG barman
- **Service Mesh** - Istio ambient mode (mTLS, zero sidecars, ztunnel)
- **Security** - cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- **Networking** - Cilium (CNI), APISIX (API Gateway, OIDC), MetalLB (LoadBalancer), kube-vip (VIP HA)

## Requirements

- [Vagrant](https://developer.hashicorp.com/vagrant/install) 2.4+
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads) 7.1+ 또는 [VMware Fusion](https://www.vmware.com/products/fusion.html) (개발은 Fusion에서 진행하며, 최소 버전은 확정된 바 없음)
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
│  2 CPU, 6GB      │  2 CPU, 6GB │ 2 CPU, 6GB      │
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

마이너 버전만 표기합니다 — 정확한 차트/이미지 버전, 고정 다이제스트, 업스트림 상태, 알려진 호환성
이슈는 **[VERSIONS.md](VERSIONS.md)가 기준 문서**입니다. 정확한 숫자를 한 파일에만 두는 것은
의도적입니다. 중복된 버전 문자열은 서로 다른 속도로 낡고, 독자는 먼저 마주친 쪽을 믿게 됩니다.

### Base Infrastructure (스크립트 설치)

| Component | Version | Description |
|-----------|---------|-------------|
| Kubernetes | v1.35.x | 컨테이너 오케스트레이션 |
| Cilium | v1.19.x | CNI + kube-proxy 대체 |
| Hubble | v1.19.x | 네트워크 관측성 |
| kube-vip | v1.1.x | 컨트롤 플레인 VIP HA |
| MetalLB | v0.16.x | 베어메탈 LoadBalancer |
| APISIX | 3.15.x | API Gateway (TLS + OIDC, openid-connect plugin) |
| cert-manager | v1.20.x | TLS 자동화 |
| CloudNative-PG | v1.29.x | PostgreSQL Operator |
| Keycloak | 26.5.x | IAM / SSO (Operator) |
| Gitea | v1.26.x | Git 서버 |
| ArgoCD | v3.4.x | GitOps CD |
| Istio | v1.30.x | 서비스 메시 (ambient mode) |

### IDP Apps (ArgoCD GitOps)

| Component | Version | Description |
|-----------|---------|-------------|
| Prometheus Stack | v0.91.x | 모니터링 (Prometheus + Grafana + Alertmanager) |
| Loki | 3.7.x | 로그 집계 |
| Grafana Alloy | v1.17.x | 로그 수집기 (Promtail 대체; Promtail은 2026-03-02 EOL) |
| Tempo | 2.9.x | 분산 트레이싱 |
| Harbor | v2.15.x | 컨테이너 레지스트리. 업스트림 소스를 ARM64용으로 재빌드한 `ghcr.io/dasomel/goharbor` — 포크가 아니라 재빌드이며 소스 수정 없음 |
| OpenBao | v2.5.x | 시크릿 관리 |
| Kyverno | v1.18.x | 정책 엔진 |
| Headlamp | v0.42.x | Kubernetes UI |
| APISIX | 3.15.x | API Gateway (TLS + OIDC) |
| SeaweedFS | v4.34.x | 오브젝트 스토리지 (S3) |
| Velero | v1.18.x | 백업 & 복원 |

## Management Portal

클러스터에는 day-2 운영용 **[Narwhal IDP Portal](https://github.com/dasomel/narwhal-portal)** (Next.js 16 + React 19)이 포함됩니다 — 실시간 메트릭·GitOps 상태·보안·비용·셀프서비스. 설치 후 `https://portal.local.narwhal.internal`로 접속.

| 대시보드 | 아키텍처 |
| :---: | :---: |
| ![Dashboard](docs/images/portal-dashboard.png) | ![Architecture](docs/images/portal-architecture.png) |
| _실시간 메트릭·ArgoCD 앱·알럿_ | _노드·네임스페이스·서비스 그래프_ |
| **보안** | **비용** |
| ![Security](docs/images/portal-security.png) | ![Cost](docs/images/portal-cost.png) |
| _Trivy 취약점 리포트_ | _네임스페이스 비용 분석_ |
| **거버넌스** | **카탈로그** |
| ![Governance](docs/images/portal-governance.png) | ![Catalog](docs/images/portal-catalog.png) |
| _스코어카드·DORA·분포_ | _셀프서비스 앱 카탈로그_ |

> 캡쳐를 [`docs/images/`](docs/images/)에 넣으세요 ([상세](docs/images/README.md)). 전체 UI 갤러리: [narwhal-portal](https://github.com/dasomel/narwhal-portal).

## Access Services

### DNS 접속 (권장)

APISIX API Gateway + cert-manager self-signed TLS를 통해 HTTPS 도메인으로 접속합니다.

DNS 설정: 클라이언트 DNS를 `192.168.56.10`으로 지정하거나 `/etc/hosts`에 추가.

| 서비스 | URL | 자격 증명 |
|--------|-----|-----------|
| ArgoCD | https://argocd.local.narwhal.internal | admin / (자동생성 시크릿) 또는 Keycloak SSO |
| Grafana | https://grafana.local.narwhal.internal | Keycloak SSO (또는 생성된 로컬 admin) |
| Gitea | https://gitea.local.narwhal.internal | Keycloak SSO (또는 생성된 gitea-admin) |
| Harbor | https://harbor.local.narwhal.internal | admin / see show-credentials.sh 또는 Keycloak SSO |
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
# http://localhost:3000 (Keycloak SSO (or the generated local admin))

# Gitea (Git)
kubectl port-forward svc/gitea-http -n devtools 3001:3000
# http://localhost:3001 (gitea-admin / see show-credentials.sh)

# Harbor (Registry)
kubectl port-forward svc/harbor -n devtools 8081:80
# http://localhost:8081 (admin / see show-credentials.sh)

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
- `admin` — cluster-admin
- `dev` — developer
- `view` — viewer
- `guest` — guest

자세한 내용: `scripts/test/show-credentials.sh` — 비밀번호는 클러스터가 생성하므로 문서에 적지 않는다.

## Verification

검증은 세 층위로 나뉘며, 각각 다른 질문에 답합니다.

| 계층 | 규모 | 답하는 질문 |
|---|---|---|
| 클러스터 검증 | 120+ 검사 | 클러스터와 모든 플랫폼 앱이 실제로 정상인가? |
| SSO 검증 | 49 검사 | 연동된 모든 앱에서 인증이 종단 간 동작하는가? |
| **CI 회귀 스위트** | **51 검사** | 이전에 진단된 통합 실패가 재발했는가? |

앞의 둘은 라이브 클러스터를 대상으로 실행하고, 세 번째는 푸시마다 CI에서 실행되며 사건 기록과
연결된 것이 이것입니다.

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

회귀 스위트([`scripts/test/regression-check-kakao.sh`](scripts/test/regression-check-kakao.sh))는
각 검사를 사건 기록의 날짜별 행에 매핑합니다. 무언가 깨질 때마다 스위트가 한 줄씩 자라며, 고쳐진
결함이 조용히 되돌아오지 못하게 하는 장치입니다.

모든 사건이 각자의 CI 검사가 되지는 않습니다. 여러 사건이 하나의 검사로 함께 덮이기도 하고, 일부는
라이브 클러스터에서만 관찰 가능해 클러스터·SSO 스위트에 들어갑니다. 검사 수가 263과 다른 이유는
그것이지, 나머지를 잊어서가 아닙니다.

## 폐쇄망(Air-Gapped) 설치

Narwhal은 Vagrant와 Kakao Cloud 양쪽에서 **인터넷 경로 없이** 설치됩니다. "이미지만 미러링하고
나머지는 되기를 바라는" 방식이 아닙니다. 번들에 컨테이너 이미지, Helm 차트, 바이너리, 원격
매니페스트, OS 패키지가 모두 들어 있고, 격리는 가정이 아니라 강제됩니다.

```bash
# 번들 생성: 이미지 · 차트 · 바이너리 · OS 패키지
./scripts/airgap/02-save-images.sh --list scripts/airgap/images.txt
./scripts/airgap/03-save-helm-charts.sh
./scripts/airgap/07-save-binaries.sh
./scripts/airgap/07-save-apt-packages.sh

# 기본 라우트를 제거하고 APT를 로컬 번들로 전환한 상태로 설치
AIRGAP=1 scripts/up.sh

# 노드가 실제로 격리됐는지 검증 — 라우트, networkd 드롭인,
# 직접 이그레스, 미러 도달성, APT 소스, 메타데이터 경로
scripts/test/verify-isolation.sh local
```

| | |
|---|---|
| 컨테이너 이미지 | 아키텍처별 105개 |
| Helm 차트 | 27개, 클러스터 내 Gitea 패키지 레지스트리에서 제공 |
| 함께 번들되는 것 | helm/cilium/hubble/yq 바이너리, 원격 매니페스트, OS 패키지 149개 |
| 격리 방식 | networkd `UseGateway=false` 드롭인 — `ip route del`과 달리 DHCP 갱신에도 유지 |

`AIRGAP=1`은 APT를 `file:///srv/airgap/apt`로 전환하고 기본 라우트를 제거합니다. 따라서 몰래
인터넷에 접근하는 컴포넌트는 이그레스 프록시 뒤에 숨지 못하고 설치를 실패시킵니다. 자세한 내용은
[`docs/common/airgap-isolation-testing.md`](docs/common/airgap-isolation-testing.md)를 참고하세요.

## GitOps Structure

```
gitops/
├── apps/
│   └── app-of-apps.yaml          # the only Application here; points ArgoCD at charts/narwhal-apps
├── charts/
│   ├── narwhal-apps/             # 35 ArgoCD Applications, rendered by the app-of-apps chart
│   │   └── templates/            #   argocd, harbor, istio*, kyverno, loki, velero, ...
│   ├── narwhal-platform/         # domain-bearing platform resources
│   │   └── templates/            #   apisix-infra/-routes, keycloak-cr, narwhal-portal-k8s, ...
│   └── kubernetes-dashboard/     # vendored (upstream Helm index 404s)
└── resources/                    # raw manifests referenced by the Applications
    ├── cnpg-backup.yaml
    ├── gitea-db.yaml
    ├── harbor-db.yaml
    ├── kisa-compliance.yaml
    ├── network-policies.yaml
    └── ...
```

`gitops/` maps onto the in-cluster Gitea repository that ArgoCD watches: its **contents** are that
repository's root. Changes reach the cluster through
[`scripts/gitops/push-to-gitea.sh`](scripts/gitops/push-to-gitea.sh) — `kubectl apply` is reverted
by selfHeal, and a local commit alone is invisible to ArgoCD.

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

**여기서 인증과 인가는 분리돼 있습니다.** APISIX `openid-connect` 플러그인은 신원만 증명하며 그룹
제한을 두지 않으므로, `narwhal` realm의 모든 사용자가 애플리케이션에 도달합니다. 도달한 뒤 무엇을
할 수 있는지는 각 애플리케이션과 Kubernetes RBAC이 위 매핑대로 강제합니다. 표는 인가를, 아래 문장은
게이트웨이를 설명합니다:

| 계정 | 그룹 | 비밀번호 확인 |
|---------|-----------------|------------------------|
| `admin` | `cluster-admin` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' \| base64 -d` |
| `dev` | `developer` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.dev}' \| base64 -d` |
| `view` | `viewer` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.view}' \| base64 -d` |
| `guest` | `guest` | `kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.guest}' \| base64 -d` |

## Documentation

문서는 **배포 대상**으로 나뉜다 — 플랫폼 계층은 같지만 노드·로드밸런서·DNS·이미지 반입
경로가 전혀 다르다. 인덱스부터 본다: **[docs/README.md](docs/README.md)**.

- [VERSIONS.md](VERSIONS.md) - Component versions

**배포 대상 무관** — [`docs/common/`](docs/common/)
- [architecture.md](docs/common/architecture.md) - 아키텍처 (인프라 절은 Vagrant 기준)
- [kubeconfig.md](docs/common/kubeconfig.md) - kubectl 인증 (cert / token / OIDC)
- [developer-onboarding.md](docs/common/developer-onboarding.md) - 개발자 온보딩
- [database.md](docs/common/database.md) - 데이터베이스(CNPG) 관리
- [gitops-push.md](docs/common/gitops-push.md) - GitOps로 변경 반영
- [troubleshooting.md](docs/common/troubleshooting.md) - 트러블슈팅
- [security.md](docs/common/security.md) - 보안 정책
- [lessons-log.md](docs/common/lessons-log.md) - 사건 기록

**Vagrant (로컬)** — [`docs/vagrant/`](docs/vagrant/)
- [dns-access.md](docs/vagrant/dns-access.md) - `*.local.narwhal.internal` DNS 및 서비스 접근
- [operations.md](docs/vagrant/operations.md) - 운영 가이드
- [disaster-recovery.md](docs/vagrant/disaster-recovery.md) - 장애 복구 런북

**Kakao Cloud** — [`docs/kakao/`](docs/kakao/)
- [cloud-deployment.md](docs/kakao/cloud-deployment.md) - 토폴로지, egress 프록시, airgap 레지스트리
- [service-domains.md](docs/kakao/service-domains.md) - `*.kakao.narwhal.internal` 서비스별 도메인, SSO 방식, 접속
- [csp/kakao-cloud/terraform/README.ko.md](csp/kakao-cloud/terraform/README.ko.md) - Terraform 사용법

## 어떤 분들을 위한 것인가

- 내부 개발자 플랫폼을 **구매하지 않고 오픈소스로 구축**하려는 플랫폼 엔지니어
- 관리형 컨트롤 플레인을 쓸 수 없는 **온프레미스·프라이빗·하이브리드 클라우드** 운영 팀
- 외부 경로 없이 설치와 업그레이드를 마쳐야 하는 **규제 환경 및 폐쇄망**
- 이 컴포넌트들이 어떻게 맞물리는지 **재현 가능한 레퍼런스**가 필요한 Kubernetes 관리자·SRE
- 장난감이 아닌 **실제에 가까운 풀스택 클러스터**를 노트북에서 돌려보려는 개발자

## 클라우드 네이티브 생태계와의 관계

Narwhal은 무엇도 대체하지 않습니다. 독자 컴포넌트를 추가하지 않고 포크도 유지하지 않습니다. 개별
프로젝트가 소유하지 않는 계층, 즉 **이미 존재하는 프로젝트들을 실제 운영 가능한 하나의 플랫폼으로
통합하는 계층**을 제공합니다.

산출물은 개별 컴포넌트가 아니라 그 사이의 통합입니다.

- 통합된 모든 컴포넌트의 버전 및 구성 호환성
- 종단 간 SSO와 인증 흐름
- GitOps 기반 배포와 지속적 reconciliation
- 폐쇄망을 위한 전체 소프트웨어 공급망 번들
- 업그레이드 및 복구 절차
- 부품이 아니라 경계를 검증하는 회귀 스위트

개별 프로젝트가 각자의 테스트를 통과하는 것만으로 플랫폼이 정상이라고 보장되지 않습니다. Narwhal은
**두 개 이상의 프로젝트가 만나는 경계에서 발생하는 문제**를 검증하고 기록하는 것을 자신의 책임으로
삼습니다.

> **Narwhal does not replace the cloud-native ecosystem. It makes the ecosystem work together.**

형제 프로젝트: [narwhal-portal](https://github.com/dasomel/narwhal-portal)(관리 UI),
[kube-ready-box](https://github.com/dasomel/kube-ready-box)(Packer 베이스 Box),
[nfs-quota-agent](https://github.com/dasomel/nfs-quota-agent)(PVC별 NFS 쿼터).

## AI 보조 유지보수

이만큼의 독립 프로젝트를 통합한다는 것은, Kubernetes 릴리스·차트 버전 상향·보안 업데이트마다 구성,
API 동작, 인증, 네트워크, 정책, 스토리지, GitOps reconciliation, 폐쇄망 번들 전반을 다시 검증해야
한다는 뜻입니다. 이 작업은 반복적이며, 소수의 메인테이너 체제에서 가장 확장이 안 되는 부분입니다.

AI 보조 유지보수를 계획 중인 영역:

- 컴포넌트 전반의 업그레이드 영향 분석
- 구성 및 호환성 리뷰
- 사건 기록으로부터의 회귀 테스트 생성
- 이슈 트리아지
- 보안 및 의존성 리뷰
- 릴리스 검증, 체인지로그 및 문서화

목표는 메인테이너를 대체하는 것이 아니라, 통합 플랫폼이 요구하는 반복적인 분석과 검증을 자동화하여
한정된 시간을 아키텍처·신뢰성·커뮤니티에 쓰는 것입니다.

## 로드맵

- Kubernetes 및 컴포넌트 업그레이드 자동화(롤백 포함)
- 컨트롤 플레인 인증서 수명주기 관리(kubeadm 기본값은 1년)
- 사설 CA 강화: OpenBao PKI를 cert-manager Issuer로 연동, 인증서 유효기간 단축
- OpenBao Transit 엔진 기반 KMS etcd 암호화
- 보안 검증 확대 및 지속적 컴플라이언스 리포팅
- Vagrant·Kakao Cloud 외 배포 대상 추가

## 기여하기

기여, 이슈, 피드백을 환영합니다. 큰 변경은 PR 전에 이슈로 먼저 논의해 주세요.

이 저장소에서는 다음 두 가지 관행이 특히 중요합니다.

1. **모든 수정은 [`lessons-log.md`](docs/common/lessons-log.md)에 행을 남깁니다** — 고치다 만든
   실수도 포함하며, 오히려 그쪽이 더 유용한 경우가 많습니다. 결론이 아니라 판별자를 기록하세요.
2. **모든 셸 스크립트는 `set -euo pipefail`을 사용**하고, 셸과 YAML 모두 2칸 들여쓰기를 씁니다. 두
   번째는 CI가 검사하지만 첫 번째는 아무도 검사하지 않으므로, 지우면 조용히 실패합니다.

전체 작업 지침은 [`CLAUDE.md`](CLAUDE.md), 문서 색인은 [`docs/README.md`](docs/README.md)를
참고하세요.

## License

Apache License 2.0 — [LICENSE](LICENSE) 참고.

Narwhal은 많은 서드파티 오픈소스를 조합하며, 적용되는 조건은 **전달 방식**에 따라 달라집니다.
일반 설치는 업스트림 이미지를 **참조**할 뿐 각 레지스트리에서 직접 받아오므로 재배포가 아닙니다.
반면 **에어갭 번들은 전부를 재배포합니다** — 이미지 약 105개를 하나의 산출물로 넘기므로, 의무는
여기에 붙습니다.

[`NOTICE`](NOTICE)가 두 경우를 모두 다루며, 단순 Apache-2.0이 아닌 조건들을 명시합니다:
**AGPL-3.0**(Grafana·Loki·Tempo — 네트워크 이용이 소스 제공 의무를 발생시킴), **Redis 8의
RSALv2/SSPLv1/AGPLv3 삼중 라이선스**(source-available이며 OSI 오픈소스가 아님), **MPL-2.0**(OpenBao),
**GPL-2.0**(BusyBox·FRR). 빌드 도구는 별개입니다 — Vagrant와 Packer는 **BUSL-1.1**이고, 이는 어떤
라이선스 스캐너도 분류하지 못합니다.

이미지별 라이선스는
[`scripts/airgap/lib/component-licenses.tsv`](scripts/airgap/lib/component-licenses.tsv)에 있습니다.
모든 행은 추정이 아니라 업스트림 프로젝트의 라이선스에서 직접 확인했으며,
`08-generate-sbom.sh`가 이를 번들의 CycloneDX SBOM에 실어 보냅니다. 재확인:

```bash
scripts/airgap/lib/refresh-component-licenses.sh --check   # 업스트림이 라이선스를 바꿨으면 exit 1
```

이 검사가 필요한 이유는 프로젝트가 실제로 라이선스를 바꾸기 때문입니다 — Grafana는 AGPL로,
Redis는 삼중 라이선스로 갔습니다. SBOM에 낡은 SPDX id가 남으면 그것이 사실로 하류에 복제됩니다.
