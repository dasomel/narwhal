# Narwhal - Platform Architecture

Narwhal은 Vagrant VM 기반의 Kubernetes Internal Developer Platform (IDP) 클러스터 자동 구축 프로젝트입니다.
로컬 환경에서 프로덕션 수준의 IDP 스택을 원클릭으로 프로비저닝합니다.

> 이 문서의 인프라 계층(노드 IP, kube-vip, MetalLB)은 **Vagrant 기준**입니다. 플랫폼 계층
> (APISIX, Keycloak, ArgoCD, 스토리지 등)은 배포 대상과 무관하게 동일합니다.
> 퍼블릭 클라우드 배포에서 무엇이 달라지는지는 [`cloud-deployment.md`](../kakao/cloud-deployment.md) 참고.

---

## Infrastructure Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Host Machine (macOS)                              │
│                                                                             │
│  Vagrant + VMware Desktop / VirtualBox                                      │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐           │
│  │ narwhal-master-1 │  │ narwhal-master-2 │  │ narwhal-master-3 │           │
│  │ 192.168.56.10    │  │ 192.168.56.11    │  │ 192.168.56.12    │           │
│  │ control-plane    │  │ control-plane    │  │ control-plane    │           │
│  │ NFS Server       │  │ dnsmasq          │  │ dnsmasq          │           │
│  │ dnsmasq          │  │                  │  │                  │           │
│  │ 2 CPU / 4GB RAM  │  │ 2 CPU / 4GB RAM  │  │ 2 CPU / 4GB RAM  │           │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘           │
│           └──────────────────┬──┘                     │                     │
│                              └───────────┬────────────┘                     │
│                                          │                                  │
│            VIP: 192.168.56.100 (kube-vip, ARP leader election)              │
│            etcd 3-node: quorum=2/3 (1 fault tolerance)                      │
│                                          │                                  │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐        │
│  │ narwhal-worker-1  │  │ narwhal-worker-2  │  │ narwhal-worker-3  │        │
│  │ 192.168.56.21     │  │ 192.168.56.22     │  │ 192.168.56.23     │        │
│  │ worker            │  │ worker            │  │ worker            │        │
│  │ 2 CPU / 6GB RAM   │  │ 2 CPU / 6GB RAM   │  │ 2 CPU / 6GB RAM   │        │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘        │
│                                                                             │
│  LB:  192.168.56.200 (MetalLB → APISIX)                                     │
│  DNS: *.local.narwhal.internal → 192.168.56.200                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### VM Specifications

| Node | IP | Role | CPU | Memory |
|------|----|------|-----|--------|
| narwhal-master-1 | 192.168.56.10 | control-plane (NoSchedule), NFS Server, dnsmasq | 2 | 4 GiB |
| narwhal-master-2 | 192.168.56.11 | control-plane (NoSchedule), dnsmasq | 2 | 4 GiB |
| narwhal-master-3 | 192.168.56.12 | control-plane (NoSchedule), dnsmasq | 2 | 4 GiB |
| narwhal-worker-1 | 192.168.56.21 | worker | 2 | 6 GiB |
| narwhal-worker-2 | 192.168.56.22 | worker | 2 | 6 GiB |
| narwhal-worker-3 | 192.168.56.23 | worker | 2 | 6 GiB |

### Network

| CIDR | Purpose |
|------|---------|
| 192.168.56.0/24 | Node network (private) |
| 192.168.56.100 | Control Plane VIP (kube-vip) |
| 192.168.56.200-220 | MetalLB IP Pool (LoadBalancer) |
| 10.244.0.0/16 | Pod CIDR |
| 10.96.0.0/12 | Service CIDR |

---

## Platform Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Client Access                               │
│                                                                      │
│   Browser ──→ *.local.narwhal.internal ──→ dnsmasq (192.168.56.10:53)      │
│                        │                                             │
│                        ▼                                             │
│              MetalLB (192.168.56.200)                                │
│                        │                                             │
│                        ▼                                             │
│              ┌─────────────────┐                                     │
│              │     APISIX      │  API Gateway (ApisixRoute/ApisixTls)│
│              │  (LoadBalancer) │  OIDC via openid-connect plugin     │
│              └────────┬────────┘                                     │
│                       │                                              │
│         ┌─────────────┼─────────────────────────┐                    │
│         │             │             │             │                  │
│         ▼             ▼             ▼             ▼                  │
│    ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐              │
│    │ Grafana │  │ Harbor   │  │Keycloak │  │ OpenBao  │              │
│    │ Hubble  │  │ Headlamp │  │OAuth2   │  │ ArgoCD   │              │
│    └─────────┘  └──────────┘  └─────────┘  └──────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

### Traffic Flow

1. **DNS Resolution**: Client → dnsmasq (master node) → `*.local.narwhal.internal` → `192.168.56.200`
2. **Load Balancing**: MetalLB L2 Advertisement → APISIX LoadBalancer Service
3. **Routing**: APISIX ApisixRoute → Backend Service (by hostname)
4. **Authentication**: APISIX openid-connect plugin → Keycloak OIDC

### ApisixRoute Mappings

| Hostname | Backend Service | Namespace |
|----------|----------------|-----------|
| argocd.local.narwhal.internal | argocd-server | devtools |
| grafana.local.narwhal.internal | prometheus-stack-grafana | monitoring |
| gitea.local.narwhal.internal | gitea-http | devtools |
| harbor.local.narwhal.internal | harbor | devtools |
| keycloak.local.narwhal.internal | keycloak-service | iam |
| headlamp.local.narwhal.internal | headlamp | devtools |
| openbao.local.narwhal.internal | openbao-ui | storage |
| hubble.local.narwhal.internal | hubble-ui | kube-system |

---

## Component Architecture

### Layer Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                      GitOps Layer                            │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────────────┐  │
│  │  ArgoCD  │  │  Gitea   │  │  App-of-Apps (18 apps)     │  │
│  │  v3.3.0  │  │  v1.25.4 │  │  automated sync + prune    │  │
│  └──────────┘  └──────────┘  └────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                    Application Layer                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐    │
│  │ Harbor   │  │ Headlamp │  │ OpenBao  │  │   Velero   │    │
│  │ Registry │  │ K8s UI   │  │ Secrets  │  │   Backup   │    │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘    │
├──────────────────────────────────────────────────────────────┤
│                   Observability Layer                        │
│  ┌────────────┐  ┌────────┐  ┌──────────┐  ┌────────────┐    │
│  │ Prometheus │  │  Loki  │  │  Tempo   │  │   Alloy    │    │
│  │ + Grafana  │  │  Logs  │  │  Traces  │  │  Collector │    │
│  └────────────┘  └────────┘  └──────────┘  └────────────┘    │
├──────────────────────────────────────────────────────────────┤
│                 Security & Policy Layer                      │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │ Keycloak   │  │ cert-manager │  │     Kyverno        │    │
│  │ IAM / SSO  │  │ TLS auto     │  │  Policy Engine     │    │
│  │ OIDC       │  │              │  │                    │    │
│  └────────────┘  └──────────────┘  └────────────────────┘    │
├──────────────────────────────────────────────────────────────┤
│                  Service Mesh Layer                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                    │
│  │ istiod   │  │ ztunnel  │  │ istio-cni│                    │
│  │ control  │  │ node     │  │ ambient  │                    │
│  │ plane    │  │ proxy    │  │ redirect │                    │
│  └──────────┘  └──────────┘  └──────────┘                    │
│  Istio v1.29 ambient mode — mTLS (PERMISSIVE), zero sidecars │
├──────────────────────────────────────────────────────────────┤
│                   Networking Layer                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐    │
│  │ Cilium   │  │  APISIX  │  │ MetalLB  │  │  kube-vip  │    │
│  │ CNI +    │  │ API GW   │  │ L2 LB    │  │  CP VIP    │    │
│  │ Hubble   │  │ OIDC     │  │          │  │            │    │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘    │
├──────────────────────────────────────────────────────────────┤
│                    Storage Layer                             │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │  NFS Server  │  │ csi-driver   │  │  nfs-quota-agent   │  │
│  │  XFS + prjqt │  │ nfs          │  │  quota enforcement │  │
│  ├──────────────┤  ├──────────────┤  ├────────────────────┤  │
│  │  SeaweedFS   │  │ CloudNative  │  │  PostgreSQL 18     │  │
│  │  S3 Storage  │  │ PG Operator  │  │  HA Clusters       │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│                  Kubernetes v1.35                            │
│              containerd v1.7 | Ubuntu 24.04 LTS              │
│             Vagrant VMs (VMware Desktop / VirtualBox)        │
└──────────────────────────────────────────────────────────────┘
```

---

## Storage Architecture

```
narwhal-master (/srv/nfs/k8s)
│
├── NFS Server (nfs-kernel-server)
│   └── XFS filesystem with project quotas (prjquota)
│
├── csi-driver-nfs ──→ Dynamic PV Provisioning
│   └── StorageClass: nfs-csi (default)
│
├── nfs-quota-agent
│   ├── PV 감시 → 디렉토리별 project quota 설정
│   ├── 고아 디렉토리 자동 정리
│   ├── 사용량 추이 기록
│   └── Web UI (대시보드)
│
└── SeaweedFS (S3-compatible)
    ├── Velero 백업 저장소
    └── CNPG WAL 아카이빙 (향후)

PostgreSQL HA (CloudNative-PG)
└── narwhal-db (1 primary + 1 PgBouncer)
    ├── keycloak database
    ├── harbor database
    └── gitea database
```

### PV/PVC Storage Mapping

| Application | StorageClass | Size | Purpose |
|-------------|-------------|------|---------|
| Prometheus | nfs-csi | 10 GiB | Metrics TSDB |
| Alertmanager | nfs-csi | 5 GiB | Alert data |
| Grafana | nfs-csi | 5 GiB | Dashboard persistence |
| Loki | nfs-csi | 10 GiB | Log index/chunks |
| Tempo | nfs-csi | 10 GiB | Trace data |
| Harbor Registry | nfs-csi | 20 GiB | Container images |
| OpenBao | nfs-csi | 10+5 GiB | Secrets + Audit |
| SeaweedFS | nfs-csi | 1+50+5 GiB | Master + Volume + Filer |
| PostgreSQL (narwhal-db) | nfs-csi | 10 GiB | Unified DB cluster |

---

## Identity & SSO Architecture

```
┌────────────────────────────────────────────────────┐
│                   Keycloak                         │
│              Realm: kubernetes                     │
│                                                    │
│  Users:                                            │
│  ├── admin   → Group: cluster-admin                 │
│  ├── dev     → Group: developer                    │
│  ├── view    → Group: viewer                       │
│  └── guest   → Group: guest                        │
│                                                    │
│  Clients (OIDC):                                   │
│  ├── kubernetes   (public)  → K8s API Server       │
│  ├── argocd       (secret)  → ArgoCD               │
│  ├── grafana      (secret)  → Grafana              │
│  ├── gitea        (secret)  → Gitea                │
│  ├── harbor       (secret)  → Harbor               │
│  └── headlamp     (secret)  → Headlamp             │
│                                                    │
│  Exposed via:                                      │
│  └── HTTPRoute (HTTPS) → keycloak.local.narwhal.internal │
└────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────┐
│          Kubernetes API Server                        │
│                                                       │
│  --oidc-issuer-url=https://keycloak.local.narwhal.internal/ │
│          realms/kubernetes                            │
│  --oidc-client-id=kubernetes                          │
│  --oidc-username-claim=preferred_username             │
│  --oidc-groups-claim=groups                           │
│  --oidc-username-prefix=oidc:                         │
│  --oidc-groups-prefix=oidc:                           │
│                                                       │
│  RBAC:                                                │
│  ├── oidc:cluster-admin → ClusterRole: cluster-admin  │
│  ├── oidc:developer    → RoleBinding: edit (dev NS)   │
│  └── oidc:viewer       → RoleBinding: view            │
└───────────────────────────────────────────────────────┘
```

### Gateway-Level SSO (APISIX openid-connect plugin)

모든 웹 앱은 APISIX의 `openid-connect` 플러그인을 통해 Gateway 레벨에서 Keycloak OIDC 인증을 강제한다.

```
Browser → argocd.local.narwhal.internal
    │
    ▼
┌─────────────────────────────────────────────────┐
│  APISIX API Gateway (platform-system)           │
│                                                 │
│  1. openid-connect 플러그인 토큰 검증           │
│     ├── 유효한 세션 → 앱으로 전달              │
│     └── 미인증 → Keycloak 로그인으로 리다이렉트 │
│                                                 │
│  2. Keycloak → OIDC 로그인                     │
│     └── 인증 성공 → 쿠키 설정 → 앱 리다이렉트  │
│                                                 │
│  3. 앱별 SSO 자동 로그인 (Keycloak 세션 공유)   │
└─────────────────────────────────────────────────┘
```

**OIDC 적용 현황:**

| 앱 | OIDC | SSO 리다이렉트 경로 | 비고 |
|---|---|---|---|
| ArgoCD | ✅ | `/auth/login?return_url=...%2Fapplications` | OIDC → 자동 로그인 |
| Grafana | ✅ | `/login/generic_oauth` | OAuth → 자동 로그인 |
| Gitea | ✅ | `/user/oauth2/keycloak` | OAuth → 자동 로그인 |
| Harbor | ✅ | `/c/oidc/login` | OIDC → 자동 로그인 |
| Headlamp | ✅ | (SPA, 기본 경로) | 클라이언트사이드 OIDC |
| OpenBao | ✅ | (SPA, 기본 경로) | 토큰 인증 |
| Hubble | ✅ | (자체 SSO 없음) | APISIX OIDC만 |
| Keycloak | ❌ | — | IAM 자체 |

**쿠키 & 세션 관리:**

| 쿠키 | 도메인 | 만료 | 용도 |
|---|---|---|---|
| `argocd.token` | `argocd.local.narwhal.internal` | 앱별 | ArgoCD 세션 |
| `grafana_session` | `grafana.local.narwhal.internal` | 앱별 | Grafana 세션 |
| `i_like_gitea` | `gitea.local.narwhal.internal` | 앱별 | Gitea 세션 |
| `sid` | `harbor.local.narwhal.internal` | 앱별 | Harbor 세션 |

**테스트 & 트러블슈팅 가이드:**

```bash
# 1. 다른 사용자로 테스트 — 프라이빗/시크릿 창 사용
#    Chrome: Ctrl+Shift+N / macOS: Cmd+Shift+N
#    Firefox: Ctrl+Shift+P / macOS: Cmd+Shift+P
#    → 창 닫으면 모든 쿠키 자동 삭제

# 2. CLI로 인증 상태 확인 (401/302 예상):
curl -sk -o /dev/null -w '%{http_code}' https://argocd.local.narwhal.internal

# 3. APISIX 로그 확인 (인증 실패 디버깅)
kubectl logs -n platform-system -l app.kubernetes.io/name=apisix --tail=20

# 4. Keycloak 세션 강제 만료 (모든 사용자 로그아웃)
#    Keycloak Admin Console → Sessions → Sign out all
#    또는 특정 사용자: Users → 해당 사용자 → Sessions → Logout all
```

> **팁**: 개발 중에는 프라이빗 창을 사용하면 매번 쿠키를 지울 필요가 없다.
> 창을 닫으면 깨끗한 상태로 다시 시작할 수 있다.

---

## Observability Stack

```
┌────────────────────────────────────────────────────────┐
│                    Grafana Dashboard                   │
│        https://grafana.local.narwhal.internal (admin/admin)  │
├──────────┬──────────────┬──────────────────────────────┤
│          │              │                              │
│    Metrics         Logs              Traces            │
│          │              │                              │
│    ┌─────▼─────┐  ┌────▼────┐  ┌──────▼──────┐         │
│    │Prometheus │  │  Loki   │  │   Tempo     │         │
│    │  7d ret.  │  │ Single  │  │  Local      │         │
│    │  TSDB     │  │ Binary  │  │  Backend    │         │
│    └─────▲─────┘  └────▲────┘  └─────────────┘         │
│          │             │                               │
│    ┌─────┴─────┐  ┌────┴────┐                          │
│    │node-export│  │ Alloy   │  (DaemonSet)             │
│    │kube-state │  │         │                          │
│    │  metrics  │  │ All     │                          │
│    │  kubelet  │  │ Nodes   │                          │
│    └───────────┘  └─────────┘                          │
└────────────────────────────────────────────────────────┘
```

### 알림 규칙

PrometheusRule `narwhal-alerts`로 17개 알림 규칙 관리:

| 그룹 | 규칙 수 | 주요 알림 |
|------|---------|----------|
| cluster-health | 4 | 노드 NotReady, API 서버/etcd/CoreDNS 다운 |
| node-health | 4 | 디스크/메모리 압력, CPU 90%+, 디스크 85%+ |
| platform-apps | 6 | 주요 앱 다운, ArgoCD OutOfSync |
| database | 3 | CNPG 비정상, 복제 지연, 연결 포화 |
| certificates | 2 | 인증서 만료 7일전, Ready=False |

AlertmanagerConfig로 severity별 라우팅:
- **critical**: 1시간 반복, 서비스 장애
- **warning**: 4시간 반복, 성능/용량 문제

---

## GitOps Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     ArgoCD (v3.3.0)                      │
│                                                          │
│  App-of-Apps Pattern                                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │              idp-apps (App-of-Apps)                │  │
│  │              auto sync + prune + self-heal         │  │
│  └─────────────────────┬──────────────────────────────┘  │
│                        │                                 │
│    ┌──────────┬────────┼────────┬──────────┬───────┐     │
│    ▼          ▼        ▼        ▼          ▼       ▼     │
│  cert-mgr  prometheus loki   apisix    harbor  velero    │
│  kyverno   alloy      tempo  metallb   openbao headlamp  │
│  seaweedfs istio-base  istiod                            │
│  istio-cni ztunnel                                       │
│                                                          │
│  Source: Gitea (gitea-admin/narwhal-gitops)              │
│  Target: https://kubernetes.default.svc                  │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                     Gitea (v1.25.4)                      │
│                                                          │
│  Repository: narwhal-gitops                              │
│  ├── apps/              (ArgoCD Application manifests)   │
│  │   ├── app-of-apps.yaml                                │
│  │   ├── cert-manager.yaml                               │
│  │   ├── prometheus-stack.yaml                           │
│  │   ├── loki.yaml                                       │
│  │   ├── ... (18 apps)                                   │
│  └── resources/         (Shared K8s resources)           │
│      ├── metallb-config.yaml                             │
│      ├── apisix-routes.yaml                             │
│      ├── harbor-db.yaml                                  │
│      ├── gitea-db.yaml                                   │
│      ├── grafana-datasources.yaml                        │
│      ├── kyverno-policies.yaml                           │
│      └── cnpg-backup.yaml                                │
│                                                          │
│  Backend: PostgreSQL HA (CNPG narwhal-db, shared)        │
│  Cache: Valkey (in-cluster)                              │
└──────────────────────────────────────────────────────────┘
```

---

## CI/CD 파이프라인

GitHub Actions 워크플로우:

| 워크플로우 | 트리거 | 검사 내용 |
|-----------|--------|----------|
| lint.yml | PR/push | shellcheck, YAML 검증, kubeconform |
| version-check.yml | PR | VERSIONS.md 동기화 검사 |
| release.yml | v* 태그 | GitHub Release 자동 생성 |

로컬 검증:
```bash
make lint      # shellcheck + yamllint
make validate  # Vagrantfile + yq 검증
```

---

## Provisioning Flow

클러스터는 Vagrant에 의해 순차적으로 프로비저닝됩니다. (3-master HA, 1 fault tolerance)

### Phase 1: Base Infrastructure (All Nodes)

```
01-prerequisites.sh  → 호스트명, /etc/hosts (VIP + 멀티마스터), 커널 모듈
02-containerd.sh     → containerd 런타임 설치
03-k8s-install.sh    → kubeadm, kubelet, kubectl 설치
```

### Phase 2: Master-1 Setup (Full Provisioning)

```
00-kube-vip.sh       → Control Plane VIP 정적 Pod (192.168.56.100, super-admin.conf)
01-nfs-server.sh     → NFS 서버 (XFS prjquota)
02-init-cluster.sh   → kubeadm init (--upload-certs, VIP endpoint)
                       → join-command.sh (worker용)
                       → join-control-plane.sh (master-2용, --ignore-preflight-errors)
03-cni-install.sh    → Cilium CNI + Hubble (VIP 기반 API 서버)
04-addons.sh         → metrics-server, csi-driver-nfs, StorageClass
05-nfs-quota-agent.sh→ NFS 프로젝트 쿼터 에이전트
```

### Phase 3: Master-2/3 Setup (Control Plane Join)

```
00-kube-vip.sh           → Control Plane VIP 정적 Pod (admin.conf)
02-join-control-plane.sh → master-1에서 SCP로 join 명령 가져와 실행
                           kubeadm join --control-plane (etcd 3-node 쿼럼, 1 fault tolerance)
```

### Phase 4: Platform Services (Master-1 only, auto-triggered after last worker join)

**Phase 2 Auto-Trigger:**
- Phase 1 (scripts 00-05) runs during master-1 provisioning
- Phase 2 (scripts 07-14) auto-triggers after last worker joins via Vagrant trigger
- Manual execution: `vagrant provision master-1 --provision-with phase2-platform`
- Executed by: `scripts/cluster/06-phase2-start.sh` wrapper script

**Execution Order:**

```
07-cnpg.sh                → CloudNative-PG Operator + narwhal-db (unified DB)
08-1-networking.sh        → MetalLB, APISIX, cert-manager
08-2-monitoring.sh        → Prometheus, Loki, Alloy, Tempo
08-3-security.sh          → Kyverno, Headlamp, OAuth2-Proxy
08-4-storage.sh           → SeaweedFS, OpenBao, Velero
08-5-registry.sh          → Harbor
08-6-tls-routes.sh        → CA cert 배포, APISIX routes
09-istio-ambient.sh       → Istio ambient mesh (mTLS, zero sidecars)
10-dnsmasq.sh             → 로컬 DNS (*.local.narwhal.internal) + CoreDNS forward
11-1-keycloak-operator.sh → Keycloak Operator + CR + HTTPRoute
11-2-keycloak-realm.sh   → Realm + Roles + Groups + Users
11-3-keycloak-clients.sh → OIDC 클라이언트 7개 + Audience mappers
11-4-keycloak-apiserver.sh → K8s API Server OIDC 설정 + RBAC
12-gitea.sh          → Gitea Git 서버 (shared narwhal-db)
13-argocd.sh         → ArgoCD 설치 + Keycloak OIDC 연동
14-gitops-bootstrap.sh → narwhal-gitops 레포 생성 + App-of-Apps 배포
```

**설치 순서의 중요성:**
- cert-manager와 APISIX TLS는 Keycloak OIDC보다 먼저 설치되어야 함 (K8s 1.35+ HTTPS 필수)
- dnsmasq는 Keycloak 전에 설정되어 DNS 해석 가능해야 함
- CNPG는 모든 DB 의존 앱보다 먼저 실행되어야 함

### Worker Nodes

```
02-join-worker.sh    → kubeadm join (master-1에서 토큰 가져오기)
```

---

## Namespace Topology

```
kube-system          Cilium, Hubble, CoreDNS, CSI-NFS, metrics-server, nfs-quota-agent
platform-system      CloudNative-PG Operator, MetalLB, APISIX, cert-manager, Kyverno
istio-system         Istio control plane (istiod, istio-cni, ztunnel)
iam                  Keycloak, OAuth2-Proxy
devtools             ArgoCD, Gitea + Valkey, Harbor, Headlamp
monitoring           Prometheus, Grafana, Alertmanager, Loki, Alloy, Tempo
storage              SeaweedFS (S3), OpenBao, Velero
database             narwhal-db (CNPG PostgreSQL HA)
dev                  Developer workloads (user namespace)
```

---

## Access Guide

### DNS 설정 (Client)

```bash
# macOS
sudo mkdir -p /etc/resolver
echo 'nameserver 192.168.56.10' | sudo tee /etc/resolver/local.narwhal.internal

# Linux (systemd-resolved)
sudo resolvectl dns eth0 192.168.56.10
sudo resolvectl domain eth0 ~local.narwhal.internal
```

### Web UI URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | https://grafana.local.narwhal.internal | admin / admin |
| Harbor | https://harbor.local.narwhal.internal | admin / Harbor12345 |
| Keycloak | https://keycloak.local.narwhal.internal | (auto-generated) |
| OpenBao | https://openbao.local.narwhal.internal | (unseal required) |
| Hubble | https://hubble.local.narwhal.internal | - |
| ArgoCD | https://argocd.local.narwhal.internal | admin / (auto) |
| Gitea | https://gitea.local.narwhal.internal | gitea-admin / gitea-admin |
| Headlamp | https://headlamp.local.narwhal.internal | Keycloak OIDC |

### OIDC Login

```bash
# 토큰 발급 (HTTPS, self-signed cert)
TOKEN=$(curl -k -s -X POST \
  'https://keycloak.local.narwhal.internal/realms/kubernetes/protocol/openid-connect/token' \
  -d 'grant_type=password&client_id=kubernetes&username=admin&password=admin' \
  | jq -r '.access_token')

# kubectl 사용
kubectl --token=$TOKEN get nodes
```

---

## Backup & Recovery

```
Velero
├── Backup Storage: SeaweedFS S3 (seaweedfs-s3:8333)
├── Bucket: velero
├── Uploader: Kopia
├── Node Agent: DaemonSet (filesystem backup)
└── Volume Snapshots: disabled (NFS)

CNPG PostgreSQL
├── WAL Archiving: SeaweedFS S3 (향후 활성화)
├── Retention: 14 days
└── Point-in-Time Recovery: 지원
```

### 백업 전략

| 컴포넌트 | 방법 | 주기 | 보존 |
|----------|------|------|------|
| 전체 클러스터 | Velero | 매일 | 7일 |
| PostgreSQL | CNPG barman + WAL | 매일 02:00 UTC | 7일 |
| Git 레포 | Velero (PVC) | 매일 | 7일 |

복구 절차: `docs/vagrant/disaster-recovery.md` 참조
검증 스크립트: `scripts/test/verify-backup.sh`

---

## 시크릿 관리

모든 비밀번호와 시크릿은 Kubernetes Secret으로 관리되며, 스크립트에서 동적으로 생성됩니다.

| Secret | 네임스페이스 | 생성 스크립트 | 용도 |
|--------|-------------|-------------|------|
| narwhal-db-credentials | database | 07-cnpg.sh | PostgreSQL DB 비밀번호 |
| oidc-client-secrets | iam | 11-3-keycloak-clients.sh | OIDC 클라이언트 시크릿 6개 |
| oauth2-proxy-secrets | iam | 11-3-keycloak-clients.sh | 쿠키/클라이언트 시크릿 |
| grafana-secrets | monitoring | 08-2-monitoring.sh | Grafana 관리자 비밀번호 |
| harbor-secrets | devtools | 08-5-registry.sh | Harbor 관리자 비밀번호 |
| gitea-admin | devtools | 12-gitea.sh | Gitea 관리자 비밀번호 |
| velero-s3-credentials | storage | 08-4-storage.sh | S3 백업 자격증명 |

비밀번호 확인:
```bash
kubectl get secret <name> -n <namespace> -o jsonpath='{.data.<key>}' | base64 -d
```

---

## Security Boundaries

```
┌─ External ───────────────────────────────────────────┐
│                                                      │
│  cert-manager   → Self-signed ClusterIssuer          │
│  Kyverno        → Policy enforcement                 │
│  OAuth2 Proxy   → Gateway-level OIDC authentication  │
│  OpenBao        → Secret management (KV, PKI)        │
│                                                      │
├─ Identity ───────────────────────────────────────────┤
│                                                      │
│  Keycloak       → Central IdP (OIDC/SAML)            │
│  K8s OIDC       → API Server authentication          │
│  RBAC           → Group-based authorization          │
│    cluster-admin → cluster-admin                     │
│    developer     → edit (dev NS)                     │
│    viewer        → view                              │
│                                                      │
├─ Service Mesh ───────────────────────────────────────┤
│                                                      │
│  Istio ambient   → mTLS PERMISSIVE (East-West)        │
│  ztunnel         → Node proxy (HBONE, zero sidecar)  │
│  PeerAuth        → Mesh-wide mutual TLS              │
│                                                      │
├─ Network ────────────────────────────────────────────┤
│                                                      │
│  Cilium          → Network policies, encryption      │
│  ArgoCD          → NetworkPolicy per component       │
│  MetalLB         → L2 only (no BGP)                  │
│                                                      │
└──────────────────────────────────────────────────────┘
```
