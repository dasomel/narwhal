# Multi-Cluster Management Plane HA 및 복구 계약 (제안)

- 상태: 설계 계약, 구현·라이브 검증 전
- 범위: Narwhal management API와 fleet controller의 가용성, operation/event 내구성, member cluster 단절 시 동작 및 복구 순서
- 현재 근거: [`multicluster-control-plane.md`](multicluster-control-plane.md), [`versioned-management-api-contract.md`](versioned-management-api-contract.md), [`failure-injection-catalog.md`](failure-injection-catalog.md)
- 경계: 이 문서의 `PROPOSED` 항목은 현재 API, database, queue, controller 또는 HA 배포가 존재한다는 뜻이 아니다.

## 현재 구현과 설계 입력

`docs/common/multicluster-control-plane.md`의 실제 기능은 Portal용 cluster credential export 및 operator 수동 등록 절차다. 같은 문서는 Kakao cluster가 destroy되어 live registration/health aggregation/E2E가 없고, 전체 bootstrap의 fleet parameterization과 multi-cluster runtime이 미구현임을 밝힌다. `gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`의 Portal Deployment 및 `narwhal-portal-valkey` Deployment는 각각 `replicas: 1`이다. 이 배포값은 HA control plane을 제공하지 않는다.

`docs/common/versioned-management-api-contract.md`와 `schemas/event-envelope-1.0.schema.json`은 async operation의 `operation_id`, `correlation_id`, `request_id`, terminal event, `idempotency_key`/`source_event_id`를 정의하지만 operation persistence, live API, durable webhook queue는 open work로 명시한다. 아래 저장·ownership·복구 규칙은 이 식별자를 이어받는 **PROPOSED** 설계다.

Workload continuity는 별도 data-plane 문제로 취급한다. `docs/common/failure-injection-catalog.md`의 `istiod-kill`은 istiod 장애 중 ztunnel 통신 유지 실험이 2 replica 구성에서 PASS한 기록(2026-07-17)이지만, fleet manager outage 실험은 아니다. [`apisix-etcd-recovery.md`](apisix-etcd-recovery.md)는 APISIX gateway가 살아 있는 동안 in-memory route cache로 트래픽을 계속 처리할 수 있으나 gateway 재시작과 빈 etcd prefix가 겹치면 route가 사라질 수 있다고 기록한다. 따라서 management plane 장애만으로 member workload를 drain/withdraw하지 않으며, 각 workload data plane의 자체 상태를 별도로 확인한다.

## 제안 topology와 durable state 경계

**PROPOSED topology:** management API는 stateless replica 3개 이상으로 두고, controller worker도 복수 replica로 둔다. 세 개라는 수는 issue의 3-node drill을 위한 시험 프로파일이지 현재 배포 요구값이 아니다. 모든 API/controller replica는 아래 HA state store를 공유한다. active non-idempotent executor는 fencing 가능한 단일 lease owner만 허용한다. Kubernetes control plane의 etcd는 이 제품 state store로 간주하지 않는다.

| 데이터 | 권위 저장소 및 보존 규칙 (PROPOSED) | 재생성 가능 여부 |
|---|---|---|
| Operation record와 append-only transition history | HA relational state store. `operation_id` primary key; 상태·revision의 조건부 갱신 | member actual state와 event log로 일부 복구 가능하나 자동 재생성 금지 |
| Event inbox/outbox와 delivery attempt | 같은 트랜잭션 경계의 durable table. `event_id` unique, producer dedupe key unique 범위 적용 | 원본 producer가 보유한 event만 재전송 가능; accepted inbox는 삭제/재수락 금지 |
| Desired intent 및 action checkpoint | operation record에 immutable request hash와 step별 checkpoint 저장 | 요청자가 같은 idempotency key로 재요청할 때 기존 operation 반환 |
| Lease/owner epoch | state store의 원자적 CAS record. `owner_id`, monotonically increasing `fence_epoch`, `expires_at` | owner 재시작 시 lease 만료 후 획득; epoch 재사용 금지 |
| Evidence linkage | #42 envelope의 operation/correlation/event ID 연결. 보존 저장소에 append-only export | trace는 재구성될 수 없으며 correlation 및 evidence ID는 유지 |

Operation record의 최소 필드는 `operation_id`, `operation_type`, `cluster_id[]`, `actor`, `request_id`, `correlation_id`, `idempotency_key`, `request_hash`, `state`, `revision`, `created_at`, `updated_at`, `lease_epoch`, `current_step`, `checkpoint`, `last_error_code`, `evidence_id[]`다. credential 원문, kubeconfig, token, private key를 state/event/evidence에 기록하지 않는다. 요청 payload 중 재실행에 필요한 비밀 참조는 외부 secret reference만 저장한다.

## Operation 상태와 실행 gate

아래 machine state와 전이는 **PROPOSED**다. 모든 전이는 이전 `revision`을 조건으로 한 원자적 compare-and-set이며, transition event/outbox 작성과 operation 갱신은 한 commit에서 성공하거나 둘 다 실패한다.

| 상태 | 의미 및 허용 후속 상태 |
|---|---|
| `Accepted` | 요청 검증 및 중복 검사 완료, 실행 전. → `Queued`, `Rejected` |
| `Queued` | dependency 및 concurrency gate 대기. → `Running`, `Paused`, `Failed` |
| `Running` | 유효한 owner epoch가 step을 실행 중. → `Running`(checkpoint), `Paused`, `Reconciling`, `Succeeded`, `Failed`, `NeedsOperator` |
| `Paused` | 의존성/정책/취소 gate로 실행 정지. → `Queued`, `Reconciling`, `Cancelled`, `NeedsOperator` |
| `Reconciling` | 저장 checkpoint와 member actual state 비교 중. → `Queued`, `Paused`, `Succeeded`, `Failed`, `NeedsOperator` |
| `Succeeded`, `Failed`, `Cancelled`, `Rejected` | terminal; 같은 operation을 재개하지 않음. 수정 요청은 새 key와 새 operation 사용 |
| `NeedsOperator` | 결과 불명 또는 안전한 자동 판정 불가. 승인된 recovery decision을 기록한 뒤 → `Paused` 또는 새 operation |

`Accepted/Queued`의 동일 `idempotency_key` + 동일 `request_hash` 요청은 기존 `operation_id`와 현재 상태를 반환한다. 동일 key에 다른 hash면 `409 IdempotencyKeyConflict`다. 키 없이 받은 신규 destructive 요청은 `400 IdempotencyKeyRequired`로 거부한다. stale revision은 `409 OperationRevisionConflict`; state store/lease unavailable은 mutation에 `503 DependencyUnavailable`을 반환하며 성공으로 기록하지 않는다. API가 요청을 접수했다고 응답하기 전 operation과 outbox commit이 끝나야 한다.

Executor는 모든 non-idempotent step 직전 lease 유효성, 현재 `fence_epoch`, operation revision, 이전 checkpoint를 재검증한다. lease 상실·만료·store 분할 감지 즉시 새 step 시작을 중단한다. 외부/member API가 fencing token을 강제할 수 없으면 해당 step 결과가 불명확한 경우 재호출하지 않고 `NeedsOperator`로 보낸다. idempotent reconcile만 재시도 가능하며, idempotency 보장은 resource/action 단위로 명시되어야 한다.

## 장애별 safe degradation

| 장애/검출 | API와 operation 동작 (PROPOSED) | member workload 동작 |
|---|---|---|
| API replica 1개 종료 | 다른 ready replica가 읽기/쓰기 처리. 미완료 요청은 idempotency key로 재호출 | 변경 없음 |
| API 전체 불가 | 관리 API unavailable; 새 mutation 수락 안 함. 이미 저장된 operation은 controller/state store가 허용하는 범위에서 진행 | 기존 트래픽 유지. management health를 이유로 workload 제거 금지 |
| state store 불가 또는 API와 partition | 상태 조회는 stale임을 명시하거나 실패. 모든 신규 mutation 및 다음 비-idempotent step fail-closed; lease 갱신 실패 owner는 정지 | 기존 트래픽 유지. rollout, credential rotation, policy 변경, destructive step은 `Paused` 또는 결과 불명 시 `NeedsOperator` |
| controller/owner 프로세스 종료 | lease 만료와 새 epoch 발급 전까지 다른 worker는 action 금지. 복구 worker는 `Reconciling`부터 시작 | 기존 트래픽 유지 |
| member cluster API/network 단절 | 해당 cluster 대상 operation만 `Paused` (`MemberUnreachable`). 다른 healthy cluster의 독립 operation은 정책상 계속 가능 | 현재 workload 및 설정 유지; 자동 failover/drain 금지 |
| event/webhook consumer/외부 endpoint 불가 | durable outbox 유지, retry 가능한 실패만 재전달. terminal operation 상태를 외부 delivery 성공에 종속시키지 않음 | 변경 없음 |
| event 저장소 full/commit 실패 | event와 operation 전이 함께 거부/rollback. 관측되지 않는 mutation 금지 | 변경 없음 |

Rollout은 진행 중인 wave 경계에서 멈추고 이미 완료된 cluster 변경을 자동 rollback하지 않는다. credential rotation은 새 credential 검증 이전 old credential revoke를 금한다(기존 수동 절차의 제한은 `multicluster-control-plane.md` 참조). policy/destructive 작업은 dependency 회복만으로 자동 재개하지 않고 reconciliation gate를 통과해야 한다. 사용자 취소는 요청일 뿐 이미 전송된 외부 action의 중단 증거가 아니다(`#42` async operation 계약).

## 복구·재조정 순서

복구 순서는 아래 순서를 바꾸지 않는다. 각 단계의 시작/완료 시각, 책임자, 결과 및 evidence reference를 recovery record에 남긴다(**PROPOSED**).

1. **격리:** 새 destructive/credential/policy mutation 수락을 막고, 기존 worker의 lease 갱신과 실제 action이 멈췄는지 확인한다. partition 중 두 owner가 모두 유효하다고 판단할 수 있으면 해당 operation을 `NeedsOperator`로 격리한다.
2. **State store 복원:** quorum/primary health, schema version, restore point 및 backup checksum을 확인한다. operation, event inbox/outbox, dedupe index, revision, evidence linkage를 한 consistency point로 restore한다. 일부 테이블만 restore했거나 backup 시각 이후 commit 유실이 예상되면 실행을 열지 않는다.
3. **Ownership 재설정:** 이전 owner lease 만료/폐기를 확인하고 새 `fence_epoch`를 발급한다. 이전 epoch를 가진 worker는 외부 action을 수행할 수 없어야 한다. fencing을 입증할 수 없으면 executor를 계속 격리한다.
4. **Read-only inventory:** member별 API reachability를 확인하고 persisted desired intent, 마지막 checkpoint, member actual resources/versions/credentials, 관측 가능한 audit/evidence를 수집한다. member unreachable은 실패 상태로 덮지 않고 별도 stale observation으로 표시한다.
5. **Reconciliation:** step별로 `desired`, `actual`, `last confirmed action`, `idempotency capability`를 비교한다. actual이 이미 목표면 step을 완료 처리하고 증거를 연결한다. actual이 시작 전과 같고 action이 idempotent임이 확인되면 같은 operation에서 재개한다. 부분 적용/결과 불명/예상 외 actual/비-idempotent 재호출 필요는 `NeedsOperator`로 둔다.
6. **Replay:** durable inbox/outbox를 event ID와 `idempotency_key` 또는 `source_event_id`로 중복 제거하며 재전송한다. 기존 `event_id`, `operation_id`, `correlation_id`를 새 값으로 치환하지 않는다. 기존 #42 계약의 dedupe window만으로 장기 replay가 보장된다고 가정하지 말고 durable uniqueness를 유지한다.
7. **재개 승인:** state store 및 lease fencing, reachable member reconciliation, event backlog 정책 검증을 통과한 뒤에만 paused mutation을 재개한다. `NeedsOperator` 해제에는 actor, 판단 근거, actual-state evidence, 선택한 resume/close action을 기록한다.

Backup/restore acceptance는 operation과 이벤트 간 관계, dedupe identity 및 transition revision을 보존하는지로 판정한다. backup point 뒤 수락한 operation이 유실될 가능성이 있으면 그 범위를 RPO로 보고해야 하며, 복구 중복 실행은 허용 가능한 RPO 설명으로 상쇄되지 않는다.

## 측정 기준 및 drill 판정

RTO/RPO 숫자는 기존 repo에 승인된 값이 없다. 그러므로 수치를 만들어내지 않는다. **PROPOSED release gate:** fleet owner가 API failover RTO, operation state/event RPO, state-store restore RTO, partition hold 탐지시간을 수치로 승인하고, 측정 결과가 그 한계를 만족하기 전에는 운영 HA 완료로 판정하지 않는다.

오프라인 결정론 drill은 동일 seed/input bundle에서 다음을 반복한다: operation accept → wave 일부 적용 → active controller kill → state store/API partition → control plane outage 동안 workload probe → state/event backup restore → 새 fence epoch → actual-state reconciliation → inbox/outbox replay → evidence export. PASS 기준은 (1) operation/event ID 및 correlation linkage 동일, (2) 각 non-idempotent step의 외부 적용 횟수 최대 1회, (3) 미확정 step이 자동 재실행되지 않음, (4) healthy workload probe 지속 성공, (5) 새 destructive action이 reconciliation gate 전에 0회, (6) 승인된 RTO/RPO 내 복구다. 현재 `tests/chaos/`의 `istiod-kill`, `cnpg-primary-kill` 등은 이 drill을 대체하지 않는다. `failure-injection-catalog.md`도 multi-cluster staged upgrade 시험이 없는 갭으로 분류한다.

## 설계 결정

- **D1 — 상태 권위:** operation/event 기록의 권위는 **PROPOSED** HA state store이며 process memory, member cluster, event delivery endpoint는 권위 저장소가 아니다. 비용은 외부 store 의존성이다. 저장소 구현 선택은 후속 설계로 남긴다.
- **D2 — 단일 executor ownership:** non-idempotent action은 CAS lease와 증가하는 fencing epoch를 통과해야 한다. fencing 미지원 대상은 불명확한 완료를 자동 재시도하지 않는다. 이로 인해 일부 작업은 수동 gate에 머물 수 있다.
- **D3 — workload 분리:** management plane 장애만으로 member workload를 withdraw하지 않는다. APISIX cache 손실 등 data-plane 자체 장애가 확인되면 별도 서비스 복구 계약을 따른다.
- **D4 — recovery 우선순위:** 저장소 일관성 → owner 격리/fencing → actual-state reconciliation → event replay → 신규 mutation 재개 순이다. 복구 속도보다 중복 mutation 방지를 우선한다.
- **D5 — 수치 없는 SLO 금지:** 승인된 RTO/RPO는 저장소에 없으므로 숫자는 operator/fleet owner 결정 전까지 미정이다. 수치와 drill evidence가 없으면 HA acceptance는 미완료다.

## 범위와 미해결 입력

외부 database/queue 제품 선정·구현, Kubernetes control plane HA, Portal API/schema 구현, controller 및 fencing adapter 구현, backup mechanism, API 인증/RBAC 설계, 구체 RTO/RPO 승인, live 3-node 또는 offline drill 실행은 범위 밖이다. #42 envelope의 schema/version, event dedupe 보존, #108 승인/evidence 필드가 실제 연결될 때 이 문서의 제안 field를 해당 contract와 정합 검토해야 한다. **ASSUMPTION:** fleet operation은 management service와 member Kubernetes API 사이에서 단계적으로 적용되며, 모든 대상 API가 공통 fencing protocol을 지원하지는 않는다.
