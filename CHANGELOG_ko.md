# Changelog

이 프로젝트의 주요 변경 사항은 이 파일에 기록됩니다.

이 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르며,
이 프로젝트는 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 준수합니다.

[English](CHANGELOG.md) | 한국어

## [Unreleased]

## [1.2.0] - 2026-08-08

Vagrant (arm64) 및 Kakao Cloud (amd64) 환경 모두에서 에어갭이 "이미지 전용"에서 인터넷 경로 없이
완전히 완료되는 설치 방식으로 전환됨.

### 추가
- **실제로 폐쇄된 폐쇄망 설치**: 번들에 Helm 차트, 바이너리(helm/cilium/hubble/yq),
  원격 매니페스트 및 OS 패키지가 포함됨. 이전에는 컨테이너 이미지만 번들링되고 나머지는 이그레스
  프록시 뒤에 숨겨진 공용 인터넷에서 가져왔음. `AIRGAP=1` 설정 시 APT를 `file:///srv/airgap/apt`로
  전환하고 기본 라우트를 삭제하여 격리가 추정이 아닌 강제로 적용됨.
- **일등 시민(First-class) 프로바이더로서의 Kakao Cloud**: OpenTofu 인프라 생성, 바스천
  프록시/레지스트리 단계, 바스천 dnsmasq에서 제공하는 `kakao.narwhal.internal` 서비스 존,
  아키텍처별 번들, 접속을 위한 `set-config-kakao.sh` / `setup-hosts-kakao.sh` 제공.
- **격리 도구**: `scripts/test/verify-isolation.sh`가 노드별 라우트, networkd 드롭인,
  직접 이그레스, 미러 도달 가능성, APT 소스를 검사함. `airgap-isolate-kakao.sh`는 이미
  프로비저닝된 노드를 격리함. `docs/common/airgap-isolation-testing.md`에 문서화됨.
- **Chaos Mesh 2.8.3 + k6 부하 테스트 수트**: Grafana 대시보드 및 Prometheus remote-write
  리시버 포함, Dr. Pym 적정 규모 산정(rightsizing) 권장 사항 포함.
- **클린 설치 회귀 테스트 수트** (`regression-check-kakao.sh`, 36개 정적 + 런타임 검사,
  각 검사는 날짜가 기재된 lessons-log 항목에 매핑됨)가 모든 푸시 시 CI에 연동됨.

### 수정
- **에어갭 미러가 단 하나의 풀(pull)도 처리하지 못했던 문제**: Ubuntu의 containerd 2.2.1이
  `config_path`를 콜론으로 구분된 쌍으로 제공했으나, containerd가 이를 허용하지 않아 해당
  문자열 자체를 디렉터리 이름으로 찾고 모든 `hosts.toml`을 무시했음. 한 노드에서 두 방식을 측정한
  결과: 콜론 형식 → `network is unreachable`, 단일 경로 → 모든 이미지를 미러에서 풀함(레지스트리
  로그 containerd 요청 0개 → 132개).
- **미러 커버리지**: 이미지 목록의 11개 레지스트리 중 5개만 `hosts.toml`을 가지고 있어
  istiod, istio-cni, gitea, argocd-redis가 업스트림에서 풀되었음. 이제 목록은 `images.txt`에서
  추출됨.
- **GitOps가 오프라인에서 동기화되지 않던 문제**: 모든 ArgoCD Application이 공개 차트
  리포지토리를 지정하고 있었음. 이제 차트는 클러스터 내 Gitea Helm 레지스트리에서 가져옴.
- **격리가 DHCP 갱신 시 유지되지 않던 문제**: `ip route del default`가 다음 임대 시 취소되었으나,
  networkd 드롭인(`UseGateway=false`)을 통해 지속성을 확보함. Kakao에서는 게이트웨이만 삭제됨(임대
  정보에 메타데이터/NTP 라우트도 포함됨).
- **Phase 2가 플랫폼 앱이 전혀 없는 상태에서도 성공을 보고할 수 있었던 문제**: Gitea 및
  GitOps 부트스트랩 단계가 경고만 출력하고 계속 진행되어, 백엔드가 없는 503 상태의 4개 서비스가
  남았음. 두 단계 모두 이제 필수(critical) 처리됨.
- NFS를 NLM 헬퍼 포트가 고정된 v3로 전환(v4.1 클라이언트/서버 교착 상태로 약 24시간 동안
  스토리지 중단 발생), kube-apiserver 힙 제한 설정, ArgoCD, Cilium operator, Tempo, Prometheus
  전반에 리소스 requests/limits 설정.

### 보안
- 하드코딩된 S3 자격 증명과 오래된 공인 IP를 리포지토리에서 제거하고, 실수로 커밋된
  6.8 GB 크기의 레지스트리 블롭 스토어를 히스토리에서 정리함 (5.79 GiB → 3.86 MiB).
- 추적(tracked)되면서 *동시에* gitignore된 파일이 존재할 경우 빌드를 실패시키는 신규 검사
  R35 추가 (`.gitignore`는 소급 적용되지 않으므로 해당 블롭 스토어가 유입되었던 원인 차단).

### 제거
- KubeMetal MLOps 연동(MLflow/SeaweedFS/Prefect) 제거. kubemetal은 이제 자체 네임스페이스에
  Helm OCI 차트를 통해 에이전트만 설치하므로 gitops 내보내기의 호출자가 없어졌음. 이에 따라
  두 번들 모두에서 mlflow 및 prefect 이미지가 함께 제거됨.

## [1.1.0] - 2026-07-13

당시 CHANGELOG 항목 없이 릴리스되었으며, 히스토리의 연속성을 위해 `v1.0.0..v1.1.0` 사이의
83개 커밋을 바탕으로 여기에 재구성함.

### 추가
- 제로클릭(zero-click) Keycloak SSO가 적용된 Kubernetes Dashboard 3.0 (차트 7.14.0, 벤더링됨 —
  업스트림 Helm 인덱스가 404 응답).
- 포털과 동기화된 듀얼 모드(라이트/다크) Keycloak 로그인 테마 및 로그아웃 자동 리다이렉트 기능.
- 모든 네임스페이스에 PSA audit/warn 라벨 적용 (KISA-POD-01); 거버넌스 페이지용 Trivy
  컴플라이언스 스캐너 추가.
- ArgoCD Application으로 관리되는 정책, 알림, RBAC 리소스.
- 최초의 `PROVIDER=kakao` 기반 작업: kube-vip 및 Vagrant 전용 경로를 건너뛰는 가드,
  MetalLB→NodePort 인그레스, 바스천 호스트, LB 공인 IP 및 고정된 연속 사설 IP.

### 수정
- argocd-redis를 앰비언트 메쉬(ambient mesh)에서 제외 — 반복적인 EOF 교착 문제의 근본 원인 해결.
- APISIX 프록시 버퍼 크기 증설; 지나치게 큰 `Set-Cookie`로 인해 포털 로그인 콜백에서
  502 에러가 발생하던 문제 해결.
- Harbor의 비결정적 차트 시크릿으로 인해 약 5분 간격의 무한 롤아웃이 발생하던 문제 해결.
- SLO 체인을 위한 로그아웃 후 리다이렉트 URI 등록.

## [1.0.0] - 2026-07-08

첫 번째 안정화 릴리스 — 연속적인 수정 없는 처음부터의 클린 설치로 검증됨
(6노드 HA 컨트롤 플레인, Ubuntu 26.04 / 커널 7.0, ARM64).

### 주요 변경
- **IDP 포털(Next.js 16 + React 19)이 고정된 공개 이미지로 제공됨**: `ghcr.io/dasomel/narwhal-portal:1.0.0` (GHCR, 멀티 아키텍처); 클러스터 내 Kaniko 빌드는 선택적 개발자 셀프서비스 도구로 변경됨.
- **Harbor 보안 강화**: 내부 공유 시크릿을 `harbor-shared-secrets` K8s Secret으로 외부에 분리(git에 평문 없음); 모든 컴포넌트 이미지를 불변 태그 `:v2.15.1`로 고정(ARM64 커널 7.0 노드에서 오래된 `:latest` amd64 레이어 CrashLoopBackOff — `exec format error` — 수정); 메트릭/exporter 비활성화.
- **포털 ↔ 클러스터 연동부 종단 간 수정**: `monitoring` PERMISSIVE mTLS 예외 처리(메트릭/로그/트레이스/알림이 메쉬 외부 포털에 도달함), 재시도 보강된 ArgoCD API 토큰 및 ServiceAccount 토큰 발급, 포털 신뢰 번들에 kube-apiserver CA 추가, 2코어 워커 노드에 맞게 trivy 스캔 작업 CPU/메모리 튜닝.
- **커널 7.0에서 Falco 비활성화** (`modern_ebpf` `scap_init` 호환성 문제, mistakes log에 문서화됨) — trivy-operator, kyverno, NetworkPolicy 및 STRICT mTLS를 통해 런타임 보안 커버리지 유지.
- CI (Lint & Validate / ShellCheck) 정상 통과; README (영문 + 국문)에 실시간 포털 스크린샷 갤러리 추가.

### 변경
- **도메인 마이그레이션 `local.narwhal.io` → `local.narwhal.internal`** (2026-06-28): 총 61개 소스
  파일 업데이트. 사유: `narwhal.io`는 실제 공개 도메인으로, 와일드카드 DNS 항목이 내부 클러스터
  도메인을 가려 로컬 dnsmasq 대신 외부 DNS 확인이 우선되는 문제가 있었음. `.internal` TLD는
  사용을 위해 ICANN에 의해 예약된 TLD로 공개적으로 해석되지 않으므로 샤도잉 문제가 해결됨.
  다음 클린 설치부터 적용되며, 기존 라이브 클러스터는 여전히 `.io`로 동작함.

### 추가
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

### 수정
- `kcadm.sh --format csv --noquotes` 잘못된 ID 반환 → jq 기반 조회로 교체 (11곳)
- `test-sso.sh` 구 네임스페이스 참조 (keycloak→iam, argocd→devtools 등)
- `kubectl auth can-i` 경고 메시지로 인한 테스트 false negative
- Istio ambient mesh ztunnel SSO 쿠키 손상 → ArgoCD, Grafana, Harbor, Gitea 등 SSO 실패
- Gitea OAuth2 소스 이름 대소문자 불일치 (`Keycloak` → `keycloak`)

## [0.2.0] - 2026-02-24

### 추가
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

### 변경
- 네임스페이스 재구조화: 개별 OSS NS → 기능별 통합 NS
  - `metallb-system`, `traefik`, `cert-manager`, `cnpg-system`, `kyverno` → `platform-system`
  - `keycloak`, `oauth2-proxy` → `iam`
  - `argocd`, `gitea`, `harbor`, `headlamp` → `devtools`
  - `seaweedfs`, `velero`, `openbao` → `storage`
- 그룹명 복수→단수: `cluster-admins`→`cluster-admin`, `developers`→`developer`, `viewers`→`viewer`
- 사용자명 변경: `k8s-admin`→`admin`, `developer`→`dev`
- PeerAuthentication STRICT → PERMISSIVE (kubelet probe + external 트래픽 허용)
- Keycloak hostname v1(`hostname-url`) → v2(`hostname.hostname` + `hostname.strict`)

### 수정
- Keycloak Operator NetworkPolicy에 HBONE 15008 누락 (mesh-to-mesh 통신 실패)
- ArgoCD ClusterRoleBinding subject namespace 불일치 (`argocd`→`devtools`)
- ArgoCD pods CrashLoopBackOff (ztunnel이 kubelet probe 차단)
- yq OIDC URL에 셸 따옴표 삽입 → API 서버 크래시
- API 서버 OIDC 플래그 추가 시점 문제 (HTTPRoute 미존재)

## [0.1.0] - 2026-02-20

### 추가
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

### 변경
- 3 CNPG 클러스터 → 통합 `narwhal-db` (HA failover, ExternalName 서비스)
- 개별 스크립트 → kube-ready-box 기반 공통 스크립트 분리
- 토폴로지: 단일 노드 → 3m+3w (Master NoSchedule, Worker에서 플랫폼 앱 실행)
- NFS StorageClass: 계층적 subDir + 최적화된 mountOptions

[Unreleased]: https://github.com/dasomel/narwhal/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/dasomel/narwhal/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/dasomel/narwhal/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dasomel/narwhal/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/dasomel/narwhal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
