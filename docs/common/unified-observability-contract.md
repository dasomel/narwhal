# Unified Observability Contract

- 상태: 설계 계약 초안
- 범위: Narwhal에 배포된 metrics/logs/traces의 현재 구성과 서비스·워크로드 SLO, 상관 키, 보존 및 비용 귀속의 목표 계약
- 근거 구현: `gitops/charts/narwhal-apps/templates/{prometheus-stack,loki,tempo,k8s-monitoring}.yaml`, `gitops/resources/{grafana-datasources,prometheus-alerts}.yaml`
- 경계: 현재 설정은 **현재 구현**으로, 새 필드·경로·동작은 **PROPOSED**로 표시한다. 미구현 상태를 acceptance 완료로 간주하지 않는다.

## 현재 신호 인벤토리

| Signal | 확인된 배포 구성 | 보존 및 한계 |
|---|---|---|
| Metrics | `prometheus-stack.yaml`의 `kube-prometheus-stack`은 `monitoring`에 배포되고 Prometheus `retention: 7d`, 10Gi PVC를 사용한다. `enableRemoteWriteReceiver: true`이며 주석상 k6 결과를 remote-write로 받는다. | TSDB 보존은 7일이다. remote-write 수신 활성화는 OTLP 수신을 뜻하지 않는다. |
| Logs | `k8s-monitoring.yaml`의 `k8s-monitoring` chart는 `podLogsViaLoki.enabled: true`로 `alloy-logs`를 사용하며 Loki push URL은 `http://loki:3100/loki/api/v1/push`다. `loki.yaml`은 Loki Monolithic 1 replica, SeaweedFS S3 `loki` bucket, TSDB schema v13을 설정한다. | 저장은 S3지만 설정에서 Loki retention/삭제 기간을 찾지 못했다. 유한 보존을 주장할 수 없다. `auth_enabled: false`와 replication factor 1도 현재 설정이다. |
| Traces | `tempo.yaml`은 Tempo 2.9.0, SeaweedFS S3 `tempo` bucket을 설정한다. `prometheus-stack.yaml`은 Grafana의 OTLP tracing address를 `tempo.monitoring.svc.cluster.local:4317`로 지정한다. | Grafana 자체 tracing 설정은 일반 애플리케이션 OTLP ingress/collector 경로를 증명하지 않는다. 설정상 trace retention은 확인되지 않았다. |
| Profiles | 해당 배포 manifest에서 profile backend/수집 구성을 찾지 못했다. | **PROPOSED:** profile 신호는 현 계약의 배포 범위 밖이며 별도 backend 채택 전 수집/보존을 약속하지 않는다. |

`grafana-datasources.yaml`은 Loki를 `http://loki:3100`, Tempo를 `http://tempo:3100` datasource로 등록한다. `docs/common/architecture.md`는 telemetry source에 Prometheus, Loki, Tempo, Hubble을 열거한다. datasource 존재만으로 로그↔trace correlation이나 incident UI가 동작한다고 추정하지 않는다. `gitops/resources/prometheus-alerts.yaml`의 `narwhal-alerts`는 PrometheusRule 기반 인프라/플랫폼 alert의 실제 예이며 SLO 정책은 아니다.

## 제안된 정규 telemetry 레코드

**PROPOSED:** 모든 signal은 다음 공통 resource identity를 제공한다. Kubernetes resource attribute와 log label / metric label / trace resource attribute에 동일 값을 사용한다.

| 필드 | 필수 | 정의 / 검증 |
|---|---:|---|
| `service.name` | 예 | 안정된 논리 서비스 이름. pod 이름이나 replica ID로 쓰지 않는다. |
| `service.namespace` | 예 | 서비스 도메인 구분자. Kubernetes namespace와 다를 수 있으며 혼용 금지. |
| `deployment.environment.name` | 예 | 운영 환경 ID. 허용 값은 배포 inventory가 소유한다. |
| `k8s.cluster.name` | 예 | 클러스터 ID. 현재 Alloy 설정의 `cluster.name: narwhal`과 일치시킨다. |
| `k8s.namespace.name` | 해당 시 | Kubernetes namespace. 플랫폼 수집기는 workload identity에서 채운다. |
| `k8s.workload.name` | 해당 시 | owning Deployment/StatefulSet/DaemonSet/Job 등. pod UID/name 대신 집계 가능한 상위 workload. |
| `service.version` | 권장 | 배포 버전 또는 immutable image digest의 안정적 표기. |

**PROPOSED cardinality 규칙:** `trace_id`, `span_id`, `request_id`, 사용자 ID, pod UID 및 임의 URL은 metric label로 금지한다. 이들은 trace/log event 또는 exemplar 연결에만 둔다. 누락/충돌 값을 임의의 `unknown` tenant로 합치지 않고 ingest 오류·drop 지표로 계량한다.

## 상관 ID 계약

**PROPOSED:** 세 종류 ID 의미를 분리한다. `trace_id`는 W3C Trace Context 16-byte ID의 lowercase hex 32자, `span_id`는 8-byte ID의 lowercase hex 16자다. `request_id`는 한 요청의 경계 ID이며 trace sampling/생성 정책과 별개다. 비밀·사용자 직접 식별 정보는 ID에 인코딩하지 않는다.

| 신호/경계 | 계약 |
|---|---|
| HTTP 진입·전파 | 유효한 `traceparent`가 있으면 검증 후 전파한다. malformed 값은 새 trace를 시작하고 오류 카운터/구조화 로그에 `invalid_trace_context`를 기록한다. `tracestate`는 크기/형식 제한을 적용하고 외부 신뢰 경계에서는 허용 목록 정책을 적용한다. |
| 애플리케이션 로그 | 구조화 필드 `trace_id`, `span_id`, `request_id`를 제공한다. 현재 Alloy의 pod log 수집 경로에 맞는 parser/label 구현은 **PROPOSED**다. 이 필드는 Loki index label이 아닌 event field로 둔다. |
| Trace | SDK/계측이 W3C context를 생성·전파한다. OTLP 수신 이후 Tempo에 보존한다. 현재 일반 workload OTLP 수신이 구성되었다는 근거는 없다. |
| Metrics | trace/request ID는 label이 아니다. 가능한 경우 exemplar에 trace ID를 붙이고, service/resource label은 정규 telemetry 필드를 쓴다. exemplar 지원/활성화는 **PROPOSED**다. |
| Platform alert / incident | alert fingerprint는 alert name 및 안정된 resource labels로 만든다. `trace_id`나 매 시각 바뀌는 값은 fingerprint에 넣지 않는다. incident record는 `alert_fingerprint`, `cluster`, `namespace`, `workload`, `service`, 시간 구간 및 선택적 trace/request ID를 연결한다. incident 화면/저장소는 **PROPOSED**다. |

알 수 없는 trace ID를 로그에 생성하거나 ID 형식 오류를 정상 correlation으로 처리하지 않는다. trace sampling으로 trace가 없을 수 있으므로 로그/metric의 ID 부재는 저장 실패와 구분한다. alert가 활성화되지 않거나 owner mapping이 없으면 incident 자동 생성 대신 correlation 상태를 `unmatched`로 표시한다.

## SLO 및 error budget 형식

**PROPOSED:** SLO는 서비스별 version-controlled 선언으로 정의한다. `docs/common/test-strategy.md`와 `gitops/resources/prometheus-alerts.yaml`은 테스트 계층 및 일반 alert를 설명하지만, 저장소에서 공통 SLI/SLO schema·목표값·error budget 정책은 확인하지 못했다. 아래 형식은 예시가 아닌 필수 계약 필드이며 실제 값은 서비스 owner가 승인해야 한다.

| 필드 | 형식 및 제약 |
|---|---|
| `apiVersion`, `kind` | `observability.narwhal.io/v1alpha1`, `ServiceSLO` (**PROPOSED 식별자**) |
| `metadata.name` | DNS label; 안정된 서비스별 이름 |
| `spec.owner` | 책임 team 또는 운영 그룹 ID; 빈 값이면 승인 불가 |
| `spec.target` | `cluster`, `namespace`, `service` 필수 식별 범위. `service`는 `service.name`과 일치 |
| `spec.indicator.type` | `availability` 또는 `latency` 중 하나. 지원되지 않는 유형은 reject |
| `spec.indicator.query` | 검토 가능한 PromQL 식. 분자·분모 또는 latency threshold/분위수와 제외 조건을 문서화 |
| `spec.objective` | `[0,1]` 사이 비율, 예: `0.999`; 숫자만으로 단위 혼동이 없도록 ratio로 고정 |
| `spec.window` | 고정 rolling 기간 (`7d`, `28d` 등); 승인된 정책에 없는 값은 reject |
| `spec.budgetPolicy` | 기간별 허용 실패량 계산 방식 및 burn-rate alert window/배수. 미정이면 계산/alert 생성 금지 |
| `spec.exclusions` | 계획 점검 등 제외 조건. 이유, 적용 기간, 승인자 필요; 무기한 wildcard 금지 |
| `spec.revision` | spec 변경 revision; 결과 및 alert annotation에서 확인 가능 |

가용성 SLI는 `good_events / eligible_events`, latency SLI는 `threshold 이내 eligible requests / eligible requests`로 정의한다. `eligible_events = 0`, 필수 데이터 소실, 쿼리 오류 또는 분모 불일치는 SLO `unknown`이며 성공으로 계산하지 않는다. `error_budget = eligible_events × (1 - objective)`는 고정 window 기준이다. 소수 event 처리/기간 경계 계산은 구현 시 PromQL recording rule 단위테스트로 고정한다. SLA는 계약상 외부 약속으로 SLO와 구분하고, 법무/서비스 owner 승인 없는 SLO를 SLA로 표시하지 않는다.

상태는 **PROPOSED** 다음 enum을 쓴다: `pending` (필드/owner 미승인), `active` (유효 쿼리 및 데이터 존재), `breached` (window 내 budget 초과), `exhausted` (남은 budget 0 이하), `unknown` (데이터·쿼리 신뢰 불가), `disabled` (owner가 승인된 기간 동안 비활성). `unknown`은 `active`로 오인하거나 budget 소진 판단을 숨기지 않는다.

## 보존 계약

**현재 확인:** Prometheus 7일. Loki와 Tempo의 저장 backend는 SeaweedFS S3(`loki`, `tempo`)이나 retention 기간은 GitOps values에서 확인되지 않는다. `docs/common/architecture.md`의 10Gi 관측 PVC 표는 기존 설명이며, 현 Loki/Tempo manifest는 S3를 설정한다. 이 문서는 해당 표를 현재 Loki/Tempo 보존 증거로 사용하지 않는다.

**PROPOSED:** 각 signal의 retention은 배포 values에서 명시적 유한 기간으로 선언되어야 하며, 설정 부재/0/무제한 값은 release 검토에서 실패로 취급한다. metrics 기본 7d는 현 설정을 유지하는 baseline이다. logs/traces/profile의 기간과 저장량 quota는 owner가 비용/규제 요건으로 결정하기 전까지 미정이며, 추정 기간을 구현 사실처럼 기재하지 않는다. deletion/compaction 지연, object versioning 및 backup으로 인한 물리 잔존 기간은 별도 기록하고 법적 보존/삭제 요구에 대해 검증한다.

## FinOps 귀속 및 사용량

**PROPOSED:** ingest usage record의 차원은 `cluster`, `signal` (`metrics|logs|traces|profiles`), `service.name`, `k8s.namespace.name`, `k8s.workload.name`, `deployment.environment.name`, `period_start`, `period_end`다. byte 기반 필수 측정값은 수신량과 저장 증가량을 분리하고, metrics는 samples 수와 series 수를 함께 기록한다. 비용은 실제 청구가 아니라 가격표/용량단가를 곱한 `estimate`로 표시하며 통화, 단가 revision, 계산 기간, 미분배분을 함께 보여야 한다.

비용 배분 순서: 직접 식별 가능한 namespace/workload ingest → 공유 플랫폼/collector overhead → 미할당 잔여분. 직접 계측이 불가능하면 비용을 임의 tenant에 나누지 않고 `unallocated`로 표시한다. 이 계약은 quota enforcement, chargeback 또는 tenant 격리를 새로 부여하지 않는다.

**PROPOSED attribution labels:** `narwhal.io/owner`, `narwhal.io/cost-center`, `narwhal.io/environment`는 resource metadata 계약 후보이며 현 Kubernetes label로 존재한다고 주장하지 않는다. 값은 GitOps 승인/소유 inventory에서 검증하고 사용자 자유 입력은 정규화/허용 목록 검사 없이 청구 키로 사용하지 않는다. cluster/namespace는 현재 Kubernetes 식별자를 그대로 귀속의 최소 단위로 사용한다. label 누락은 ingest를 중단시키지 않고 `unallocated` 금액/사용량을 높이며 데이터 품질 경고를 낸다.

## Collector, export 및 오류 기준

`prometheus-stack.yaml`의 remote-write receiver는 k6 remote-write 용도로 설명되어 있다. 이 설정은 OTLP gRPC/HTTP endpoint가 아니며 issue의 “OTel ingress 경로 정의” 수용 기준을 충족한다고 보지 않는다.

**PROPOSED ingest 경로:** 서비스 계측은 OTLP/gRPC 또는 OTLP/HTTP로 수집 계층에 전송하고, 수집 계층은 resource identity 검증·속성 정규화·quota/rate limit·batch/retry 후 signal별 backend로 내보낸다. 중앙 OpenTelemetry Collector 배포 및 DNS/service endpoint, 인증, TLS, network policy, queue/persistence 및 export protocol은 별도 설계·보안 검토 후 정한다. 현재 없는 host/port, endpoint, namespace, secret, environment variable 이름을 이 계약에서 만들지 않는다. Prometheus pull scrape, remote-write producer와 Loki/Tempo push 경로는 OTLP와 서로 다른 protocol로 취급한다.

**PROPOSED 실패 처리:** 잘못된 resource identity/trace context는 이유별 카운터 및 rate-limited diagnostic을 남긴다. 인증/TLS 오류는 fail closed이며 익명 tenant로 downgrade하지 않는다. backend unavailable은 bounded queue/retry 후 drop을 계량하고, backpressure가 producer에 전달 가능한 protocol에서는 retryable response를 반환한다. 허용량 초과는 차원별 quota/error telemetry를 남기며 일부 signal drop이 다른 signal의 성공으로 표시되지 않게 한다. credential이나 원문 payload를 로그로 출력하지 않는다.

Vendor-neutral export는 **PROPOSED** OpenTelemetry OTLP 표준 payload와 Prometheus/OpenMetrics 호환 metrics 경로로 제한된 개념 계약이다. 실제 외부 export API, endpoint, 인증 모델, 필터링, rate limit, format version 및 audit는 미정이며 public API가 있다고 이 문서는 주장하지 않는다.

## 현재 격차 및 수용 판정

| 이슈 수용 항목 | 저장소 근거 기준 판정 |
|---|---|
| OTLP ingress / Collector | 미구현 확인. Grafana→Tempo OTLP tracing address만 존재. **PROPOSED** |
| Prometheus/Loki/Tempo correlation ID | datasource만 확인; 공통 ID 전파/parser/exemplar 및 correlation 화면은 근거 없음. **PROPOSED** |
| 서비스·워크로드 SLI/SLO/error budget | 공통 모델·목표 없음. 일반 Prometheus alert와 혼동 금지. **PROPOSED** |
| alert-to-incident correlation UI | 확인되지 않음. **PROPOSED** |
| signal별 ingestion volume / retention cost | Prometheus 7d 외 retention 근거 없고 용량/비용 dashboard 확인 안 됨. **PROPOSED** |
| tenant/namespace 사용량 | `k8s.namespace.name`는 귀속 후보. per-namespace 수집량/비용 구현은 확인되지 않음. **PROPOSED** |
| vendor-neutral export/API | receiver 또는 Grafana datasource 설정은 외부 export/API를 의미하지 않음. **PROPOSED** |
| 기존 stack regression 없음 | 문서만으로 runtime 회귀 테스트를 증명할 수 없음. 구현/배포 시 기존 scrape, log push, Tempo query, retention 검증 필요. |

**ASSUMPTION:** `service.namespace` 및 `deployment.environment.name`은 OpenTelemetry resource attribute 이름을 적용할 수 있는 애플리케이션 계측 체계가 존재한다는 가정이다. 현재 repo에서 이를 채우는 공통 SDK/bootstrap을 확인하지 못했다. **ASSUMPTION:** namespace는 개발 tenant 귀속 최소 경계로 제안하지만, 현재 observability backend가 tenant 격리/namespace별 billing을 제공한다는 뜻은 아니다.

## 범위 밖

이 deliverable은 계약 문서다. Collector/SDK 설치와 endpoint, API/CRD 구현, dashboard 및 incident UI, SLO query/alert 배포, retention policy 수치 확정, 가격표 및 실제 비용 회계, profile backend, tenant isolation, vendor export 구현/회귀 실행은 범위 밖이다. 이를 완료 처리하려면 각각 GitOps/코드 변경과 cluster 또는 통합 환경 증거가 필요하다.
