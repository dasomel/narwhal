# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-07-08

First stable release — validated by consecutive zero-fix, from-scratch clean installs
(6-node HA control plane, Ubuntu 26.04 / kernel 7.0, ARM64).

### Highlights
- **IDP Portal (Next.js 16 + React 19) ships from a pinned public image** `ghcr.io/dasomel/narwhal-portal:1.0.0` (GHCR, multi-arch); the in-cluster Kaniko build is demoted to an optional developer self-service tool.
- **Harbor hardened**: internal shared secrets externalized to the `harbor-shared-secrets` K8s Secret (no plaintext in git); all component images pinned to immutable `:v2.15.1` (fixes the stale-`:latest` amd64 layer crashloop — `exec format error` — on ARM64 kernel-7.0 nodes); metrics/exporter disabled.
- **Portal ↔ cluster seam fixed end-to-end**: `monitoring` PERMISSIVE mTLS exception (metrics/logs/traces/alerts now reach the non-mesh portal), retry-hardened ArgoCD API-token and ServiceAccount-token issuance, kube-apiserver CA added to the portal trust bundle, and trivy scan-job CPU/memory tuned for the 2-core workers.
- **Falco disabled on kernel 7.0** (`modern_ebpf` `scap_init` incompatibility, documented in the mistakes log) — runtime security coverage remains via trivy-operator, kyverno, NetworkPolicy, and STRICT mTLS.
- CI (Lint & Validate / ShellCheck) is green; the READMEs (EN + KO) gain a live portal screenshot gallery.

### Changed
- **Domain migration `local.narwhal.io` → `local.narwhal.internal`** (2026-06-28): all 61 source
  files updated. Reason: `narwhal.io` is a real public domain — its wildcard DNS entry shadowed the
  internal cluster domain, causing external DNS resolution to win over the local dnsmasq. The
  `.internal` TLD is ICANN-reserved for private use and is never publicly resolvable, eliminating
  the shadow. Takes effect on next clean install; existing live clusters still run `.io`.

### Added
- OIDC RBAC 테스트 섹션 (`test-sso.sh` 8/8: 15개 체크)
- Keycloak `kubernetes` 클라이언트 audience mapper (K8s API 서버 `aud` 클레임 검증)
- `--oidc-ca-file` API 서버 플래그 (self-signed 인증서 JWKS 검증)
- `microprofile-jwt` 스코프 중복 groups 매퍼 자동 정리
- SSO 웹 서버 Istio ambient mesh opt-out (`istio.io/dataplane-mode: none`)
- 앱별 접근제어(인가) 강화: 4계층(cluster-admin/developer/viewer/guest) 전체 적용
  - ArgoCD: guest→`role:none` 명시적 차단 (deny 정책 추가)
  - Gitea: `narwhal` Organization + Developers/Viewers 팀 자동 생성, `--restricted-group guest` + `--group-team-map` 설정
  - Harbor: 프로젝트 `library`에 developer→Developer, viewer→Guest 그룹 멤버 자동 설정
  - OAuth2-Proxy: `allowed_groups` 필터 추가 (guest 그룹 차단)
- `test-sso.sh` 9/9 앱별 접근제어 검증 섹션 추가

### Fixed
- `kcadm.sh --format csv --noquotes` 잘못된 ID 반환 → jq 기반 조회로 교체 (11곳)
- `test-sso.sh` 구 네임스페이스 참조 (keycloak→iam, argocd→devtools 등)
- `kubectl auth can-i` 경고 메시지로 인한 테스트 false negative
- Istio ambient mesh ztunnel SSO 쿠키 손상 → ArgoCD, Grafana, Harbor, Gitea 등 SSO 실패
- Gitea OAuth2 소스 이름 대소문자 불일치 (`Keycloak` → `keycloak`)

## [0.2.0] - 2026-02-24

### Added
- 기능별 네임스페이스 통합 (`platform-system`, `iam`, `devtools`, `storage`, `dev`)
- 4사용자 체계: `admin`, `dev`, `view`, `guest`
- 4그룹 체계 (단수형): `cluster-admin`, `developer`, `viewer`, `guest`
- OIDC 그룹 기반 K8s RBAC (ClusterRoleBinding + RoleBinding)
- Keycloak HTTPRoute 조기 생성 (OIDC 검증 전 필수)
- Keycloak HBONE NetworkPolicy (`keycloak-allow-hbone`, 포트 15008)
- ArgoCD ambient mesh opt-out (`istio.io/dataplane-mode: none`)
- ArgoCD ClusterRoleBinding namespace 자동 패치 (devtools)
- CoreDNS hairpin fix (Traefik ClusterIP로 `*.local.narwhal.internal` 해석)
- README에 릴리스/라이선스 배지

### Changed
- 네임스페이스 재구조화: 개별 OSS NS → 기능별 통합 NS
  - `metallb-system`, `traefik`, `cert-manager`, `cnpg-system`, `kyverno` → `platform-system`
  - `keycloak`, `oauth2-proxy` → `iam`
  - `argocd`, `gitea`, `harbor`, `headlamp` → `devtools`
  - `seaweedfs`, `velero`, `openbao` → `storage`
- 그룹명 복수→단수: `cluster-admins`→`cluster-admin`, `developers`→`developer`, `viewers`→`viewer`
- 사용자명 변경: `k8s-admin`→`admin`, `developer`→`dev`
- PeerAuthentication STRICT → PERMISSIVE (kubelet probe + external 트래픽 허용)
- Keycloak hostname v1(`hostname-url`) → v2(`hostname.hostname` + `hostname.strict`)

### Fixed
- Keycloak Operator NetworkPolicy에 HBONE 15008 누락 (mesh-to-mesh 통신 실패)
- ArgoCD ClusterRoleBinding subject namespace 불일치 (`argocd`→`devtools`)
- ArgoCD pods CrashLoopBackOff (ztunnel이 kubelet probe 차단)
- yq OIDC URL에 셸 따옴표 삽입 → API 서버 크래시
- API 서버 OIDC 플래그 추가 시점 문제 (HTTPRoute 미존재)

## [0.1.0] - 2026-02-20

### Added
- Vagrant 기반 Kubernetes v1.35 IDP 클러스터 자동 프로비저닝
- 2-Phase 프로비저닝 구조 (Phase 1: 클러스터 인프라, Phase 2: 플랫폼 앱)
- HA Control Plane: 3 masters + kube-vip VIP (192.168.56.100)
- CNI: Cilium v1.19 (kube-proxy replacement) + Hubble 네트워크 옵저버빌리티
- Service Mesh: Istio v1.29 ambient mode (zero sidecars, ztunnel mTLS)
- Gateway API: Traefik v3.6 + cert-manager self-signed TLS
- Storage: NFS (Block) + SeaweedFS (S3) + nfs-quota-agent
- Database: CloudNative-PG v1.28 통합 `narwhal-db` (Keycloak, Gitea, Harbor 공유)
- IAM/SSO: Keycloak v26.5 OIDC (6개 앱 연동: ArgoCD, Grafana, Gitea, Harbor, Headlamp, OAuth2-Proxy)
- GitOps: ArgoCD v3.3 + Gitea v1.25 (App-of-Apps 패턴)
- Observability: Prometheus + Grafana + Loki + Tempo + Promtail
- Security: cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- Backup: Velero + CNPG barman → SeaweedFS S3
- Networking: MetalLB (LoadBalancer), dnsmasq (로컬 DNS)
- Dashboard: Headlamp + OAuth2-Proxy
- 클러스터 검증 스크립트 (`verify-cluster.sh`, `test-sso.sh`)
- SSO CA cert 자동 배포 (Headlamp, Grafana, ArgoCD)
- DNS HA: 모든 master 노드에 dnsmasq, CoreDNS forward 설정

### Changed
- 3 CNPG 클러스터 → 통합 `narwhal-db` (HA failover, ExternalName 서비스)
- 개별 스크립트 → kube-ready-box 기반 공통 스크립트 분리
- 토폴로지: 단일 노드 → 3m+3w (Master NoSchedule, Worker에서 플랫폼 앱 실행)
- NFS StorageClass: 계층적 subDir + 최적화된 mountOptions

[Unreleased]: https://github.com/dasomel/narwhal/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/dasomel/narwhal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
