# Narwhal GitOps

This repository is the GitOps source of truth for the **Narwhal Kubernetes Internal Developer Platform (IDP)**. Every platform component running on the cluster — networking, service mesh, observability, storage, security, identity, and the developer portal itself — is declared here as Kubernetes/Helm manifests and reconciled continuously by ArgoCD using an app-of-apps pattern. Nothing is meant to be applied by hand: if it isn't in this repo, it doesn't run on the cluster (and if you `kubectl apply` a change directly, ArgoCD's `selfHeal` will revert it).

이 저장소는 **Narwhal Kubernetes 내부 개발자 플랫폼(IDP)**의 GitOps 단일 진실 공급원(source of truth)입니다. 네트워킹, 서비스 메시, 관측성(Observability), 스토리지, 보안, 아이덴티티, 그리고 개발자 포털까지 클러스터에서 동작하는 모든 플랫폼 컴포넌트가 이 저장소 안에 Kubernetes/Helm 매니페스트로 선언되어 있으며, ArgoCD가 app-of-apps 패턴으로 지속적으로 동기화(reconcile)합니다. 수동으로 적용하는 것은 전제하지 않습니다 — 이 저장소에 없다면 클러스터에서 동작하지 않으며, `kubectl apply`로 직접 바꿔도 ArgoCD의 `selfHeal`이 즉시 되돌립니다.

> This directory mirrors 1:1 to the in-cluster Gitea repository `narwhal-gitops` (`http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git`), which is the actual repo ArgoCD watches.
> 이 디렉토리는 클러스터 내부 Gitea 저장소 `narwhal-gitops`(ArgoCD가 실제로 감시하는 저장소)와 1:1로 미러링됩니다.

---

## Table of Contents / 목차

- [Repository Layout / 저장소 구조](#repository-layout--저장소-구조)
- [How It Deploys / 배포 방식](#how-it-deploys--배포-방식)
- [Components / 컴포넌트](#components--컴포넌트)
  - [Networking / 네트워킹](#networking--네트워킹)
  - [Service Mesh / 서비스 메시](#service-mesh--서비스-메시)
  - [Observability / 관측성](#observability--관측성)
  - [Storage & Backup / 스토리지 및 백업](#storage--backup--스토리지-및-백업)
  - [Security / 보안](#security--보안)
  - [IAM & SSO / 인증·인가](#iam--sso--인증인가)
  - [Registry & Developer UI / 레지스트리 및 개발자 UI](#registry--developer-ui--레지스트리-및-개발자-ui)
  - [Portal / 포털](#portal--포털)
- [Conventions / 규칙](#conventions--규칙)

---

## Repository Layout / 저장소 구조

```text
gitops/
├── apps/
│   └── app-of-apps.yaml           # Root ArgoCD Application (the single entry point)
├── charts/
│   ├── narwhal-apps/              # One ArgoCD Application per upstream Helm chart
│   │   └── templates/
│   │       ├── metallb.yaml, apisix.yaml, cert-manager.yaml        # networking
│   │       ├── istio-base.yaml, istiod.yaml, istio-cni.yaml, ztunnel.yaml  # service mesh
│   │       ├── prometheus-stack.yaml, loki.yaml, tempo.yaml, k8s-monitoring.yaml  # observability
│   │       ├── seaweedfs.yaml, openbao.yaml, velero.yaml, velero-ui.yaml  # storage/backup
│   │       ├── trivy-operator.yaml, kyverno.yaml                  # security
│   │       ├── harbor.yaml, headlamp.yaml                         # registry/UI
│   │       ├── falco.yaml                                         # DISABLED (see file header)
│   │       ├── narwhal-platform.yaml                               # meta-app -> charts/narwhal-platform
│   │       └── ghost-pod-reaper.yaml, openbao-unseal.yaml, istiod-pdb.yaml,
│   │           istio-telemetry-monitors.yaml, headlamp-policy.yaml # small support Applications
│   └── narwhal-platform/          # Platform-owned raw manifests (one chart, six templates)
│       └── templates/
│           ├── keycloak-cr.yaml           # Keycloak CR + login theme ConfigMap
│           ├── narwhal-portal-k8s.yaml    # Portal Deployment/Service/RBAC/Valkey
│           ├── apisix-routes.yaml         # ApisixRoute/ApisixUpstream definitions
│           ├── apisix-infra.yaml          # APISIX's own etcd + supporting infra
│           ├── istio-ambient-policies.yaml# PeerAuthentication (mTLS) policies
│           └── argocd-config.yaml         # ArgoCD OIDC/RBAC/diff-ignore configuration
└── resources/                     # Extra raw-YAML resources referenced by "small support" apps above
    ├── keycloak-theme/            # Custom Narwhal Keycloak login theme assets
    ├── network-policies.yaml, rbac-policies.yaml, kyverno-policies.yaml, ...
    └── ...
```

**EN:** `apps/` holds the single root Application. `charts/narwhal-apps` is a thin Helm chart whose only job is to template out one ArgoCD `Application` per platform component (mostly pointing at third-party Helm charts). `charts/narwhal-platform` is a second Helm chart that templates the manifests Narwhal itself owns and authors (Keycloak CR, the portal, APISIX routing, mesh policy, ArgoCD config) — these aren't upstream charts, they're first-party YAML. `resources/` holds extra standalone YAML (NetworkPolicies, RBAC, Kyverno policies, the Keycloak theme, etc.) that small "support" Applications in `narwhal-apps` point at via `directory.include`.

**KO:** `apps/`에는 루트 Application 하나만 있습니다. `charts/narwhal-apps`는 컴포넌트별 ArgoCD `Application`을 템플릿으로 찍어내는 얇은 Helm 차트로, 대부분 서드파티 Helm 차트를 가리킵니다. `charts/narwhal-platform`은 Narwhal이 직접 소유·작성하는 매니페스트(Keycloak CR, 포털, APISIX 라우팅, 메시 정책, ArgoCD 설정)를 템플릿하는 두 번째 Helm 차트이며, 업스트림 차트가 아닌 자체 작성 YAML입니다. `resources/`에는 `narwhal-apps` 안의 소규모 "지원" Application들이 `directory.include`로 가리키는 독립 YAML(NetworkPolicy, RBAC, Kyverno 정책, Keycloak 테마 등)이 들어 있습니다.

---

## How It Deploys / 배포 방식

**EN:** ArgoCD bootstraps from a single root Application, `idp-apps`, defined in `apps/app-of-apps.yaml`. That Application points at `charts/narwhal-apps`, which renders one child `Application` per platform component — including `narwhal-platform`, itself a meta-app that renders the six `charts/narwhal-platform` manifests as its own children. The result is a three-level app-of-apps tree:

```
idp-apps (root, apps/app-of-apps.yaml)
 └─ narwhal-apps (chart, one Application per platform component)
     ├─ metallb, apisix, cert-manager, istio-*, prometheus-stack, loki, tempo,
     │  k8s-monitoring, seaweedfs, openbao, velero, velero-ui, trivy-operator,
     │  kyverno, harbor, headlamp, (falco — disabled), + small support apps
     └─ narwhal-platform (chart, renders raw platform manifests)
         └─ keycloak-cr, narwhal-portal-k8s, apisix-routes, apisix-infra,
            istio-ambient-policies, argocd-config
```

All Applications use `syncPolicy.automated` with `prune: true` and `selfHeal: true`. **This means any change made directly with `kubectl` (or via the ArgoCD UI's "Edit") will be reverted automatically** the next time ArgoCD reconciles — the only durable way to change cluster state is to edit the YAML in this repo and push it to the Gitea remote. There is no local dev loop that bypasses Git.

**KO:** ArgoCD는 `apps/app-of-apps.yaml`에 정의된 루트 Application `idp-apps` 하나에서 부트스트랩됩니다. 이 Application은 `charts/narwhal-apps`를 가리키며, 컴포넌트별 자식 `Application`을 렌더링합니다 — 그중 `narwhal-platform`도 하나의 메타 앱으로서 `charts/narwhal-platform`의 6개 매니페스트를 자신의 자식으로 렌더링합니다. 결과적으로 3단계 app-of-apps 트리가 됩니다.

모든 Application은 `syncPolicy.automated`에 `prune: true`, `selfHeal: true`를 사용합니다. **즉 `kubectl`(또는 ArgoCD UI의 "Edit")로 직접 변경한 내용은 다음 재동기화(reconcile) 시 자동으로 되돌아갑니다.** 클러스터 상태를 지속적으로 바꾸는 유일한 방법은 이 저장소의 YAML을 수정한 뒤 Gitea 원격 저장소로 push하는 것뿐이며, Git을 우회하는 로컬 개발 루프는 존재하지 않습니다.

---

## Components / 컴포넌트

### Networking / 네트워킹

#### MetalLB
| Chart | Version | Namespace |
|---|---|---|
| `metallb` (metallb.github.io) | `0.16.1` | `platform-system` |

**EN:** MetalLB provides bare-metal LoadBalancer IP address allocation (LB IPAM) for a Vagrant/on-prem cluster that has no cloud provider to hand out external IPs. It assigns the platform's single external VIP (`192.168.56.200`) to the APISIX gateway Service, which is how every `*.local.narwhal.internal` hostname becomes reachable from outside the cluster. Without it, `type: LoadBalancer` Services would stay `<pending>` forever.

**KO:** MetalLB는 외부 IP를 자동 할당해줄 클라우드 프로바이더가 없는 베어메탈(Vagrant/온프레미스) 클러스터를 위한 LoadBalancer IP 할당(LB IPAM) 기능을 제공합니다. 플랫폼의 단일 외부 VIP(`192.168.56.200`)를 APISIX 게이트웨이 Service에 할당하며, 이를 통해 모든 `*.local.narwhal.internal` 호스트가 클러스터 외부에서 접근 가능해집니다. 없으면 `type: LoadBalancer` Service는 영원히 `<pending>` 상태로 남습니다.

#### APISIX
| Chart | Version | Namespace |
|---|---|---|
| `apisix` (charts.apiseven.com) | `2.13.0` | `platform-system` |

**EN:** Apache APISIX is the platform's single API gateway / ingress controller. Every external hostname (Keycloak, ArgoCD, Grafana, Harbor, the portal, OpenBao, velero-ui, Hubble, Gitea, and more) is routed through it via `ApisixRoute`/`ApisixUpstream` CRDs (defined in `apisix-routes.yaml`), which the bundled `apisix-ingress-controller` (v1.8.0) syncs to the APISIX admin API backed by its own etcd (`apisix-infra.yaml`). It terminates TLS, injects the `openid-connect` plugin for OIDC-protected apps, and trusts the internal Narwhal root CA for backend TLS verification.

**KO:** Apache APISIX는 플랫폼의 단일 API 게이트웨이/인그레스 컨트롤러입니다. Keycloak, ArgoCD, Grafana, Harbor, 포털, OpenBao, velero-ui, Hubble, Gitea 등 모든 외부 호스트명이 `ApisixRoute`/`ApisixUpstream` CRD(`apisix-routes.yaml`)를 통해 라우팅되며, 번들된 `apisix-ingress-controller`(v1.8.0)가 이를 자체 etcd 기반 APISIX admin API(`apisix-infra.yaml`)로 동기화합니다. TLS 종료, OIDC 보호 애플리케이션을 위한 `openid-connect` 플러그인 주입, 백엔드 TLS 검증을 위한 내부 Narwhal 루트 CA 신뢰를 담당합니다.

#### cert-manager
| Chart | Version | Namespace |
|---|---|---|
| `cert-manager` (charts.jetstack.io) | `v1.20.2` | `platform-system` |

**EN:** cert-manager automates issuance and renewal of TLS certificates cluster-wide via `ClusterIssuer`/`Certificate` CRDs, backing the internal Narwhal root CA and the wildcard certificate used for `*.local.narwhal.internal`. It's deployed with CRDs enabled and Prometheus metrics on; ArgoCD is configured to ignore drift on its webhook `caBundle` fields since `cainjector` mutates those at runtime.

**KO:** cert-manager는 `ClusterIssuer`/`Certificate` CRD를 통해 클러스터 전역의 TLS 인증서 발급과 갱신을 자동화하며, 내부 Narwhal 루트 CA와 `*.local.narwhal.internal` 와일드카드 인증서를 뒷받침합니다. CRD와 Prometheus 메트릭이 활성화된 채로 배포되며, `cainjector`가 런타임에 webhook의 `caBundle` 필드를 변경하기 때문에 ArgoCD는 해당 필드의 drift를 무시하도록 구성되어 있습니다.

### Service Mesh / 서비스 메시

All four components below are pinned to Istio **1.30.1** and share the `istio-system` namespace, running in **ambient mode** (no sidecars).
아래 네 컴포넌트 모두 Istio **1.30.1**로 고정되어 있으며, `istio-system` 네임스페이스를 공유하고 **ambient 모드**(사이드카 없음)로 동작합니다.

#### Istio Base
| Chart | Version | Namespace |
|---|---|---|
| `base` (istio-release GCS charts) | `1.30.1` | `istio-system` |

**EN:** Installs Istio's CRDs (VirtualService, PeerAuthentication, etc.) and cluster-wide RBAC — a prerequisite for every other Istio component. Synced first (`sync-wave: -10`).

**KO:** Istio의 CRD(VirtualService, PeerAuthentication 등)와 클러스터 전역 RBAC를 설치하며, 다른 모든 Istio 컴포넌트의 전제 조건입니다. 가장 먼저 동기화됩니다(`sync-wave: -10`).

#### Istiod
| Chart | Version | Namespace |
|---|---|---|
| `istiod` (istio-release GCS charts) | `1.30.1` | `istio-system` |

**EN:** The Istio control plane — configuration distribution, certificate issuance for mTLS, and service discovery for the mesh. Runs the `ambient` profile with 2 replicas and pod anti-affinity for HA, tolerating control-plane taints so it can run on the (tainted) masters.

**KO:** Istio 컨트롤 플레인으로, 설정 배포, mTLS용 인증서 발급, 메시 서비스 디스커버리를 담당합니다. `ambient` 프로필로 2개 레플리카를 파드 안티어피니티와 함께 실행하며, control-plane taint를 tolerate하여 마스터 노드에서도 실행될 수 있습니다.

#### Istio CNI
| Chart | Version | Namespace |
|---|---|---|
| `cni` (istio-release GCS charts) | `1.30.1` | `istio-system` |

**EN:** A CNI plugin (chained after Cilium) that redirects pod traffic into the ambient mesh data plane without sidecar injection. Configured with `cni.exclusive=false` so it coexists with Cilium instead of overwriting its CNI config.

**KO:** ambient 메시 데이터 플레인으로 파드 트래픽을 사이드카 주입 없이 리다이렉트하는 CNI 플러그인(Cilium 뒤에 체이닝됨)입니다. `cni.exclusive=false`로 설정되어 Cilium의 CNI 설정을 덮어쓰지 않고 공존합니다.

#### ztunnel
| Chart | Version | Namespace |
|---|---|---|
| `ztunnel` (istio-release GCS charts) | `1.30.1` | `istio-system` |

**EN:** The per-node ambient dataplane proxy (DaemonSet) that transparently secures pod-to-pod traffic over mTLS/HBONE (port 15008) for every mesh-enrolled namespace — the actual traffic-handling component of Istio ambient mode, as opposed to sidecar Envoy.

**KO:** ambient 데이터 플레인의 노드별 프록시(DaemonSet)로, 메시에 등록된 모든 네임스페이스 간 파드-투-파드 트래픽을 mTLS/HBONE(15008 포트)으로 투명하게 보호합니다. 사이드카 Envoy 대신 Istio ambient 모드에서 실제 트래픽을 처리하는 컴포넌트입니다.

### Observability / 관측성

#### Prometheus Stack
| Chart | Version | Namespace |
|---|---|---|
| `kube-prometheus-stack` (prometheus-community) | `86.2.3` | `monitoring` |

**EN:** The metrics backbone — Prometheus (7-day retention, `nfs-csi`-backed PVC), Alertmanager, Grafana, and the full suite of ServiceMonitor/PrometheusRule CRDs. `kubeProxy`/`kubeEtcd` component monitors are disabled (Cilium replaces kube-proxy; etcd metrics aren't exposed on this cluster), and `ruleSelector`/`ruleNamespaceSelector` are wide open so any namespace can ship its own `PrometheusRule`.

**KO:** 메트릭의 근간이 되는 스택으로 Prometheus(7일 보존, `nfs-csi` 기반 PVC), Alertmanager, Grafana, 그리고 ServiceMonitor/PrometheusRule CRD 전체를 포함합니다. `kubeProxy`/`kubeEtcd` 컴포넌트 모니터는 비활성화되어 있으며(Cilium이 kube-proxy를 대체, 이 클러스터에서는 etcd 메트릭 미노출), `ruleSelector`/`ruleNamespaceSelector`가 전체 개방되어 있어 어떤 네임스페이스든 자체 `PrometheusRule`을 배포할 수 있습니다.

#### Loki
| Chart | Version | Namespace |
|---|---|---|
| `loki` (grafana-community) | `18.4.0` | `monitoring` |

**EN:** Log aggregation backend, running in `Monolithic` deployment mode with S3-compatible object storage (SeaweedFS, bucket `loki`) as its chunk/ruler/admin store. Ingests logs from Grafana Alloy and, when enabled, Falcosidekick events.

**KO:** 로그 집계 백엔드로 `Monolithic` 배포 모드로 동작하며, S3 호환 오브젝트 스토리지(SeaweedFS, `loki` 버킷)를 청크/룰러/어드민 저장소로 사용합니다. Grafana Alloy와 (활성화 시) Falcosidekick 이벤트로부터 로그를 수집합니다.

#### Tempo
| Chart | Version | Namespace |
|---|---|---|
| `tempo` (grafana-community) | `2.2.3` (app pinned to `2.9.0`) | `monitoring` |

**EN:** Distributed tracing backend, storing traces in the same SeaweedFS S3 bucket family. The container image tag is explicitly pinned to `2.9.0` (overriding the chart's default `2.10.7`) because newer Tempo builds dropped vParquet2 block-encoding support that the SeaweedFS `tempo` bucket may still contain.

**KO:** 분산 추적(tracing) 백엔드로, 동일한 SeaweedFS S3 버킷 계열에 트레이스를 저장합니다. 컨테이너 이미지 태그는 명시적으로 `2.9.0`으로 고정되어 있는데(차트 기본값 `2.10.7` 대신), 최신 Tempo 빌드가 vParquet2 블록 인코딩 지원을 제거했고 SeaweedFS `tempo` 버킷에 해당 형식의 데이터가 남아있을 수 있기 때문입니다.

#### k8s-monitoring (Grafana Alloy)
| Chart | Version | Namespace |
|---|---|---|
| `k8s-monitoring` (grafana.github.io) | `4.2.0` | (chart default) |

**EN:** Deploys Grafana Alloy as a log-shipping DaemonSet, replacing the deprecated Promtail (EOL 2026-03-02). Collects container logs cluster-wide and pushes them to Loki via `podLogsViaLoki`.

**KO:** Grafana Alloy를 로그 수집용 DaemonSet으로 배포하며, 지원 종료된(EOL 2026-03-02) Promtail을 대체합니다. 클러스터 전역의 컨테이너 로그를 수집하여 `podLogsViaLoki`를 통해 Loki로 전송합니다.

### Storage & Backup / 스토리지 및 백업

#### SeaweedFS
| Chart | Version | Namespace |
|---|---|---|
| `seaweedfs` (seaweedfs.github.io) | `4.34.0` | `storage` |

**EN:** An S3-compatible distributed object store, self-hosted as the platform's storage backend for everything that needs a bucket — Loki chunks, Tempo traces, Velero backups, and CNPG WAL archives. All persistent components use `nfs-csi`-backed PVCs.

**KO:** S3 호환 분산 오브젝트 스토어로, 버킷이 필요한 모든 컴포넌트(Loki 청크, Tempo 트레이스, Velero 백업, CNPG WAL 아카이브)를 위한 플랫폼의 자체 호스팅 스토리지 백엔드입니다. 영속 컴포넌트는 모두 `nfs-csi` 기반 PVC를 사용합니다.

#### OpenBao
| Chart | Version | Namespace |
|---|---|---|
| `openbao` (openbao.github.io) | `0.28.3` | `storage` |

**EN:** OpenBao (the open-source HashiCorp Vault fork) is the platform's central secrets manager — dynamic secrets, Kubernetes-auth-based injection, and a UI for platform operators. Runs single-instance (`ha.enabled: false`) with TLS enabled on the HTTPS listener; a companion `openbao-unseal` support app handles automatic unsealing after restarts.

**KO:** OpenBao(오픈소스 HashiCorp Vault 포크)는 플랫폼의 중앙 시크릿 관리자로, 동적 시크릿 발급, Kubernetes 인증 기반 주입, 플랫폼 운영자용 UI를 제공합니다. 단일 인스턴스(`ha.enabled: false`)로 동작하며 HTTPS 리스너에 TLS가 활성화되어 있고, 재시작 후 자동 언씰(unseal)을 처리하는 별도의 `openbao-unseal` 지원 앱이 함께 있습니다.

#### Velero
| Chart | Version | Namespace |
|---|---|---|
| `velero` (vmware-tanzu) | `12.0.3` | `storage` |

**EN:** Cluster backup/restore tool. Uses the AWS S3 plugin pointed at the SeaweedFS S3 endpoint (`s3ForcePathStyle`) as its backup storage location, so it needs no real cloud account — SeaweedFS masquerades as S3.

**KO:** 클러스터 백업/복원 도구입니다. AWS S3 플러그인을 SeaweedFS S3 엔드포인트로 향하게 하여(`s3ForcePathStyle`) 백업 저장 위치로 사용하므로 실제 클라우드 계정이 필요 없습니다 — SeaweedFS가 S3 역할을 대신합니다.

#### Velero UI
| Chart | Version | Namespace |
|---|---|---|
| `velero-ui` (github.com/otwld/velero-ui) | `v0.10.1` | `storage` |

**EN:** A web UI/API for triggering and inspecting Velero backups/restores without kubectl, protected by Keycloak OIDC (server-side token exchange trusts the internal CA via `NODE_EXTRA_CA_CERTS`).

**KO:** kubectl 없이 Velero 백업/복원을 트리거하고 조회할 수 있는 웹 UI/API로, Keycloak OIDC로 보호됩니다(서버 측 토큰 교환은 `NODE_EXTRA_CA_CERTS`로 내부 CA를 신뢰).

### Security / 보안

#### Trivy Operator
| Chart | Version | Namespace |
|---|---|---|
| `trivy-operator` (aquasecurity) | `0.27.0` | `security-system` |

**EN:** Continuous vulnerability, misconfiguration, exposed-secret, and compliance scanning for every workload and image on the cluster, running in lightweight `Standalone` mode (Trivy CLI runs per-scan-Job, no shared server) to fit the resource-constrained worker nodes. Feeds the portal's Cluster Security page via `VulnerabilityReport`/`ConfigAuditReport`/etc. CRDs.

**KO:** 클러스터의 모든 워크로드와 이미지에 대해 취약점, 설정 오류, 노출된 시크릿, 컴플라이언스를 지속적으로 스캔합니다. 자원이 제한된 워커 노드에 맞게 경량 `Standalone` 모드(스캔 Job마다 Trivy CLI 실행, 공유 서버 없음)로 동작하며, `VulnerabilityReport`/`ConfigAuditReport` 등의 CRD를 통해 포털의 Cluster Security 페이지에 데이터를 제공합니다.

#### Kyverno
| Chart | Version | Namespace |
|---|---|---|
| `kyverno` (kyverno.github.io) | `3.8.1` | `platform-system` |

**EN:** A Kubernetes-native policy engine enforcing admission-time and background policies (e.g. restricting portal-created Jobs, generating NetworkPolicies) across the cluster. Runs with multiple replicas per controller (admission/background/cleanup/reports) for resilience; it is fail-closed, so its own admission webhook availability is on the critical path for all pod scheduling.

**KO:** Kubernetes 네이티브 정책 엔진으로, 어드미션 시점 및 백그라운드 정책(예: 포털이 생성하는 Job 제한, NetworkPolicy 자동 생성)을 클러스터 전역에 강제합니다. 컨트롤러별(admission/background/cleanup/reports)로 다중 레플리카로 동작해 복원력을 확보하며, fail-closed 방식이므로 자체 어드미션 웹훅의 가용성이 모든 파드 스케줄링의 critical path에 있습니다.

> **Falco** (runtime security / syscall-level threat detection) is defined in `templates/falco.yaml` but currently **disabled** (`{{- if false }}`) — Falco 0.39.2's modern_eBPF driver fails `scap_init` on the Ubuntu 26.04 / kernel 7.0 nodes used by this cluster, and the 0.43+ upgrade needed to fix it is a breaking migration. Runtime coverage in the meantime comes from Trivy Operator + Kyverno + NetworkPolicy + STRICT mTLS. See the file header and `CLAUDE.md` mistakes log (2026-07-08) for details.
> **Falco**(런타임 보안/시스템콜 수준 위협 탐지)는 `templates/falco.yaml`에 정의되어 있으나 현재 **비활성화**(`{{- if false }}`)되어 있습니다 — Falco 0.39.2의 modern_eBPF 드라이버가 이 클러스터의 Ubuntu 26.04/커널 7.0 노드에서 `scap_init`에 실패하며, 이를 해결할 0.43+ 업그레이드는 breaking 마이그레이션이기 때문입니다. 그동안의 런타임 보호는 Trivy Operator + Kyverno + NetworkPolicy + STRICT mTLS로 대체됩니다. 자세한 내용은 파일 헤더와 `CLAUDE.md` 미스테이크 로그(2026-07-08)를 참고하세요.

### IAM & SSO / 인증·인가

#### Keycloak
| Manifest | Managed by | Namespace |
|---|---|---|
| `keycloak-cr.yaml` (Keycloak CR + login theme) | Keycloak Operator (installed by `scripts/cluster/11-keycloak.sh`) | `iam` |

**EN:** Keycloak is the platform's Single Sign-On / OIDC Identity Provider — every SSO-integrated app (ArgoCD, Grafana, Harbor, Headlamp, OpenBao, velero-ui, Gitea, the K8s API server itself, and the portal) authenticates against it. It's not deployed via a generic Helm chart but as a `Keycloak` CR reconciled by the upstream Keycloak Operator, with a custom Narwhal-branded login theme (`keycloak-theme-narwhal` ConfigMap, bilingual EN/KO messages) mounted in. CPU/memory requests must live under `spec.resources` (the Operator ignores resources set under `unsupported.podTemplate`), while health-probe tuning goes the opposite way, through `unsupported.podTemplate`.

**KO:** Keycloak은 플랫폼의 SSO/OIDC 아이덴티티 제공자입니다 — SSO가 연동된 모든 애플리케이션(ArgoCD, Grafana, Harbor, Headlamp, OpenBao, velero-ui, Gitea, Kubernetes API 서버 자체, 그리고 포털)이 이를 통해 인증합니다. 일반 Helm 차트가 아니라 업스트림 Keycloak Operator가 조정(reconcile)하는 `Keycloak` CR로 배포되며, Narwhal 브랜딩이 적용된 커스텀 로그인 테마(`keycloak-theme-narwhal` ConfigMap, 한/영 이중언어 메시지)가 마운트되어 있습니다. CPU/메모리 요청은 반드시 `spec.resources`에 있어야 하며(Operator가 `unsupported.podTemplate` 아래의 resources는 무시함), 헬스 프로브 튜닝은 반대로 `unsupported.podTemplate`을 통해 적용됩니다.

### Registry & Developer UI / 레지스트리 및 개발자 UI

#### Harbor
| Chart | Version | Namespace |
|---|---|---|
| `harbor` (helm.goharbor.io) | `1.19.1` | `devtools` |

**EN:** The platform's private container registry — image storage, vulnerability scanning integration, and OIDC-based access control (auto-onboards Keycloak users via a dedicated `harbor_username` attribute, since Harbor reserves its built-in `admin` account). Uses an external CloudNative-PG-backed PostgreSQL database rather than the chart's bundled one.

**KO:** 플랫폼의 프라이빗 컨테이너 레지스트리로, 이미지 저장, 취약점 스캔 연동, OIDC 기반 접근 제어(Harbor가 내장 `admin` 계정을 예약해 두므로 전용 `harbor_username` 속성으로 Keycloak 사용자를 자동 온보딩)를 제공합니다. 차트에 내장된 DB 대신 CloudNative-PG 기반 외부 PostgreSQL을 사용합니다.

#### Headlamp
| Chart | Version | Namespace |
|---|---|---|
| `headlamp` (kubernetes-sigs.github.io/headlamp) | `0.42.0` | `devtools` |

**EN:** A web-based Kubernetes dashboard/UI for cluster operators, authenticated via Keycloak OIDC. Opted out of the ambient mesh (`istio.io/dataplane-mode: none`) to avoid ztunnel interfering with its SSO cookies, with probe-timeout tuning applied separately by a companion Kyverno policy (`headlamp-policy.yaml`).

**KO:** 클러스터 운영자를 위한 웹 기반 Kubernetes 대시보드/UI로, Keycloak OIDC로 인증됩니다. ztunnel이 SSO 쿠키를 간섭하지 않도록 ambient 메시에서 제외(`istio.io/dataplane-mode: none`)되어 있으며, 프로브 타임아웃 튜닝은 별도의 Kyverno 정책(`headlamp-policy.yaml`)으로 적용됩니다.

### Portal / 포털

#### Narwhal Portal
| Manifest | Image | Namespace |
|---|---|---|
| `narwhal-portal-k8s.yaml` (Deployment/Service/RBAC/Valkey) | `ghcr.io/dasomel/narwhal-portal:1.0.1` (public GHCR, SemVer-pinned) | `devtools` |

**EN:** The Narwhal management portal (Next.js) — the developer-facing UI for this entire IDP: cluster architecture view, ArgoCD app status, security/vulnerability reports, node metrics, logs/traces, backup status, and a component "scorecard" (checks ArgoCD sync health, image provenance, PDB/NetworkPolicy presence). Deployed with a least-privilege `ServiceAccount` (cluster-wide read-only RBAC plus a namespace-scoped Role for its own self-service Jobs), a dedicated Valkey cache Deployment, and the internal root CA mounted via `NODE_EXTRA_CA_CERTS` so its server-side calls to Keycloak/ArgoCD/OpenBao/K8s API trust the cluster's TLS. The image tag is an immutable SemVer pin (not `:latest`) so ArgoCD can actually detect upgrades as a manifest diff.

**KO:** Narwhal 관리 포털(Next.js)은 이 IDP 전체를 위한 개발자 대상 UI입니다 — 클러스터 아키텍처 뷰, ArgoCD 앱 상태, 보안/취약점 리포트, 노드 메트릭, 로그/트레이스, 백업 상태, 그리고 컴포넌트 "스코어카드"(ArgoCD 동기화 상태, 이미지 출처, PDB/NetworkPolicy 존재 여부 점검)를 제공합니다. 최소 권한 `ServiceAccount`(클러스터 전역 읽기 전용 RBAC + 자체 셀프서비스 Job을 위한 네임스페이스 한정 Role), 전용 Valkey 캐시 Deployment와 함께 배포되며, 내부 루트 CA가 `NODE_EXTRA_CA_CERTS`로 마운트되어 Keycloak/ArgoCD/OpenBao/K8s API로의 서버측 호출이 클러스터 TLS를 신뢰하도록 되어 있습니다. 이미지 태그는 불변 SemVer 고정 값(`:latest` 아님)이라 ArgoCD가 업그레이드를 실제 매니페스트 diff로 감지할 수 있습니다.

---

## Conventions / 규칙

**EN**
- **SemVer-pinned images and charts.** Mutable tags like `:latest` are avoided in GitOps manifests — ArgoCD diffs the rendered manifest string, so a re-published `:latest` looks identical and silently fails to trigger a sync (see the portal image, which learned this the hard way and is now pinned to `1.0.1`). Chart `targetRevision` is always an explicit version.
- **GitOps-only changes.** Every persistent change goes through this repo → push to Gitea → ArgoCD auto-syncs. `kubectl apply`/`edit` against a managed resource is reverted by `selfHeal` on the next reconcile loop; it's only useful for throwaway debugging.
- **Namespace overview:**

| Namespace | Purpose |
|---|---|
| `platform-system` | Shared infra plane: MetalLB, APISIX, cert-manager, Kyverno (PERMISSIVE mTLS exception — talks to non-mesh clients) |
| `istio-system` | Service mesh control plane and dataplane (istiod, istio-cni, ztunnel) |
| `monitoring` | Observability stack: Prometheus, Grafana, Alertmanager, Loki, Tempo (PERMISSIVE mTLS exception for the opted-out portal) |
| `storage` | SeaweedFS, OpenBao, Velero, Velero UI |
| `security-system` | Trivy Operator (and, when re-enabled, Falco) |
| `iam` | Keycloak (intentionally NOT mesh-enrolled) |
| `devtools` | ArgoCD, Gitea, Harbor, Headlamp, the Narwhal Portal (PERMISSIVE mTLS exception scoped to Harbor's mixed-enrollment workloads) |
| `database` | CloudNative-PG PostgreSQL clusters (PERMISSIVE mTLS exception — plain-TCP calls from non-mesh Keycloak) |

**KO**
- **SemVer로 고정된 이미지/차트.** `:latest`처럼 변경 가능한 태그는 GitOps 매니페스트에서 지양합니다 — ArgoCD는 렌더링된 매니페스트 문자열을 diff하므로, 재게시된 `:latest`는 동일하게 보여 동기화 트리거가 조용히 실패합니다(포털 이미지가 이를 직접 겪은 뒤 `1.0.1`로 고정됨). 차트 `targetRevision`도 항상 명시적 버전을 사용합니다.
- **GitOps 전용 변경.** 지속적인 변경은 모두 이 저장소 수정 → Gitea push → ArgoCD 자동 동기화 순서를 따릅니다. 관리 대상 리소스에 대한 `kubectl apply`/`edit`는 다음 재동기화 루프에서 `selfHeal`이 되돌립니다 — 일회성 디버깅 용도로만 유용합니다.
- **네임스페이스 개요:** 위 표 참고.

---

<sub>Generated to describe the state of `gitops/apps` and `gitops/charts` as of the versions pinned in each manifest — always trust the manifests over this document if they diverge. See `../CLAUDE.md` and `../VERSIONS.md` for provisioning scripts and version history.</sub>
<sub>이 문서는 각 매니페스트에 고정된 버전을 기준으로 `gitops/apps`, `gitops/charts`의 상태를 설명합니다 — 내용이 어긋난다면 항상 매니페스트를 우선하세요. 프로비저닝 스크립트와 버전 이력은 `../CLAUDE.md`, `../VERSIONS.md`를 참고하세요.</sub>
