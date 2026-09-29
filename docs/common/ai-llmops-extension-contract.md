# 선택적 AI/LLMOps Extension 경계 및 Lifecycle 계약 초안

이 문서는 narwhal#55의 설계 초안이다. 현재 저장소에 AI 전용 controller/API/CRD 구현이
없으므로, 아래 `PROPOSED` 항목은 구현 사실이 아니라 extension 도입 시 지켜야 할 계약이다.
Narwhal core는 Kubernetes 기반 자원·identity·policy·GitOps 경계를 제공하고 AI extension은
dataset/model/workflow 의미를 소유한다. KubeMetal의 로컬 AI runtime, assistant, ChatOps는
이 계약의 대상이 아니다.

## 저장소에서 확인한 기준

- `docs/common/versioned-management-api-contract.md`는 `/api/v1`을 향후 외부 계약 경로로
  예약하지만 live API는 없다고 명시한다. 따라서 이 문서는 endpoint URL을 정의하지 않는다.
- `gitops/charts/narwhal-apps/templates/tenants.yaml`은 tenant namespace를 승인된 Git PR 후
  ArgoCD가 생성·prune하는 구조다. `gitops/resources/tenants/`가 namespace별 리소스를 담는다.
- `gitops/resources/argocd-projects.yaml`의 `tenants` AppProject는 `dev` 및 `dev-*` 안의
  namespaced 리소스만 허용하고 cluster-scoped 리소스 whitelist는 비워둔다. ResourceQuota,
  LimitRange, Role, RoleBinding은 tenant가 수정하지 못하게 차단한다.
- `gitops/resources/rbac-policies.yaml`은 developer cluster 권한을 read-only로 두고
  `developer-workload-admin`을 namespace RoleBinding으로만 부여한다. `docs/common/oidc-rbac-contract.md`
  에서 Portal team mapping도 RBAC 권한 부여가 아닌 UI visibility scope로 정의한다.
- `gitops/resources/dev-namespace.yaml`은 CPU/memory/storage quota와 LimitRange를 정의하지만
  GPU 자원은 선언하지 않는다. `gitops/` 및 `scripts/` 검색에서도 GPU device plugin,
  training controller, AI dataset/model 리소스 구현을 찾지 못했다.
- `docs/common/supply-chain-policy.md`와 `docs/common/airgap-isolation-testing.md`는 digest 기반
  artifact 고정과 오프라인 검증을 다룬다. 이 초안은 AI 산출물에도 그 원칙을 연결하되
  SBOM/provenance 발급 기능이 이미 있다고 주장하지 않는다.

## Core와 extension의 소유권

| 계약 영역 | Narwhal core 소유 | AI extension 소유 |
| --- | --- | --- |
| 실행 자원 | namespace, quota/LimitRange, Kubernetes Job/Pod, GPU 자원 할당 결과, 공통 identity/RBAC/policy 집행 | 학습·추론 작업의 목적, 입력/출력 의미, framework/runtime 설정 |
| artifact | 저장소 접근 경계와 공통 digest/provenance 검증 인터페이스 | dataset/model 메타데이터, 버전 계보, annotation 및 RAG component reference |
| 배포 | workload 실행 및 네트워크/policy 경계 | model deploy 요청, endpoint revision/traffic 의미, health·usage 해석 |
| 운영 | GitOps source of truth, 감사 가능한 승인, 공통 operation/event envelope | AI workflow 상태와 extension 전용 status condition |

Extension은 core namespace/RBAC/quota를 우회하거나 ClusterRole, CRD, Namespace를 tenant
워크로드에서 생성하지 않는다. Core가 제공하지 않는 GPU resource class나 artifact 기능은
extension 선행조건으로 명시하고, 충족되지 않으면 fail-closed 한다.

## CRD 및 namespace 소유권

모든 kind와 API group은 `PROPOSED`; 이름은 예시일 뿐 등록된 API가 아니다.

| Proposed kind | Scope | 소유자 / 목적 |
| --- | --- | --- |
| `AIWorkspace` | namespaced | extension; team의 dataset/model 참조와 작업 기본값 묶음 |
| `Dataset` | namespaced | extension; dataset 논리 ID, 버전, annotation 상태, artifact 참조 |
| `Model` | namespaced | extension; 모델 논리 ID, plugin/config 참조, immutable version 참조 |
| `ModelDeployment` | namespaced | extension; 요청된 model version과 runtime/GPU 요구량, endpoint 연결 |
| `APIEndpoint` | namespaced | extension; 공개할 배포 revision, route 참조, health/usage evidence |
| Controller CRD 및 cluster 권한 | cluster-scoped | platform 운영자/GitOps만 설치·업그레이드·삭제 |

각 tenant namespace는 `gitops/resources/tenants/<team>/<namespace>.yaml` 패턴으로 승인 후
생성한다. Tenant CR은 같은 namespace에만 생성 가능해야 하며, cross-namespace 참조는 기본
거부한다. 공유 dataset/model이 필요하면 owner namespace의 명시적 share grant를 extension이
검증하고, 실제 artifact 읽기 권한도 동일 grant로 제한한다. 이 grant는 Kubernetes RBAC을
확장하지 않는다. `dev`/`dev-*`는 현재 AppProject 경계의 예이며, AI namespace 이름 정책은
`PROPOSED`이고 새 namespace prefix를 현재 지원한다고 간주하지 않는다.

## Metadata 계약 (필수 필드)

아래 schema는 `PROPOSED`이며 CRD/OpenAPI schema가 아직 없다. 공통 메타데이터는 모든 리소스에
적용한다.

| 필드 | 형식 / 제약 | 의미 |
| --- | --- | --- |
| `metadata.name` | Kubernetes DNS label | namespace 내 고유 이름 |
| `metadata.labels[ai.narwhal.io/tenant]` | non-empty stable ID | tenant/team 소유권. 생성 주체가 임의로 타 tenant 값 지정 불가 |
| `metadata.labels[ai.narwhal.io/project]` | non-empty stable ID | tenant 내부 project 경계 |
| `spec.owner` | `{tenant, project, subject}` 문자열 | 감사와 인가용 요청 소유자; 인증 토큰의 subject와 일치 확인 |
| `spec.artifact` | `{uri, digest, mediaType}` | 불변 artifact 식별자. digest는 `sha256:<64 lowercase hex>` |
| `status.observedGeneration` | integer | controller가 반영한 generation |
| `status.conditions[]` | `{type,status,reason,message,lastTransitionTime,observedGeneration}` | 현재 판정과 실패 사유 |

`Dataset.spec`은 `datasetId`, `version`, `format`, `schemaRef`, `artifact`, `annotation`을
포함한다. `annotation`은 `{state, schemeRef, completedAt}`이며 scheme/version은 참조로 고정한다.
버전은 생성 후 내용 변경이 금지되고, 변경은 새 version 생성으로 표현한다.

`Model.spec`은 `modelId`, `version`, `artifact`, `pluginRef`, `configurationRef`, `sbomRef`,
`provenanceRef`를 포함한다. `pluginRef`와 `configurationRef`는 버전 또는 digest로 고정한다.
모든 필수 참조가 준비되기 전에는 배포할 수 없다.

`AIWorkspace.spec`은 `datasetRefs[]`, `modelRefs[]`, `storageClaimRef`, `resourceProfileRef`를
포함한다. 리소스 참조는 `{namespace,name,version}`이며 namespace 생략은 자기 namespace로
해석한다. PVC/RWX 지원은 storage class 및 권한이 확인된 경우에만 가능하다.

`ModelDeployment.spec`은 `modelRef`, `runtimeRef`, `configurationRef`, `resources`를 포함한다.
`resources`는 CPU/memory 및 선택적 `nvidia.com/gpu` 정수 요청을 갖는다. NVIDIA GPU 존재,
device plugin 설치, GPU quota 지원은 현재 코드에서 확인되지 않으므로 GPU 작업은 해당 기능이
설치·정책 승인된 경우에만 허용한다.

`APIEndpoint.spec`은 `deploymentRef`, `revision`, `exposureRef`, `smokeTestRef`를 포함한다.
Host, URL path, 인증 방식, Gateway 종류는 배포 환경이 결정하므로 이 문서는 값을 발명하지
않는다. 외부 노출은 별도 승인된 exposure policy가 없으면 금지한다.

RAG 참조는 `rag.components[]`로 두며 각 항목은 `{role, artifactRef, version, digest}`이고
`role`은 `embedding`, `reranker`, `vectorStore` 중 하나다. 외부 서비스 연결 credential은 CR
본문에 넣지 않고 승인된 Secret 참조만 허용한다.

## Lifecycle 및 실패 계약

각 리소스의 controller는 `status.observedGeneration`을 갱신하기 전까지 Ready로 보고해서는
안 된다. 표의 transition은 `PROPOSED`다.

| 대상 | 상태 순서 | 실패 및 복구 |
| --- | --- | --- |
| Dataset | `Pending → Uploading → Validating → Available → Retiring → Deleted` | 업로드 중단/형식 불일치/digest mismatch는 `Failed` condition과 reason 기록. 원본 버전은 수정하지 않고 재시도는 새 upload operation 또는 새 version으로 수행 |
| Model version | `Draft → Validating → Ready → Deprecated → Retired` | digest/SBOM/provenance 누락 또는 plugin/config 미해결은 Ready 금지. Published version 변경 금지; 수정은 새 version |
| AIWorkspace | `Pending → Provisioning → Ready → Deleting → Deleted` | PVC/quota/access 검증 실패는 `Failed`; dataset/model의 실제 소유권 확인 전 Ready 금지. 삭제 시 연결 Job 종료/증거 보존을 확인 |
| ModelDeployment | `Pending → Preflight → Scheduling → Starting → Serving → Degraded/Failed → Stopping → Stopped` | quota 부족, GPU 미가용, 이미지/모델 digest 미일치, policy 거부는 시작 전 실패. 재시도는 idempotency/correlation을 유지하는 새 operation |
| APIEndpoint | `Pending → Publishing → Ready → Unhealthy → RollingBack → Ready/Failed → Retiring → Retired` | route publish 실패/health probe 실패 시 트래픽 Ready 판정 금지. rollback은 직전 정상 immutable revision으로만 가능하며 target 소실 시 `Failed` |

공통 terminal error condition의 `reason` 값은 `InvalidSpec`, `Unauthorized`, `PolicyDenied`,
`QuotaExceeded`, `ArtifactUnavailable`, `DigestMismatch`, `GPUUnavailable`, `PreflightFailed`,
`DependencyNotReady`, `HealthCheckFailed`, `ReconcileError` 중 하나로 제한한다. 상세 오류는 민감정보
없이 `message`에 기록하고, secret 값·token·prompt/data 본문은 status/event/log에 기록하지 않는다.
명시적 삭제는 비동기 operation이며 operation contract의 `operation_id`, `correlation_id`,
최종 `operation.completed|failed` 규칙을 따른다. CRD 삭제/cluster-wide prune으로 tenant data를
암묵 삭제하지 않는다.

## 인가·정책 hook 순서

Extension은 요청마다 다음 검사를 순서대로 통과시킨다. 하나라도 불명확하거나 실패하면 거부한다.

1. 인증 subject 및 역할을 확인한다. `developer` 같은 cluster 역할은 tenant 소유권을 대신하지 않는다.
2. 요청 namespace, `spec.owner.tenant/project`, tenant/project membership이 일치하는지 확인한다.
   Portal의 team visibility만으로 API/스토리지 권한을 부여하지 않는다.
3. 참조된 Dataset/Model/RAG artifact의 namespace 및 share grant, version, digest를 검사한다.
4. `ResourceQuota`/`LimitRange`, node resource availability, GPU quota/resource profile, storage
   quota, admission/network/security policy를 실행 전에 검증한다.
5. dataset upload/annotation 변경, model publish, GPU Job 생성, endpoint exposure, rollback은
   actor, reason, correlation ID와 판정 결과를 감사 evidence에 남긴다. GitOps namespace 승인은
   현재 패턴을 유지한다.

정책 거부는 403 의미의 `Unauthorized` 또는 `PolicyDenied`, 잘못된 참조/필드는 `InvalidSpec`,
용량 부족은 `QuotaExceeded`, 사전 실행 smoke test 실패는 `PreflightFailed`로 표준화한다.
실제 HTTP status와 API route는 구현 시 `/api/v1` 계약을 별도 갱신한 뒤에만 정한다.

## Job preflight, health 및 evidence

Job 생성 전 preflight는 최소한 (1) tenant ownership, (2) 참조 version/digest 및 artifact 접근,
(3) schema/config/plugin/RAG 의존성, (4) quota·GPU·storage 가용성, (5) admission/policy 판정,
(6) 격리된 smoke test 결과를 확인한다. 모든 항목이 `pass`여야 실제 workload를 제출한다.
Smoke test는 운영 endpoint에 트래픽을 보내지 않으며, 테스트 입력·결과 원문 대신 digest와
상태를 보존한다. 실패하면 Job을 생성하지 않고 어떤 검사에서 거부됐는지 기록한다.

Endpoint `Ready`는 선택된 immutable deployment revision이 배포되어 readiness probe 및 smoke
test가 통과한 경우에만 성립한다. `usage` evidence는 기간, 요청 수, 성공/실패 수, 지연시간
집계, source/observed time을 기록한다. 현재 저장소에는 AI endpoint health/usage 수집기가 없으므로
metrics 이름·저장소·수집 주기는 구현 선택 사항이다.

모든 dataset/model/runtime image에 가능한 경우 digest, SBOM, provenance 참조를 기록하고
artifact를 재검증한다. 저장소의 `docs/common/supply-chain-policy.md`는 image pin을 tag가 아닌
digest로 다루며, `docs/common/airgap-isolation-testing.md`는 격리 상태 반입 검증을 다룬다.
AI artifact/model air-gap 반입은 해당 검증 절차에 연결하되, 미검증 artifact는 `Available` 또는
`Ready`가 될 수 없다. 상세 bundle format은 별도 계약 사항이다.

## Acceptance trace 및 범위 밖

이 초안으로 합의할 검증 시나리오는 최소 두 tenant에서 각각 dataset 생성/공유 → immutable
model version 등록 → preflight → GPU Job 배정(실제 GPU 환경에서) → versioned endpoint Ready →
health/smoke evidence → 이전 정상 revision rollback까지 owner와 digest를 추적하는 것이다.
CPU-only 환경은 기존 `dev` quota/LimitRange 및 tenant AppProject 동작이 그대로 유지되는지
회귀 검증해야 한다. 현재 저장소만으로는 GPU 기반 실행, AI CRD, API, 2 tenant end-to-end를
입증할 수 없으며 이는 구현/별도 runtime 검증에 남는다.

범위 밖: 실제 CRD group/version·API URL·controller, storage schema, GPU vendor/plugin 설치,
model-serving framework 선택, ingress hostname/auth format, metrics backend/retention, RFP 원문
요건의 적합성 판단. #45 air-gap lifecycle과 artifact format을 합치는 결정도 별도 설계가 필요하다.
