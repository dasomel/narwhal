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
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐         │
│  │ narwhal-worker-1  │  │ narwhal-worker-2  │  │ narwhal-worker-3  │         │
│  │ 192.168.56.21     │  │ 192.168.56.22     │  │ 192.168.56.23     │         │
│  │ worker            │  │ worker            │  │ worker            │         │
│  │ 2 CPU / 6GB RAM   │  │ 2 CPU / 6GB RAM   │  │ 2 CPU / 6GB RAM   │         │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘         │
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

| Hostname | Backend Service |
|----------|----------------|
| grafana.local.narwhal.io | prometheus-stack-grafana (monitoring) |
| harbor.local.narwhal.io | harbor (harbor) |
| keycloak.local.narwhal.io | keycloak-service (keycloak) |
| openbao.local.narwhal.io | openbao-ui (openbao) |
| hubble.local.narwhal.io | hubble-ui (kube-system) |
| oauth2-proxy.local.narwhal.io | oauth2-proxy (oauth2-proxy) |

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
│  Istio v1.29 ambient mode — mTLS (STRICT), zero sidecars    │
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
│  ├── k8s-admin  → Group: cluster-admins            │
│  └── developer  → Group: developers                │
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
│  ├── oidc:cluster-admins → ClusterRole: cluster-admin │
│  ├── oidc:developers     → ClusterRole: edit          │
│  └── oidc:viewers        → ClusterRole: view          │
└───────────────────────────────────────────────────────┘
```

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
- Phase 2 (scripts 06-13) auto-triggers after last worker joins via Vagrant trigger
- Manual execution: `vagrant provision master-1 --provision-with phase2-platform`
- Executed by: `scripts/cluster/phase2-platform.sh` wrapper script

**Execution Order:**

```
06-cnpg.sh           → CloudNative-PG Operator + narwhal-db (unified DB)
07-platform-apps.sh  → MetalLB, Traefik, cert-manager, Prometheus,
                       Loki, Promtail, Tempo, Kyverno, Headlamp,
                       OAuth2 Proxy, SeaweedFS, Harbor, OpenBao, Velero
08-istio-ambient.sh  → Istio ambient mesh (mTLS, zero sidecars)
09-dnsmasq.sh        → 로컬 DNS (*.local.narwhal.io) + CoreDNS forward
10-keycloak.sh       → Keycloak Operator + OIDC 설정 + API Server 연동 (HTTPS)
11-gitea.sh          → Gitea Git 서버 (shared narwhal-db)
12-argocd.sh         → ArgoCD 설치 + Keycloak OIDC 연동
13-gitops-bootstrap.sh → narwhal-gitops 레포 생성 + App-of-Apps 배포
```

**설치 순서의 중요성:**
- cert-manager와 Traefik TLS는 Keycloak OIDC보다 먼저 설치되어야 함 (K8s 1.35+ HTTPS 필수)
- dnsmasq는 Keycloak 전에 설정되어 DNS 해석 가능해야 함
- CNPG는 모든 DB 의존 앱보다 먼저 실행되어야 함

### Worker Nodes

```
01-join-cluster.sh   → kubeadm join (master-1에서 토큰 가져오기)
```

---

## Namespace Topology

```
kube-system          Cilium, Hubble, CoreDNS, CSI-NFS, metrics-server
cnpg-system          CloudNative-PG Operator
keycloak             Keycloak (uses shared narwhal-db)
monitoring           Prometheus, Grafana, Alertmanager, Loki, Promtail, Tempo
cert-manager         cert-manager + ClusterIssuer
metallb-system       MetalLB Controller + Speakers
traefik              Traefik Gateway API Controller
kyverno              Kyverno Policy Engine
headlamp             Headlamp K8s Dashboard
oauth2-proxy         OAuth2 Proxy (Gateway Auth)
seaweedfs            SeaweedFS (Master + Volume + Filer + S3)
harbor               Harbor Registry (uses shared narwhal-db)
openbao              OpenBao Secret Management
velero               Velero Backup + Node Agents
gitea                Gitea + Valkey (uses shared narwhal-db)
argocd               ArgoCD (Server, Repo, Controller, Dex, Redis)
nfs-quota-agent      NFS Quota Agent
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
  -d 'grant_type=password&client_id=kubernetes&username=k8s-admin&password=k8s-admin' \
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
│    cluster-admins → cluster-admin                    │
│    developers     → edit                             │
│    viewers        → view                             │
│                                                      │
├─ Service Mesh ───────────────────────────────────────┤
│                                                      │
│  Istio ambient   → mTLS STRICT (East-West)           │
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
