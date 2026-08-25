# Narwhal Platform Test Strategy (T1-T7)

> narwhal#50 "[P0][Quality][Testing] Platform Test Strategy / Offline E2E / Failure
> Injection / Upgrade Validation"의 1차 구현. 흩어진 테스트 자산
> (`scripts/test/regression-check-kakao.sh`, `tests/chaos/`, `tests/k6/`,
> `docs/common/airgap-isolation-testing.md`, `docs/evaluation/`)을 이슈가 정의한
> T1-T7 계층으로 매핑하고, 어디가 비어 있는지 숨기지 않고 적는다.
>
> **이 문서를 쓴 시점 상태**: Kakao Cloud 클러스터는 destroy된 상태다(2026-08-10).
> 그래서 이 패스에서 실제로 만들 수 있었던 것은 정적 분석 · 문서 · CI 도구 · 리포트
> 스캐폴딩뿐이다. T3/T4의 런타임 절반, T5의 라이브 실행, T6의 라이브 업그레이드는
> **전부 라이브 클러스터가 있어야만 검증 가능**하며, 이 문서는 그것들을 "됐다"고
> 주장하지 않는다 — 마지막으로 실행된 시점과 그 이후 재검증 여부를 각 항목에 명시한다.

## 1. T1-T7 계층 정의

이슈 본문 그대로, 요약만 덧붙인다.

| 계층 | 정의 | 이 저장소에서의 의미 |
|------|------|----------------------|
| **T1** Unit/Contract | parser, manifest, policy, API schema, version/compat rules, artifact metadata, webhook/event schema | 파일 하나(또는 두 파일의 정적 diff)를 라이브 컴포넌트 없이 검사 |
| **T2** Component/Integration | K8s API, ArgoCD, Harbor/Registry, Storage, Observability, Keycloak/OpenBao/Kyverno/Cilium 등 개별 컴포넌트를 단독 기동해 검증 | Keycloak 파일럿 구현됨 — offline desired-state 계약 + 로컬 컨테이너 OIDC 계약, §3.2 참조 |
| **T3** Platform E2E | cluster bootstrap → validation → GitOps → workload → monitoring → backup, 전체 스택 라이브 검증 | 라이브 클러스터 필수 |
| **T4** Offline E2E | 외부 연결 차단 상태에서 artifact/DB 반입, image/Helm/OCI/package import, 모든 runtime egress 차단 검증 | 정적 절반(번들 완전성)은 커버, 라이브 절반(실제 격리)은 별도 도구 존재하나 실행 필요 |
| **T5** Failure/Chaos | node/pod/control-plane/component 장애, network/DNS/LB/registry 아웃티지, storage 장애, backup/restore 실패, upgrade 실패/rollback, alert storm/notification 실패 | Chaos Mesh 실험 6+1종 존재(2026-07-17 실행), 다수 하위 시나리오 미구현 |
| **T6** Upgrade/Compatibility | supported version matrix 자동 검증, preflight, canary/staged upgrade, zero/minimal disruption, regression/smoke, rollback, air-gap bundle reproducibility | version-check.yml이 5개 컴포넌트만 부분 커버, 나머지 전부 없음 |
| **T7** AI/LLM Validation | RCA evidence completeness, hallucination 탐지, citation/evidence grounding, confidence, tool-call authorization, reproducibility, destructive action 승인 게이트 | **이 저장소에 없음** — 검증 대상 LLM 기능 자체가 아직 없음 |

## 2. 계층별 커버리지 총괄

| 계층 | 상태 | 근거 |
|------|------|------|
| T1 | **양호** — 62개 정적 체크 자동화, CI 게이트(`lint.yml` → `regression-static`) | §3.1 |
| T2 | **파일럿 구현됨** — Keycloak render 계약은 정적 CI 게이트, 로컬 컨테이너 runtime은 이미지가 사전 적재된 호스트에서 실행 | §3.2 |
| T3 | **설계·구현됨, 실행 차단** — 클러스터 destroy 이후 미실행 | §3.3 |
| T4 | **절반**: 정적/오프라인-반입 검증은 T1 체크로 이미 존재, 라이브 격리 검증(`verify-isolation.sh`)은 실행 차단 | §3.4 |
| T5 | **부분** — 6~7개 chaos 실험 존재·과거 실행됨(재검증 차단), catalog 절반 이상은 미구현 시나리오 | `docs/common/failure-injection-catalog.md` |
| T6 | **부분** — 버전 일치 5개만, matrix-of-combinations·업그레이드 회귀·rollback evidence 전무 | §3.6 |
| T7 | **커버리지 0** — 검증 대상 LLM 기능이 이 저장소에 아직 없음 | §3.7 |

### 3.1 T1 — 이 저장소가 실제로 갖고 있는 것

`scripts/test/regression-check-kakao.sh --static`의 R-체크 **전부**가 T1이다. 이유:
어느 것도 kubectl/argocd/docker/레지스트리 등 살아있는 컴포넌트를 호출하지 않는다 —
전부 리포지토리 안의 파일(스크립트, YAML, TSV)에 대한 grep/AST/구조 검사다. 정확히
T1의 정의("parser, manifest, policy, ... artifact metadata validation")와 일치한다.

같은 이유로 `.github/workflows/lint.yml`의 `yaml-validate`(kubeconform), `markdown-lint`
행 형식 검사, `version-check.yml`의 5개 버전 비교도 T1이다.

R88/R89도 예외가 아니다. T2 adapter의 `--mode render`를 호출하지만 Helm output과
catalog/YAML 파일을 검사하는 **T1 static preflight**다. R88은 실제 chart, R89는 yq-mutated
temp copy를 검증한다; 살아 있는 Keycloak process를 기동하는 `--mode runtime`만 T2 behavior다.

**전체 R-체크 매핑** (2026-08-25 기준, `regression-check-kakao.sh` 945줄):

일부 체크는 정적 검사(T1)이면서 **주제가 오프라인/에어갭**인 것들이 있다 — "T4 관련"
열로 표시했다. 메커니즘은 T1(라이브 없이 실행)이지만, 무엇을 검증하는지는 T4의
관심사(반입 번들 완전성, 격리 상태를 흉내)와 겹친다는 뜻이다.

| ID | 검증 대상 | T4 관련 |
|----|-----------|:---:|
| R01 | containerd 1.7 하드 핀 부재 | |
| R02 | nfs-common 설치 | |
| R03 | ip_forward + br_netfilter 설정 | |
| R04 | cloud 스크립트에 `sudo -E` 없음 | |
| R05 | 미러 호스트 경로가 `/v2/<upstream>` | ✓ |
| R06 | bare ref 정규화 | ✓ |
| R07 | 이미지 리스트가 kubeadm 리스트를 union | ✓ |
| R08 | 이미지 tar 쓰기 전 기존 파일 제거 | ✓ |
| R09 | `stage-kakao-nodes`가 COPYFILE_DISABLE 설정 | |
| R10 | patch-apiserver-memory 절대경로 호출 | |
| R11 | patch-apiserver-memory가 /livez 폴링 | |
| R12 | kakao 분기가 CoreDNS 존을 씀 | |
| R13 | LB 모듈이 compute에 depends_on | |
| R14 | cloud 스크립트에 192.168.56 하드코딩 없음 | |
| R15 | cloud/airgap 스크립트 전부 `set -euo pipefail` | |
| R16 | 프로비저닝 스크립트 전부 파싱됨(`bash -n`) | |
| R17 | metallb 템플릿이 provider로 게이트됨 | |
| R18 | `bastion.tf`가 git 추적됨 | |
| R19 | 번들 카운트(images.txt/manifest/oci)가 일치 | ✓ |
| R20 | bootstrap registry.tar가 이미지보다 최신 | ✓ |
| R21 | 01-prerequisites가 yq 설치 | |
| R22 | 매니페스트 도구 전부 scripts/에서 설치됨 | |
| R23 | cloud 스크립트에 local.narwhal.internal 하드코딩 없음 | |
| R24 | 베스천이 도메인 split DNS를 서빙 | |
| R25 | 01-prerequisites가 노드 리졸버를 DNS_SERVER로 지정 | |
| R26 | /etc/hosts 서비스명 나열 방식 부재 | |
| R27 | 공개 다운로드가 retry()로 감싸짐 | ✓ |
| R28 | 설치 경로에 `helm repo add` 없음 | ✓ |
| R28b | `helm pull/fetch` 없음(번들에서만) | ✓ |
| R29 | 기본 설치 경로에 인터넷 fetch 없음 | ✓ |
| R30 | 모든 `ctr … pull`이 `--hosts-dir` 사용 | ✓ |
| R31 | provision-kakao.sh가 AIRGAP을 노드에 전달 | ✓ |
| R32 | stage-kakao-nodes.sh가 apt 번들을 스테이징 | ✓ |
| R33 | 양쪽 번들이 bin/manifests/.deb 보유 | ✓ |
| R34 | GitOps 레이어에 공개 repoURL 없음 | ✓ |
| R35 | 추적된 파일이 .gitignore와 겹치지 않음 | |
| R37 | 클러스터-와이드 developer 롤에 exec/portforward/attach 없음 | |
| R38 | developer-workload-admin에 ClusterRoleBinding 없음 | |
| R39 | APISIX가 gitea bypass 라우트에서 X-WEBAUTH-USER 제거 | |
| R40 | gitea가 모든 리버스 프록시를 신뢰하지 않음 | |
| R41 | narwhal-gitops가 public으로 생성되지 않음 | |
| R42 | bootstrap이 ArgoCD repo credential을 등록 | |
| R43 | main 브랜치 보호가 적용됨 | |
| R44 | Application이 default AppProject를 쓰지 않음 | |
| R45 | developer sync가 클러스터-와이드가 아님 | |
| R46 | 모든 Application이 자기 AppProject 정책에 맞음 | |
| R47 | GitOps git 연산에 스왈로된 실패 없음 | |
| R48 | idp-apps가 source validation에 게이트됨 | |
| R49 | 언쿼티드 heredoc 안에 백틱 없음 | |
| R50 | 빌드 경로에 mutable `latest` 다운로드 없음 | |
| R51 | 빌드 경로에 mutable git ref tarball 없음 | |
| R52 | 언핀드 글로벌 npm install 없음 | |
| R53 | 에어갭 다운로드가 체크섬 검증됨 | ✓ |
| R54 | 체크섬 행 누락 시 fetch 실패 | ✓ |
| R55 | OIDC RBAC ↔ portal ALLOWED_GROUPS 계약 일치(+drift 탐지) | |
| R56 | gitea-http NetworkPolicy가 실제 호출자만 허용 | |
| R57 | 위 정책에서 APISIX 규칙 제거 시 탐지 | |
| R58 | image-list 생성이 hook 이미지 누락 시 hard-fail | ✓ |
| R59 | 위 hard-fail이 회귀 시 탐지 | ✓ |
| R60 | `09-verify-bundle-completeness.sh` 존재(번들 1:1 게이트) | ✓ |
| R61 | tuning Job NetworkPolicy가 ingress+egress 전부 거부 | |
| R62 | 위 정책에서 egress 규칙 재추가 시 탐지 | |
| R63 | component-licenses.tsv에 금지/공백 라이선스 없음 | |
| R64 | 위에서 금지 라이선스 재추가 시 탐지 | |
| R65 | gitops/에 mutable(`:latest`/untagged) 이미지 참조 없음 | |
| R66 | 위에서 `:latest` 재추가 시 탐지 | |

R36은 스크립트에 존재하지 않는다 — 번호가 R35 다음 R37로 건너뛴다. 언제·왜 빠졌는지
git blame/PR 이력을 확인하지 않았으므로 추측으로 채우지 않는다; 재사용 가능한
번호이므로 다음 신규 체크가 R36을 쓰면 안 된다는 점만 기록해 둔다.

**요약**: R01-R89 정적 체크는 전부 T1. 이 저장소의 정적 회귀 스위트는 이미 T1을 잘 하고 있다 —
문제는 T1 다음이 통째로 비어 있다는 것.

### 3.2 T2 — Keycloak 파일럿 구현됨 (라이브 클러스터 대체 아님)

재사용 가능한 진입점은 `scripts/test/t2-component.sh <component> --mode render|runtime|all`이고,
컴포넌트별 어댑터는 `scripts/test/t2/`에 등록한다. 첫 어댑터
`scripts/test/t2/keycloak.sh`는 두 경계를 분리한다.

- `keycloak --mode render`는 실제 `gitops/charts/narwhal-platform`을 Helm으로 렌더하고,
  Keycloak v2alpha1 CR의 `iam` namespace, hostname, DB Secret 참조, first-class CPU request,
  probe, Istio ambient opt-out 및 theme mount를 검증한다. 또한
  `tests/chaos/experiments/keycloak-kill.yaml`의 `iam` + `app: keycloak` selector와
  `failure-injection-catalog.md`의 Keycloak T2 링크를 확인한다. 이는 **T1 static preflight**이며,
  R88은 실제 chart PASS, R89는 temp-copy에서 CPU request를 `yq`로 제거해 반드시 FAIL하는지
  확인한다.
- `keycloak --mode runtime`은 pull하지 않고 로컬에 사전 적재된
  `quay.io/keycloak/keycloak:26.5.7`만 사용해 `start-dev` 컨테이너를 기동한다. 매 실행마다
  ephemeral realm/client/user/password를 만들고 groups 및 audience mapper를 설정한 뒤,
  password grant access token의 bare `groups`와 client `aud`를 검증한다.

실행 예: `scripts/test/t2-component.sh keycloak --mode render`; 적재된 이미지가 있는
호스트에서는 `scripts/test/t2-component.sh keycloak --mode runtime`. render는 **T1 offline
desired-state preflight**이며 CRD apply, Operator/PostgreSQL/Kubernetes/Istio 기동 또는 Chaos
Mesh 실행을 증명하지 않는다. runtime이 Docker default bridge에서 Keycloak API/token을 실제로
기동하는 T2 behavior이며, 네트워크 격리·Operator·CNPG·live cluster를 검증하지 않는다. 즉
전체 플랫폼 T3와 동등하지 않고, 다른 컴포넌트와 live-cluster 통합은 여전히 남아 있다.

### 3.3 T3 — 설계·구현됨, 실행 차단

`docs/evaluation/README.md`의 "2단계 — 기능 스모크"가 정확히 T3다:
`verify-cluster.sh`(노드/VIP/etcd/CNI/DB/APISIX/TLS/DNS), `test-sso.sh`(OIDC 플로우),
`verify-backup.sh`(Velero). `regression-check-kakao.sh --runtime`의 T01-T13도 T3
(T14/T15은 §3.4로 분류 — 에어갭 관심사이기 때문).

| ID | 검증 대상 |
|----|-----------|
| T01 | 6/6 노드 Ready |
| T02 | containerd가 1.7.x가 아님 |
| T03 | ArgoCD Application 10개 이상 전부 Synced+Healthy |
| T04 | Terminating에 멈춘 파드 없음 |
| T05 | local.narwhal.internal에 남은 라우트 없음 |
| T06 | ApisixTls가 reconcile됨(generation 일치) |
| T07 | CoreDNS hairpin 존 존재 |
| T08 | keycloak.\<domain\>이 클러스터 내부에서 resolve됨 |
| T09 | hairpin 존이 wildcard(고정 리스트 아님) |
| T10 | APISIX admin `/routes`가 200(etcd 데드락 아님) |
| T11 | Gitea에 AppleDouble 파일 없음 |
| T12 | 모든 서비스 도메인이 worker LB로 도달 가능 |
| T13 | AAAA가 SERVFAIL이 아니라 NODATA |

**차단 사유**: Kakao 클러스터가 destroy된 상태(2026-08-10, 52/52 리소스 제거,
project memory 확인). 이 패스에서 `--runtime`을 실행하지 않았다 — 실행하면 즉시
"no reachable cluster"로 exit 1이거나, 잘못된 컨텍스트를 잡아 거짓 결과를 낼 위험이
있고(§KUBE_CONTEXT 가드가 있긴 하지만), 애초에 검증할 클러스터가 없다.

### 3.4 T4 — 절반

| 하위 검증 | 상태 | 실행 위치 |
|-----------|------|-----------|
| 번들 완전성(이미지 수, manifest, oci layout 일치) | **T1로 이미 자동화** | R19/R20/R33/R53/R54/R58-R60 |
| 이미지/Helm/apt 번들이 실제로 오프라인 경로에서만 온다는 정적 증거 | **T1로 이미 자동화** | R05-R08/R28-R32/R34 |
| 클러스터가 정말 인터넷과 끊겼는가(라이브) | **문서·도구 존재, 실행 차단** | `docs/common/airgap-isolation-testing.md` + `scripts/test/verify-isolation.sh` |
| 미러가 실제로 사용되는가(containerd config_path, registry 로그) | **문서·도구 존재, 실행 차단** | `regression-check-kakao.sh --runtime` T14/T15 |
| vulnerability DB(trivy-db) 오프라인 반입 | **없음 — 신규 발견 갭** | 아래 참조 |

`gitops/charts/narwhal-apps/templates/trivy-operator.yaml`이
`dbRepository: aquasecurity/trivy-db`를 `ghcr.io`에서 **런타임에** pull하도록 설정돼
있다(`scripts/airgap/01-generate-image-list.sh`는 trivy **바이너리** 이미지만
번들에 포함하고, DB 자체는 별도). 이슈의 핵심 시나리오 2번("vulnerability DB 반입 →
image scan → 결과 재현")을 위한 오프라인 DB 미러링/버저닝 경로가 이 저장소에
없다 — 에어갭 클러스터에서 trivy-operator를 그대로 두면 스캔이 실패하거나(네트워크
없음) 조용히 스킵될 것으로 추정되나, 확인하려면 라이브 클러스터가 필요하다.
**AC "security/vulnerability scan 결과에 DB version/digest 기록"이 미충족인 근본
원인이 이것**이다.

### 3.5 T5 — 부분

`docs/common/failure-injection-catalog.md` 참조. 요약: Chaos Mesh 기반 실험
6종(+smoke 1종)이 존재하고 2026-07-17에 전부 PASS로 실행된 기록이 있다(이전 트리아지가
"카탈로그 없음"이라고 한 것은 부정확하다 — `tests/chaos/RUNBOOK.md`를 못 본 것으로
보인다). 그러나 그 실행 이후 클러스터가 재생성·재destroy되어 **한 번도
재검증되지 않았고**, 이슈가 요구하는 하위 시나리오 중 노드 전체 장애, LB/registry
아웃티지, 스토리지(NFS/SeaweedFS) 장애, 진짜 backup/restore 실패 주입, upgrade
실패/rollback 주입, alert storm/notification 실패는 전혀 구현돼 있지 않다
(alertmanager의 Slack/Discord webhook 설정은 `gitops/resources/alertmanager-config.yaml`에
TODO로 주석 처리된 채로 남아 있다 — 확인 완료, 알림 채널 자체가 없다).

### 3.6 T6 — 부분

- `.github/workflows/version-check.yml`이 VERSIONS.md ↔ 실제 스크립트/차트 값을
  5개 컴포넌트(Kubernetes, Cilium, ArgoCD, Keycloak, Traefik chart)에 대해서만
  비교한다. VERSIONS.md에는 이보다 훨씬 많은 컴포넌트가 있다 — §4에서 정량화한다.
- 에어갭 번들 재현성(air-gap bundle reproducibility)은 R19/R20/R33/R58-R60이
  정적으로 이미 검증한다(§3.1).
- **없음**: canary/staged upgrade 자동화, preflight 게이트, zero/minimal-disruption
  판정, upgrade 후 rollback evidence 저장, version matrix의 "조합"(예: K8s
  1.35.5 + Cilium 1.19.4 + Istio 1.30.1가 같이 도는지) 자동 검증 — 지금은 컴포넌트별
  1:1 버전 비교만 있고 조합 호환성 개념 자체가 없다.

### 3.7 T7 — 커버리지 0

narwhal 저장소 자체에는 검증 대상이 되는 LLM/AI 기능이 아직 없다(이슈의 시나리오 4
"Local LLM 1차 RCA"는 목표 시나리오이지 구현된 기능이 아니다). T7의 8개 하위 항목
(RCA evidence completeness, hallucination 탐지, citation grounding, confidence,
tool-call authorization, reproducibility, destructive-action 승인 게이트)은 전부
"검증할 대상이 생기기 전까지는 만들 수 없는" 종류라서, 이번 패스는 여기에 아무것도
만들지 않았다 — 빈 티어를 채우는 척하는 스캐폴딩보다, 대상이 없다고 정직하게
적는 쪽을 택했다.

## 4. Compatibility Matrix ↔ CI 연결 상태

`VERSIONS.md`(210줄)는 다음 카테고리로 나뉜다: Core Components, Networking, Service
Mesh, Storage, Database, (그리고 그 아래 더 있음 — Observability/Security/Backup 등).
`version-check.yml`은 이 중 **5개 값**만 비교한다:

| VERSIONS.md 컴포넌트 | version-check.yml 커버 |
|----------------------|:---:|
| Kubernetes | ✓ |
| Cilium | ✓ |
| ArgoCD | ✓ |
| Keycloak | ✓ |
| Traefik (chart) | ✓ |
| APISIX / APISIX IC / etcd / MetalLB / kube-vip / Istio / ztunnel / csi-driver-nfs / SeaweedFS / CloudNative-PG / (그 외 Observability·Security·Backup 절) | ✗ |

**AC "compatibility matrix를 CI regression에 연결"은 부분 충족으로 표시한다** — 연결
자체는 2026-07-26부터 이미 있었지만(이번 패스에서 만든 것 아님), VERSIONS.md 전체를
덮지 않고, "조합" 개념도 없다. 전체 컴포넌트를 커버하도록 확장하는 것은 이 패스의
범위 밖으로 남긴다 — `version-check.yml`의 반복되는 5-블록 구조를 컴포넌트
리스트 기반 루프로 리팩터링하는 편이 한 컴포넌트씩 블록을 추가하는 것보다 나은데,
이는 그 자체로 별도 작업이다.

## 5. 표준 테스트 fixture 패턴 — mutated temp copy

이슈의 AC "standard test data/fixture 정의"에 대한 답은 **새로 만들지 않는 것**이다.
`regression-check-kakao.sh`는 이미 66개 체크 중 상당수(R46/R55/R57/R59/R62/R64/R66)에서
동일한 패턴을 쓰고 있고, 이것이 이 저장소의 표준이다:

1. **양성 케이스**: 실제 파일/저장소 상태에 대해 체크를 돌려 PASS를 확인한다.
2. **음성 케이스**: 실제 파일을 **절대 건드리지 않고**, `mktemp -d`로 만든 임시
   디렉터리에 파일을 복사한 뒤 그 사본만 변형(mutate)한다 — 지운 줄, 되돌린 수정,
   재추가한 금지 패턴.
3. 같은 체크 스크립트를 변형된 사본에 대해 돌려 **반드시 실패해야** 정상이다
   (`check_not`으로 뒤집어 표현).
4. 임시 디렉터리는 `rm -rf`로 정리한다 — 실패 경로에서도 정리되도록
   `mktemp`/변형/체크/정리를 한 블록 안에 순서대로 둔다(트랩까지는 안 쓴다;
   이 스위트의 각 블록이 독립적이라 스크립트 전체가 죽어도 다음 `run_static` 호출
   때 다시 mktemp되므로 상태가 누적되지 않는다).

구체적 예시(코드 위치 인용):
- `scripts/test/regression-check-kakao.sh:316-337`(R55) — `sed`로 portal의
  `auth.ts` 사본에서 그룹 하나를 지운 뒤, 계약 체크가 그 drift를 잡아내는지 확인.
- 같은 파일 `:352-373`(R57) — python으로 NetworkPolicy YAML 사본에서 APISIX
  규칙을 제거한 뒤 재확인.
- `:411-417`(R64), `:430-440`(R66), `:457-483`(R59) — 각각 TSV/YAML/셸 스크립트에
  동일 패턴.

**왜 이게 맞는 패턴인가**: "체크가 통과하는 것만 본 적 있다"는 "체크가 작동한다는
증거"가 아니다(2026-08-03 lessons-log 사건 — R21이 코드가 아니라 주석에 매치돼
회귀를 놓쳤던 사건이 바로 이 교훈의 출처다). 실제 파일을 mutate하면 커밋 전에는
원상복구를 깜빡할 위험이 있고, 원본이 항상 완전한 상태가 아닐 수도 있다(예: R19가
번들이 없을 때 warn으로 스킵하는 것처럼, 실행 환경마다 다름) — temp copy는 이
두 위험을 모두 없앤다.

이번 패스에서 이 패턴을 새로 쓴 곳은 없다(§7 참조 — JSON/MD export는 회귀 체크가
아니라 리포팅 기능이라 이 패턴이 요구하는 "회귀가 재발했을 때 잡아내야 하는 버그"가
없다); 대신 실행 검증(양성 케이스 + 인위적으로 주입한 실패 케이스)으로 스크립트가
실제로 작동함을 §7에서 보였다.

## 6. Compatibility 확장 후보 — 다음 세션

- `version-check.yml`을 5개 하드코딩 블록에서 `(VERSIONS.md 패턴, 파일:정규식)` 목록
  기반 루프로 리팩터링 — 새 컴포넌트 추가 비용을 "블록 하나 복붙"에서 "테이블 한 줄"로
  낮춘다.
- APISIX/Istio/csi-driver-nfs/CloudNative-PG 등 나머지 컴포넌트부터 우선순위로 추가.

## 7. 머신 판독 가능 테스트 리포트 (JSON / Markdown export)

`regression-check-kakao.sh`가 `--json-report <path>`와 `--md-report <path>`를
지원한다(narwhal#50, 이번 패스에서 추가). 기존 stdout 출력·종료 코드는 그대로다 —
플래그를 안 주면 동작이 전혀 바뀌지 않는다(하위 호환 확인 완료, §검증 참조).

```bash
scripts/test/regression-check-kakao.sh --static \
  --json-report /tmp/report.json --md-report /tmp/report.md
```

**JSON 스키마**:

```json
{
  "test_run_id": "20260825T035659Z-83339",
  "script": "scripts/test/regression-check-kakao.sh",
  "mode": "--static",
  "started_at": "2026-08-25T03:56:59Z",
  "ended_at": "2026-08-25T03:57:04Z",
  "git_commit": "7621d32ba20421cac7f644fe648db87a1406172c",
  "git_dirty": false,
  "summary": {"pass": 62, "fail": 0, "warn": 2, "total": 64},
  "checks": [
    {"id": "R01", "status": "PASS", "timestamp": "2026-08-25T03:56:59Z",
     "description": "no hard containerd 1.7 pin (2026-07-26)"}
  ]
}
```

**이슈의 "공통 증적" 목록과의 대응**:

| 이슈가 요구하는 필드 | 이번 패스 | 사유 |
|-----------------------|:---:|------|
| test run id | ✓ | `RUN_ID`(타임스탬프+PID) |
| PASS/FAIL + reason | ✓ | `status` + `description`(reason이 description에 이미 녹아 있음 — R-체크들의 description 문구 자체가 실패 이유를 말한다) |
| 타임스탬프 | ✓ | 체크별 UTC ISO8601 |
| input/config hash | ✓(대용) | `git_commit` + `git_dirty` — 진짜 "config hash"는 아니지만 이 저장소가 정직하게 말할 수 있는 가장 가까운 것 |
| platform/component versions(실제 구동 버전) | ✗ | 라이브 클러스터 필요 |
| artifact/DB/image/chart digest | ✗ | 라이브 스캔/레지스트리 필요 |
| cluster/environment metadata | ✗ | 라이브 클러스터 필요 |
| logs/metrics/events/traces 참조 | ✗ | 라이브 Loki/Grafana 필요 |
| remediation link | ✗(부분 대체) | Markdown 리포트의 각 행이 `docs/common/lessons-log.md`의 날짜와 사람이 대조 가능하나, 자동 링크는 아님 |
| rollback result | ✗ | T6 라이브 업그레이드 필요 |

비어 있는 필드를 채우는 척(예: `"cluster_metadata": null`을 항상 넣는 것)은
일부러 하지 않았다 — 필드가 "항상 null"이면 리포트를 소비하는 쪽이 null과
"확인 안 됨"을 구분 못 하게 되고, 그게 이 이슈가 경계하는 종류의 가짜 완성이다.

**검증 절차와 결과** (2026-08-25, 이 worktree에서 실행):

```
$ ./scripts/test/regression-check-kakao.sh --static \
    --json-report /tmp/report.json --md-report /tmp/report.md
...
== RESULT
  passed 62   failed 0   warnings 2
  no known bug reappeared
  JSON report: /tmp/report.json
  Markdown report: /tmp/report.md
$ echo $?
0
```

- `python3 -c "import json; json.load(open('/tmp/report.json'))"` — 파싱 성공,
  `summary.total == len(checks) == 64`, `summary.pass+fail+warn == total`.
- 하위 호환: `--json-report`/`--md-report` 없이 실행 → 기존과 동일한 exit 0,
  기존과 동일한 stdout(리포트 저장 안내 줄만 없음).
- **음성 케이스**: `scripts/common/02-containerd.sh`의 임시 사본(실제로는 원본에
  줄을 추가했다가 즉시 원복 — 아래 diff로 확인)에 `containerd=1.7.99`를 주입해
  R01을 의도적으로 깨뜨린 뒤 재실행 → `exit 1`, JSON의 `summary.fail == 1`,
  `checks`에서 `id=="R01"`인 항목의 `status=="FAIL"` 확인. 원본 파일은
  `git status --short scripts/common/02-containerd.sh`가 빈 출력을 낼 때까지
  원상복구했다(diff 없음 확인 완료).

## 8. Acceptance Criteria 현황

이슈 원문의 11개 항목. **여기 표시한 상태 외의 주장은 하지 않는다** — "확인 안 함"과
"됐음"을 섞지 않는다.

| # | AC | 상태 | 비고 |
|---|----|------|------|
| 1 | 모든 P0/P1 issue에 test plan 연결 | ❌ 미착수 | 별도의 대규모 감사 작업, 이번 범위 밖 |
| 2 | offline E2E test environment 정의 | 🟡 부분 | `docs/common/airgap-isolation-testing.md`가 이미 정의(이번 패스 이전부터), 실행은 라이브 클러스터 필요 |
| 3 | standard test data/fixture 정의 | ✅ 이번 패스 | §5 — 기존 mutated-temp-copy 패턴을 표준으로 명문화 |
| 4 | failure injection scenario catalog 정의 | ✅ 이번 패스 | `docs/common/failure-injection-catalog.md` |
| 5 | compatibility matrix를 CI regression에 연결 | 🟡 부분(기존) | §4 — 5/다수 컴포넌트만, 이번 패스에서 확장 안 함 |
| 6 | upgrade 결과·rollback evidence 자동 저장 | ❌ 차단 | 라이브 클러스터 + 신규 자동화 필요 |
| 7 | vulnerability scan 결과에 DB version/digest 기록 | ❌ 신규 갭 발견 | §3.4 — trivy-db가 오프라인 미러링 안 됨 |
| 8 | incident/RCA에 evidence completeness check | ❌ 미착수 | RCA 자동화 자체가 없음(T7 선행 필요) |
| 9 | LLM 기능에 validator·approval gate | ❌ 대상 없음 | §3.7 |
| 10 | test report를 JSON/Markdown export | ✅ 이번 패스 | §7 |
| 11 | release gate: P0 100% PASS, P1 critical path PASS | ❌ 미착수 | CI가 R-체크 전체 통과는 이미 게이트하지만(`regression-static`), P0/P1 티어 구분이나 "critical path"라는 개념 자체가 없음 |

**3/11 이번 패스에서 새로 충족, 2/11은 기존에 이미 부분 충족 상태였음을 재확인,
6/11은 여전히 미착수/차단.** 이슈는 본래 다주(multi-week) 스코프로 설계됐고, 이
패스는 그 중 라이브 클러스터 없이 진행 가능한 조각만 처리했다.

## 9. 남은 작업

우선순위 제안(다음 세션 또는 담당자용, 순서는 의존성 기준이지 중요도 기준이 아님):

1. **T2 확장** — Keycloak 파일럿(`scripts/test/t2-component.sh`,
   `scripts/test/t2/keycloak.sh`) 다음으로 다른 component adapter 및 live-cluster 연동을
   추가한다. 현재 Keycloak render/runtime은 T3 대체가 아니다.
2. **trivy-db 오프라인 반입 경로 설계** — AC 7의 근본 원인. `scripts/airgap/`에
   DB 이미지/번들을 추가하고 `dbRepository`를 미러 경유로 바꿔야 한다.
3. **클러스터 재기동 후**: T3/T4 런타임 절반, T5 chaos 재검증(6개 실험 재실행 +
   미구현 시나리오 우선순위 상위 3개 구현), T6 최소 1회 실제 canary 업그레이드
   리허설.
4. **version-check.yml 리팩터링**(§6) 후 VERSIONS.md 전체 컴포넌트로 확장.
5. **P0/P1 test plan 링크 감사**(AC 1) — 이슈 트래커를 순회하는 별도 세션.
