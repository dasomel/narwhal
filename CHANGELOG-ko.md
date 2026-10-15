# Changelog

이 프로젝트의 주요 변경 사항은 이 파일에 기록됩니다.

이 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르며,
이 프로젝트는 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 준수합니다.

[English](CHANGELOG.md) | 한국어

## [Unreleased]

보안 강화 중심의 릴리스. 에어갭 번들과 오프라인 업그레이드 번들에 무결성 검증 체인이 추가되어 승격 전에 검사되며,
ArgoCD, Gitea, RBAC, OpenBao, Keycloak, APISIX, NFS, `database` 신뢰 경계가 강화되었고, 이 경계가 퇴행하지 않도록
정적 CI 게이트가 다수 추가됨. 기존 클러스터나 번들에서 운영자 조치가 필요한 변경이 여러 건 있으므로 먼저
**업그레이드 참고**를 읽을 것.

### 업그레이드 참고

- **다이제스트 인벤토리 도입 이전에 만든 에어갭 번들은 다시 저장해야 함 (#337).** `02-save-images.sh`가 이제
  `images.digests.json`을 기록하며, `09-verify-bundle-completeness.sh`는 이 파일이 없거나, 형식이 잘못되었거나,
  정렬되지 않았을 때, 이미지에 항목이 없을 때, `oci/` 디렉터리가 기록되어 있지 않을 때, 다이제스트 또는 플랫폼이
  일치하지 않을 때 실패(fail closed)함. 이전에 저장한 번들은 `02`를 다시 실행하기 전까지 `09`에서 실패하며,
  `05`(로드)는 변경 없음.
- **`09`가 모든 OCI 레이아웃의 바이트를 읽음 (#336, #338).** manifest, index, config blob의 해시를 재계산하고,
  레이어 존재 여부와 크기를 확인하며, 심볼릭 링크된 blob을 거부하고, 다이제스트로 고정된 ref가 고정된 다이제스트를
  유지하도록 요구하며, 이미지 config의 `os/architecture`가 기록된 `collapsed_to` 플랫폼과 다르면 실패함
  (amd64 번들에 arm64 이미지가 들어간 경우 이제 거부됨).
- **업그레이드 번들 승격이 번들 생성 시각과 출처에 대해 fail closed로 동작 (#335).**
  `promote-upgrade-bundle.sh`는 `created_at`이 `UPGRADE_BUNDLE_MAX_AGE_DAYS`(기본 90, 허용 범위는 0 초과 3650
  이하이며 0은 검사를 끄지 않음)보다 오래되었거나, 없거나, 형식이 잘못되었거나, 미래 시각이거나,
  `provenance.author` / `provenance.source_repo`가 비어 있는 번들을 거부함. 이 값을 올리는 것은 검토를 거친
  결정이어야 함. `--rollback`은 검증기를 실행하지 않으므로 신선도 검사를 적용하지 않음.
  `scripts/airgap/lib/`의 샘플 매니페스트는 2026-08-01과 2026-08-25 날짜이므로 기본 상한을 통과하는 기간이
  각각 2026-10-30, 2026-11-23까지임. CI는 이 상한을 적용하지 않음.
- **업그레이드 번들 매니페스트 검증이 더 엄격해짐 (#318, #321, #324, #327, #331).**
  아티팩트 ID는 유일하고 비어 있지 않아야 하며, 다이제스트 길이는 알고리즘과 일치해야 하고, 승격 시 검토된
  라이선스 정책이 강제되며, 라이선스만 바뀐 변경도 dry-run diff에 나타나고, 후보 번들에서 제거된 의존성이
  보고됨. ID가 중복되거나 비어 있거나, 다이제스트 길이가 틀렸거나, 라이선스 정책을 위반한 매니페스트는 승격 시
  거부됨.
- **이미지 목록 수집이 엄격해짐 (93161df, #222).** 번들된 차트를 렌더링하지 못하면(따라서 hook/Job 이미지를
  수집하지 못하면) `01-generate-image-list.sh`가 중단됨. 로컬 반복 작업에서는 `AIRGAP_ALLOW_INCOMPLETE=1`로
  해제할 수 있음. `up.sh`는 VM 작업 전에 `check-bundle.sh`를 실행하며 호스트 아키텍처용 번들이 없으면 일찍 중단됨.
- **에어갭 Trivy가 클러스터 내 Harbor에서 DB를 가져옴 (9377dee, #302, #320).** 취약점 DB, Java DB, 정책 번들을
  더 이상 `ghcr.io`에서 실시간으로 받지 않음. `scripts/airgap/lib/fetch-security-db.sh`,
  `promote-security-db.sh`, `push-security-db.sh`로 준비해야 함(Harbor 프로젝트 `trivy-db`). 미래 시각이거나
  형식이 잘못된 신선도 증거는 거부됨.
- **ArgoCD Application이 더 이상 `default` AppProject에서 실행되지 않음 (6ead267).** `13-argocd.sh`가 새
  `platform`, `tenants` AppProject(`gitops/resources/argocd-projects.yaml`)를 적용하며 적용하지 못하면 실패함.
  목록에 없는 네임스페이스나 리포지토리를 지정한 Application은 동기화 시점에 거부됨. ArgoCD `developer` 역할은
  `tenants/*`만 동기화할 수 있음(기존 `*/*`). 따라서 개발자는 argocd, keycloak, kyverno를 동기화할 수 없음.
- **`narwhal-gitops`가 비공개 Gitea 리포지토리가 됨 (52dbb8d).** ArgoCD는 전용 `argocd` 계정과
  `read:repository` 토큰(리포지토리 Secret으로 등록)으로 읽으며, 최초 push 이후 `main`은 push 허용 목록으로
  보호됨. `14-gitops-bootstrap.sh`를 다시 실행하면 기존 리포지토리에도 `private: true`가 재적용됨.
  리포지토리 생성, clone, 설정 복사, push 실패는 더 이상 무시되지 않고 부트스트랩을 중단시킴 (65d07b1).
- **`developer` ClusterRole이 읽기 전용이 됨 (7551c21).** 변경 동사와 `exec` / `portforward` / `attach`는 새
  `developer-workload-admin` 역할로 옮겨졌고 `dev`(및 테넌트 네임스페이스, ed06e82)에만 바인딩됨. 그 외 모든
  네임스페이스는 직접 `kubectl` 사용 시 읽기 전용이며, 포털은 자체 ServiceAccount를 쓰므로 영향 없음.
- **포털의 OpenBao 접근이 1시간짜리 Kubernetes 인증 토큰이 됨 (e923d7b, 2b589b7).**
  `13-2-narwhal-portal-bindings.sh`가 `narwhal-portal` Kubernetes 인증 역할(TTL 1h, audience 검증)을 만들고,
  `storage:openbao`에 `system:auth-delegator`를 바인딩하고, projected ServiceAccount 토큰을 마운트하며, 8760h
  토큰을 더 이상 저장하지 않음. 레거시 토큰은 `ENABLE_LEGACY_OPENBAO_TOKEN=true`일 때만 발급됨. 포털 정책은 KV-v2
  메타데이터 접근만 유지하고 `secret/data/narwhal-portal/*`는 더 이상 부여하지 않음. 기존 배포에서 무엇을 다시
  적용해야 하는지는 확인되지 않음(unverified). 해당 커밋은 여기서 라이브 클러스터에 실행되지 않았음.
- **Keycloak OIDC TLS가 검증되며 실패 시 차단됨 (5352bc2, dae7237, 3950512, 6790454).** ArgoCD OIDC 설정에서
  `insecureSkipVerify`가 제거되었고 `rootCA`가 `argocd-cm` / `argocd-secret`에 주입됨.
  `11-3-keycloak-clients.sh`는 `narwhal-wildcard-tls`와 `narwhal-ca-cert` 어디에서도 CA를 얻지 못하면 exit 1로
  종료함. `test-sso.sh`는 검증이 성공하기를 기대함.
- **APISIX Admin API는 파드 CIDR과 루프백만 허용 (1dddbe4).** 허용 소스는 `10.244.0.0/16`과 `127.0.0.1/32`이며,
  NetworkPolicy가 `:9180` ingress를 제한함. 장애 복구(break-glass)용 파드에는 라벨
  `app.kubernetes.io/name=apisix-ingress-controller`가 있어야 함(`docs/common/apisix-etcd-recovery.md`,
  `docs/common/apisix-admin-api.md`).
- **NFS export 기본값이 `root_squash`와 모드 750 루트로 변경 (6151b86, 7e4e622).** 두 export 줄에서
  `no_root_squash`가 제거됨. `root_squash`는 디스크에 이미 있는 소유권을 바꾸지 않으므로
  `docs/common/compliance-hardening.md`의 마이그레이션 런북을 따르고 `scripts/test/verify-nfs-root-squash.sh`로
  확인할 것. 비루트 쓰기를 위해 `csi-driver-nfs`는 `feature.enableFSGroupPolicy=false`로 실행되며(불변 `File`
  정책을 가진 라이브 CSIDriver는 사전 업그레이드 가드가 삭제함) Grafana의 `initChownData`는 비활성화됨.
- **`database` 네임스페이스가 기본적으로 ingress를 차단 (3148c14).** CloudNativePG, 그 오퍼레이터, Keycloak,
  Harbor, Gitea만 허용되며 그 외 호출자는 차단됨.
- **ArgoCD 캐시가 Valkey로 실행됨 (3a3aef9).** `13-argocd.sh`가 `argocd-redis`를
  `docker.io/valkey/valkey:8-alpine`으로 지정함(Redis 8은 RSALv2/SSPL/AGPL). 기존 클러스터는 `13-argocd.sh`를
  다시 적용하면 반영됨.
- **포털이 `narwhal-portal:1.0.19`로 고정됨 (2a11c10, #245).** Cosign 서명이 없으며, 1.0.18은 시작 시
  크래시하므로 사용하면 안 됨(`SECURITY.md`).
- **`up.sh`가 Phase 2가 불완전하면 exit 2로 종료 (#225)**, macOS 로컬 네트워크 권한이 VM을 막는 경우 정확한
  시스템 설정 수정 방법을 출력하고 중단함.
- **Kakao Cloud (e496631, 8f24bcf, eda06db).** 바스천이 자체 보안 그룹을 가지며, SSH는 필수값인
  `bastion_ssh_allowed_cidrs`로 제한되고 `0.0.0.0/0`은 거부됨. SSH 호스트 키는 클러스터별 `known_hosts`로
  검증되며(최초 접속 시 신뢰, 키가 바뀌면 차단), 노드나 바스천을 재구축한 후에는 `kakao_forget_host`로 이전
  항목을 지울 것. 큐블릿 포트 10250은 노드 서브넷에만 열림.

### 추가

- **오프라인 업그레이드 번들 파이프라인 (7becc87, #300, #301).** JSON 스키마, 검증기, 승인이 포함된 승격
  게이트, dry-run diff, 내용 및 의존성 검증, 컴포넌트 업그레이드 웨이브 그래프의 정적 검사(#46). cert-manager가
  첫 오케스트레이션 파일럿임 (dd9ca0e).
- **에어갭 번들 무결성 체인 (#336, #337, #338).** OCI 레이아웃 바이트 검증, 저장 시점 이미지별 다이제스트
  인벤토리, `09` 게이트의 config 플랫폼 검증. `--report FILE`로 JSON 레이아웃 리포트를 기록함.
- **오프라인 취약점 DB 번들 무결성 검사** `scripts/verify/security-db-bundle-check.sh` (#302).
- **불변 릴리스 기반 작업 (24fedbb, a12401c, b342bff).** `image-digests.tsv`가 각 태그가 해석된 다이제스트를
  기록하며 `09`는 행이 없는 이미지가 있으면 실패함. kaniko와 alpine/git은 불변 태그로 고정되고, 예약된
  워크플로가 이미지 다이제스트 드리프트를 감지함.
- **공급망 CI 게이트.** 가변 이미지 태그를 병합 전에 거부(ad7804c)하고 모든 워크플로의 가변 패키지 설치를
  거부(#316)하며, pip 요구 사항 옵션에는 해시가 필요하고(#330), `component-licenses.tsv`에 대한 라이선스 정책
  게이트(aa9c1a4), SBOM·커밋·워크플로 실행·취약점 상관 분석(6dab398, #317), 오프라인 번들용 CycloneDX
  SBOM(c6c16dd), 이미지별 라이선스 맵이 포함된 `NOTICE`(c65f3ab), 모든 릴리스 아티팩트는 목록에 있거나
  `UNKNOWN`으로 검토되어야 함(#295), kubeconform CRD 스키마를 벤더링하고 `-ignore-missing-schemas`를
  제거(7493182), 서드파티 다운로드는 고정되고 체크섬이 검증됨(fc31efb).
- **서비스 메시용 신규 정적 게이트.** R242는 `09-istio-ambient.sh`의 `AMBIENT_NAMESPACES`가 NetworkPolicy
  검사가 다루는 네임스페이스와 다르면 CI를 실패시킴(#332). R243은 `PeerAuthentication`의 `PERMISSIVE` /
  `DISABLE`을 검토된 허용 목록에서만 허용하고, 메시 전역 `STRICT`를 요구하며, 기존 예외 2건을 개수가 고정된 알려진
  이슈로 기록함(#333, #334).
- **보안 태세용 정적 게이트 추가 (#283-#293, #319, #322, #325, #326).** 웹훅 `failurePolicy`와 타임아웃,
  `hostPath` 마운트(중첩된 CronJob 포함), Pod Security Admission 라벨, 워크로드 런타임 태세와 RuntimeClass 참조,
  seccomp 오버라이드, kubelet 및 API 서버 인가 기준선, 비활성화된 `NodeRestriction` 거부.
- **노드 및 etcd 검증기.** `scripts/verify/kubelet-authz-check.sh`(#227), 노드 측 static pod 매니페스트
  검증기(#291), 안전한 키 교체를 위한 `etcd-encryption-check.sh`와 `rotate-etcd-encryption-key.sh`(#233).
  kubeadm 설정은 이제 kubelet 익명 인증 끔, webhook 인증/인가, `readOnlyPort: 0`을 명시함(kubeadm 기본값과
  동일).
- **Kyverno `verify-image-signatures`(Audit, 9b19aa1)와
  `scripts/airgap/10-verify-image-signatures.sh`.** 알려진 이슈 참조: 이 정책은 현재 아무것도 집행하지 않음.
- **Kyverno `restrict-tuning-job-admission` (9742afc).** `devtools`의 privileged 및 호스트 네임스페이스 파드는
  `narwhal-tuning-job` ServiceAccount로 제한됨.
- **풀 리퀘스트 기반 테넌트 네임스페이스 (ed06e82).** `resources/tenants/<team>/<namespace>.yaml`을 `tenants`
  Application이 동기화하며, 포털의 `portal-gitops` 신원은 브랜치를 push할 수 있지만 `main`에 병합할 수 없음.
- **설계 문서와 기준선.** CAPI 대 프로바이더 ADR(#266), Kubernetes/OSS 호환성 매트릭스(#271, #296), 통합 관측성
  계약(#281), 스토리지 보호 정책(#279), 관리 플레인 HA 계약(#280), 이벤트 엔벨롭 기준선과 멀티클러스터 자격 증명
  내보내기(9af2489, 7fc3a45), T1-T7 테스트 분류(c16dc86).

### 변경

- **컴포넌트 고정 (`VERSIONS.md`).** Kubernetes 1.35.5 → 1.35.7(kubeadm/kubelet/kubectl 행은 이제 CI에서
  검사, 80c0c77), nfs-quota-agent 0.2.1 → 0.5.0(d8821a9), Harbor는 불변 태그 v2.15.1로 고정, 포털 이미지
  1.0.16 → 1.0.19(#245).
- **포털 RBAC.** PVC 사전 점검을 위한 `StorageClass`, `ResourceQuota` 읽기 전용 접근(#315), `argocd-server`가
  더 이상 `nodes/proxy`에 접근하지 않음(#255), Velero 서버 역할이 `cluster-admin` 대신 최소 권한이 됨(#250),
  포털의 Gitea 토큰이 최소 권한이 됨(#252, #253).
- **GitOps 부트스트랩이 fail closed로 동작 (65d07b1, a6bc6bb).** ArgoCD가 소스를 바라보기 전에 Gitea API로
  소스를 검증하며, Gitea 토큰 발급은 멱등적임.
- **Keycloak 클라이언트 강화 (e67f234, 50aef34).** 와일드카드 대신 클라이언트별 정확한 `webOrigins`, 업데이트
  경로에서도 `directAccessGrantsEnabled=false`, 예상치 못한 클라이언트가 있으면 부트스트랩을 실패시키는
  `validate-keycloak-clients.sh`.
- **SBOM 상관 분석이 무관한 취약점 식별자와 매칭되지 않음 (#317).**

### 수정

- **Ambient 프로브와 HBONE.** GitOps NetworkPolicy가 이제 kubelet 프로브와 포트 15008을 허용함(#240, #241).
- **네트워킹.** APISIX values 렌더링, Cilium 풀이 `POD_NETWORK_CIDR`을 따름, APISIX viewer 키가 비어 있으면
  스크립트가 중단됨(#236, 0ca929e). Prometheus 파드는 ambient 데이터플레인에서 제외됨(dee1dca).
- **재부팅 후 NFS PVC 마운트.** `rpc-statd`가 이제 부팅 시 시작됨(#249).
- **대시보드.** CSRF Secret이 `apiVersion`과 함께 렌더링되어 앱이 동기화됨(#258).
- **에어갭 이미지 다이제스트**를 업스트림 드리프트 이후 갱신(#194, #259, #310)하고, 포털 Kaniko Job이 고정한
  빌드 헬퍼 다이제스트를 번들에 포함(#262).
- **Keycloak HTTPS 준비 게이트**가 관리 포트 대신 `/realms/master`를 조사함(#231).

### 보안

- `database` 기본 ingress 차단, Gitea ingress를 APISIX를 거치지 않는 정당한 소스로 제한, APISIX Admin API는
  파드 CIDR과 루프백에서만 접근 가능.
- 게이트웨이가 더 이상 클라이언트가 보낸 `X-WEBAUTH-USER`를 신뢰하지 않음 (492e65a).
- ArgoCD 읽기 전용 신원을 가진 비공개 GitOps 리포지토리, AppProject 경계, 클러스터 전역 `exec`나 변경 권한이 없는
  개발자 역할.
- 단기 포털 OpenBao 신원, Keycloak OIDC TLS 검증과 CA 신뢰, `root_squash` NFS export.
- Kakao 바스천 SSH 허용 목록과 검증된 호스트 키 (e496631, 8f24bcf).

### 알려진 이슈

- **이미지 서명이 집행되지 않음.** Kyverno `verify-image-signatures` 정책은 `Audit` 전용이며 `required: false`이고
  공개 키는 PLACEHOLDER(`MFkw...AAE/narwhal/cosign/trusted/signer/pub`)임. cosign 키가 발급되지 않았으므로(#35)
  이 정책은 현재 아무것도 집행하지 않음. 포털 이미지 1.0.19도 Cosign 서명이 없음.
- **부트스트랩 중 메시 전역 PERMISSIVE.** `09-istio-ambient.sh`가 메시 전역 PERMISSIVE `PeerAuthentication`을
  적용하고, GitOps 차트는 같은 이름으로 `STRICT`를 선언하므로 ArgoCD가 수렴하기 전까지 트래픽이 fail-open임
  (#126).
- **`monitoring-permissive`**는 자체 주석이 워크로드별 범위를 권장함에도 `monitoring` 네임스페이스 전체를
  PERMISSIVE로 설정함. R243이 개수를 고정하여 확산되지 않음.
- **`dev` 네임스페이스 라벨 충돌.** `gitops/resources/dev-namespace.yaml`은
  `istio.io/dataplane-mode: none`을 설정하지만 `09-istio-ambient.sh`는 `dev`를 `AMBIENT_NAMESPACES`에 넣고
  ambient로 라벨링함(#128). 라이브 클러스터에서 어느 쪽이 우선하는지는 확인되지 않음(unverified).
- **Ingress 격리가 부분적임.** `gitops/resources`에서 ingress를 격리하는 정책은 `database`, Gitea, APISIX Admin
  API뿐이며 `monitoring`과 `platform-system`에는 없음(차트에 포함된 정책은 전수 확인하지 않음).
- **설치 경로에 따라 개발자 ArgoCD 역할이 다를 수 있음(unverified).** `13-argocd.sh`는 `tenants/*`에 `sync`를
  쓰지만 `gitops/charts/narwhal-platform/templates/argocd-config.yaml`의 `argocd-rbac-cm`은 여전히 `*/*`에
  `sync`를 선언함. 라이브 클러스터가 어느 쪽이 되는지는 확인되지 않음.
- **에어갭 번들 복사는 OCI referrer(SBOM, 서명, attestation)를 보존하지 않음.** `09`는 손실을 보고하지만
  실패시키지 않음(#124).
- **다이제스트 인벤토리는 최초 저장 시점 신뢰(trust-on-first-save)임.** `images.digests.json`에는 서명이 없으므로
  번들이 `02`가 기록한 것과 일치함은 증명하지만 `02`가 올바른 바이트를 보았음은 증명하지 않음(#124).
- **라이브 클러스터에 적용되지 않음.** AppProject 분리, 개발자 RBAC 변경, 비공개 Gitea 리포지토리, Valkey 교체의
  커밋 메시지는 실행 중인 narwhal 클러스터가 아니라 정적 검사 또는 임시 클러스터에서 확인했다고 명시함.

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
