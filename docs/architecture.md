# Narwhal - Platform Architecture

Narwhal은 Vagrant VM 기반의 Kubernetes Internal Developer Platform (IDP) 클러스터 자동 구축 프로젝트입니다.
로컬 환경에서 프로덕션 수준의 IDP 스택을 원클릭으로 프로비저닝합니다.

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
│  LB:  192.168.56.200 (MetalLB → Traefik)                                    │
│  DNS: *.local.narwhal.io → 192.168.56.200                                   │
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
│   Browser ──→ *.local.narwhal.io ──→ dnsmasq (192.168.56.10:53)      │
│                        │                                             │
│                        ▼                                             │
│              MetalLB (192.168.56.200)                                │
│                        │                                             │
│                        ▼                                             │
│              ┌─────────────────┐                                     │
│              │     Traefik     │  Gateway API Controller             │
│              │  (LoadBalancer) │  HTTPRoute-based routing            │
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

1. **DNS Resolution**: Client → dnsmasq (master node) → `*.local.narwhal.io` → `192.168.56.200`
2. **Load Balancing**: MetalLB L2 Advertisement → Traefik LoadBalancer Service
3. **Routing**: Traefik HTTPRoute → Backend Service (by hostname)
4. **Authentication**: OAuth2 Proxy → Keycloak OIDC (선택적)

### HTTPRoute Mappings

| Hostname | Backend Service | Namespace |
|----------|----------------|-----------|
| argocd.local.narwhal.io | argocd-server | devtools |
| grafana.local.narwhal.io | prometheus-stack-grafana | monitoring |
| gitea.local.narwhal.io | gitea-http | devtools |
| harbor.local.narwhal.io | harbor | devtools |
| keycloak.local.narwhal.io | keycloak-service | iam |
| headlamp.local.narwhal.io | headlamp | devtools |
| openbao.local.narwhal.io | openbao-ui | storage |
| hubble.local.narwhal.io | hubble-ui | kube-system |
| oauth2-proxy.local.narwhal.io | oauth2-proxy | iam |

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
│  │ Prometheus │  │  Loki  │  │  Tempo   │  │  Promtail  │    │
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
│  │ Cilium   │  │ Traefik  │  │ MetalLB  │  │  kube-vip  │    │
│  │ CNI +    │  │ Gateway  │  │ L2 LB    │  │  CP VIP    │    │
│  │ Hubble   │  │ API      │  │          │  │            │    │
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
│  ├── headlamp     (secret)  → Headlamp             │
│  └── oauth2-proxy (secret)  → Gateway Auth         │
│                                                    │
│  Exposed via:                                      │
│  └── HTTPRoute (HTTPS) → keycloak.local.narwhal.io │
└────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────┐
│          Kubernetes API Server                        │
│                                                       │
│  --oidc-issuer-url=https://keycloak.local.narwhal.io/ │
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

### Gateway-Level SSO (Traefik ForwardAuth + OAuth2-Proxy)

모든 웹 앱은 Traefik ForwardAuth 미들웨어를 통해 Gateway 레벨에서 인증을 강제한다.
미인증 사용자는 URL 자체에 접근할 수 없으며, OAuth2-Proxy의 `allowed_groups`로 그룹 기반 접근 제어를 수행한다.

```
Browser → argocd.local.narwhal.io
    │
    ▼
┌─────────────────────────────────────────────────┐
│  Traefik Gateway                                │
│                                                 │
│  1. ForwardAuth → OAuth2-Proxy /oauth2/auth     │
│     ├── 쿠키 있음 → 200 OK → 앱으로 전달       │
│     └── 쿠키 없음 → 401                        │
│                                                 │
│  2. Errors 미들웨어 (401 캐치)                  │
│     └── auth-redirect (nginx) → JS 리다이렉트   │
│         └── oauth2-proxy/oauth2/start?rd=<URL>  │
│                                                 │
│  3. OAuth2-Proxy → Keycloak 로그인              │
│     ├── allowed_groups 검사                     │
│     │   ├── 통과 → 쿠키 설정 → rd URL 리다이렉트│
│     │   └── 실패 (guest) → 403 Access Denied    │
│     └── 쿠키 도메인: .local.narwhal.io (전체)   │
│                                                 │
│  4. 앱별 SSO 자동 로그인 (Keycloak 세션 공유)   │
│     └── appRedirects 맵으로 앱 SSO URL 직행     │
└─────────────────────────────────────────────────┘
```

**ForwardAuth 적용 현황:**

| 앱 | ForwardAuth | SSO 리다이렉트 경로 | 비고 |
|---|---|---|---|
| ArgoCD | ✅ | `/auth/login?return_url=...%2Fapplications` | OIDC → 자동 로그인 |
| Grafana | ✅ | `/login/generic_oauth` | OAuth → 자동 로그인 |
| Gitea | ✅ | `/user/oauth2/keycloak` | OAuth → 자동 로그인 |
| Harbor | ✅ | `/c/oidc/login` | OIDC → 자동 로그인 |
| Headlamp | ✅ | (SPA, 기본 경로) | 클라이언트사이드 OIDC |
| OpenBao | ✅ | (SPA, 기본 경로) | 토큰 인증 |
| Hubble | ✅ | (자체 SSO 없음) | ForwardAuth만 |
| Keycloak | ❌ | — | IAM 자체 |
| OAuth2-Proxy | ❌ | — | 인증 서비스 자체 |

**그룹 기반 접근 제어 (OAuth2-Proxy `allowed_groups`):**

| 그룹 | 접근 가능 | 차단 |
|---|---|---|
| cluster-admin | 모든 앱 | — |
| developer | 모든 앱 | — |
| viewer | 모든 앱 | — |
| guest | ❌ 403 Access Denied | 모든 앱 |

**핵심 리소스:**

| 리소스 | 네임스페이스 | 설명 |
|---|---|---|
| `ConfigMap/auth-redirect-page` | devtools | JS 리다이렉트 페이지 (PKCE 충돌 방지 포함) |
| `Deployment/auth-redirect` | devtools | nginx (redirect 페이지 서빙) |
| `Middleware/forwardauth-oauth2` | devtools, monitoring, storage, kube-system | ForwardAuth → OAuth2-Proxy |
| `Middleware/auth-signin` | devtools, monitoring, storage, kube-system | Errors (401 → 리다이렉트) |
| `Service/auth-redirect-ext` | monitoring, storage, kube-system | ExternalName → devtools/auth-redirect |

**쿠키 & 세션 관리:**

| 쿠키 | 도메인 | 만료 | 용도 |
|---|---|---|---|
| `_oauth2_proxy` | `.local.narwhal.io` | 7일 (기본) | Gateway 인증 (전체 앱 공유) |
| `argocd.token` | `argocd.local.narwhal.io` | 앱별 | ArgoCD 세션 |
| `grafana_session` | `grafana.local.narwhal.io` | 앱별 | Grafana 세션 |
| `i_like_gitea` | `gitea.local.narwhal.io` | 앱별 | Gitea 세션 |
| `sid` | `harbor.local.narwhal.io` | 앱별 | Harbor 세션 |

**테스트 & 트러블슈팅 가이드:**

```bash
# 1. 다른 사용자로 테스트 — 프라이빗/시크릿 창 사용
#    Chrome: Ctrl+Shift+N / macOS: Cmd+Shift+N
#    Firefox: Ctrl+Shift+P / macOS: Cmd+Shift+P
#    → 창 닫으면 모든 쿠키 자동 삭제

# 2. 같은 브라우저에서 재로그인 — 쿠키 삭제
#    Chrome:  개발자도구(F12) → Application → Cookies
#             → .local.narwhal.io 도메인 우클릭 → Clear
#    Firefox: 개발자도구(F12) → Storage → Cookies
#             → .local.narwhal.io 선택 → Delete All
#    Safari:  환경설정 → 개인정보보호 → 웹사이트 데이터 관리
#             → local.narwhal.io 검색 → 제거

# 3. CLI로 인증 상태 확인
#    쿠키 없이 접근 (401/302 예상):
curl -sk -o /dev/null -w '%{http_code}' https://argocd.local.narwhal.io
#    OAuth2-Proxy 쿠키로 접근 (200 예상):
curl -sk -o /dev/null -w '%{http_code}' \
  -H "Cookie: _oauth2_proxy=<쿠키값>" https://argocd.local.narwhal.io

# 4. OAuth2-Proxy 로그 확인 (인증 실패 디버깅)
kubectl logs -n iam -l app.kubernetes.io/name=oauth2-proxy --tail=20

# 5. Keycloak 세션 강제 만료 (모든 사용자 로그아웃)
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
│        https://grafana.local.narwhal.io (admin/admin)  │
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
│    │node-export│  │Promtail │  (DaemonSet)             │
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
│  cert-mgr  prometheus loki   traefik   harbor  velero    │
│  kyverno   promtail   tempo  metallb   openbao headlamp  │
│  seaweedfs oauth2-proxy  istio-base  istiod              │
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
│      ├── traefik-routes.yaml                             │
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
08-1-networking.sh        → MetalLB, Traefik, cert-manager
08-2-monitoring.sh        → Prometheus, Loki, Promtail, Tempo
08-3-security.sh          → Kyverno, Headlamp, OAuth2-Proxy
08-4-storage.sh           → SeaweedFS, OpenBao, Velero
08-5-registry.sh          → Harbor
08-6-tls-routes.sh        → CA cert 배포, Traefik routes
09-istio-ambient.sh       → Istio ambient mesh (mTLS, zero sidecars)
10-dnsmasq.sh             → 로컬 DNS (*.local.narwhal.io) + CoreDNS forward
11-1-keycloak-operator.sh → Keycloak Operator + CR + HTTPRoute
11-2-keycloak-realm.sh   → Realm + Roles + Groups + Users
11-3-keycloak-clients.sh → OIDC 클라이언트 7개 + Audience mappers
11-4-keycloak-apiserver.sh → K8s API Server OIDC 설정 + RBAC
12-gitea.sh          → Gitea Git 서버 (shared narwhal-db)
13-argocd.sh         → ArgoCD 설치 + Keycloak OIDC 연동
14-gitops-bootstrap.sh → narwhal-gitops 레포 생성 + App-of-Apps 배포
```

**설치 순서의 중요성:**
- cert-manager와 Traefik TLS는 Keycloak OIDC보다 먼저 설치되어야 함 (K8s 1.35+ HTTPS 필수)
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
platform-system      CloudNative-PG Operator, MetalLB, Traefik, cert-manager, Kyverno
istio-system         Istio control plane (istiod, istio-cni, ztunnel)
iam                  Keycloak, OAuth2-Proxy
devtools             ArgoCD, Gitea + Valkey, Harbor, Headlamp
monitoring           Prometheus, Grafana, Alertmanager, Loki, Promtail, Tempo
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
echo 'nameserver 192.168.56.10' | sudo tee /etc/resolver/local.narwhal.io

# Linux (systemd-resolved)
sudo resolvectl dns eth0 192.168.56.10
sudo resolvectl domain eth0 ~local.narwhal.io
```

### Web UI URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | https://grafana.local.narwhal.io | admin / admin |
| Harbor | https://harbor.local.narwhal.io | admin / Harbor12345 |
| Keycloak | https://keycloak.local.narwhal.io | (auto-generated) |
| OpenBao | https://openbao.local.narwhal.io | (unseal required) |
| Hubble | https://hubble.local.narwhal.io | - |
| ArgoCD | https://argocd.local.narwhal.io | admin / (auto) |
| Gitea | https://gitea.local.narwhal.io | gitea-admin / gitea-admin |
| Headlamp | https://headlamp.local.narwhal.io | Keycloak OIDC |

### OIDC Login

```bash
# 토큰 발급 (HTTPS, self-signed cert)
TOKEN=$(curl -k -s -X POST \
  'https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token' \
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

복구 절차: `docs/disaster-recovery.md` 참조
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
