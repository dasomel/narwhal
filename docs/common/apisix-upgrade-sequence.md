# APISIX 업그레이드 순서와 롤백 기준

이 문서는 narwhal#46의 APISIX 구성요소 한정 실행 설계다. 자동 오케스트레이터나
실클러스터에서 검증된 무중단 절차를 뜻하지 않는다. 현재 선언 상태로는 APISIX의
최소중단 업그레이드를 승인할 수 없다. 특히 `apisix-etcd`는 단일 replica와
`emptyDir`를 사용하고, 재생성 후 라우팅 설정 재동기화가 멈출 수 있다
([`apisix-infra.yaml`](../../gitops/charts/narwhal-platform/templates/apisix-infra.yaml),
[`apisix-etcd-recovery.md`](apisix-etcd-recovery.md)).

## 적용 범위와 실제 의존 순서

APISIX는 chart `2.13.0`으로 설치되며, 현재 값은 앱 `3.15.0`과 Ingress Controller
`1.8.0`을 사용한다. APISIX는 `apisix-etcd.platform-system.svc.cluster.local:2379`의
etcd를 설정 저장소로 사용하고, Controller는 `ApisixRoute` CRD를 감시한다
([`apisix.yaml`](../../gitops/charts/narwhal-apps/templates/apisix.yaml),
[`VERSIONS.md`](../../VERSIONS.md)). 게이트웨이는 wildcard TLS Secret과
`ApisixTls` 리소스도 사용한다 (`apisix-infra.yaml`).

업그레이드 의존 순서는 다음과 같다. 같은 단계 안에서도 앞 단계의 게이트가
통과하기 전에는 다음 단계를 시작하지 않는다.

| 순서 | 구성요소 | 진행 조건 및 결정 |
|---|---|---|
| 0 | Kubernetes/Cilium, cert-manager, 클러스터 DNS | API 접근, Pod 네트워크, `narwhal-wildcard-tls` Secret 및 `narwhal-ca-issuer`가 정상이어야 한다. 상세 플랫폼 순서는 [`upgrade-orchestration.md`](upgrade-orchestration.md)를 따른다. |
| 1 | `apisix-etcd` | 현재는 업그레이드/재시작 금지. 진행하려면 별도 변경으로 지속 저장소와 검증된 snapshot/restore가 마련되고, 빈-prefix 복구 위험이 제거되어야 한다. 단일 멤버 etcd를 다중 멤버로 단순 scale 변경하지 않는다. |
| 2 | APISIX chart의 gateway 및 Ingress Controller | 변경된 chart/app/controller 버전 조합을 사전 호환성 검토한 뒤 한 번에 한 업그레이드만 수행한다. Controller `1.8.0`은 `VERSIONS.md`에서 동결 대상으로 지정되어 있으므로, 이를 변경할 경우 v2 전환을 포함한 별도 migration 설계가 선행되어야 한다. |
| 3 | 라우트와 TLS 사용자 트래픽 | `narwhal-platform`의 `ApisixRoute`, `ApisixUpstream`, `ApisixTls` 동기화 및 실제 대표 경로 검증이 통과해야 작업을 닫는다. 해당 리소스는 [`apisix-routes.yaml`](../../gitops/charts/narwhal-platform/templates/apisix-routes.yaml)과 `apisix-infra.yaml`이 관리한다. |

단계 1의 차단 조건이 해소되기 전에는 APISIX pod 재생성 가능성이 있는 변경을
실행하지 않는다. 기존 recovery 문서는 이전 조합 `apisix-ingress-controller 0.14.1`을
언급하지만, 현재 `VERSIONS.md`와 `apisix.yaml`은 `1.8.0`을 선언한다. 이 버전
불일치는 복구 절차의 현재 조합 적용성을 보장하지 않으므로, 업그레이드 전에 별도
검증·정정해야 한다.

## 실행 전 체크포인트와 preflight

다음 항목은 모두 만족해야 한다. 하나라도 실패하거나 확인할 수 없으면 시작하지
않는다.

1. 변경 승인에는 현재/목표 chart, 앱, Controller, etcd 버전과 변경 대상이 명시되어
있다. 목표 이미지와 chart가 사용 환경의 air-gap bundle에 들어 있는지도 별도로
확인한다. 저장소에는 자동 bundle 연계가 없다.
2. GitOps의 현재 APISIX Application, `apisix-infra.yaml`, `apisix-routes.yaml`의
커밋·버전 pin·values를 보존한다. Secret 값은 문서나 체크포인트에 복사하지 않는다.
3. `apisix-etcd`의 `endpoint health`가 성공하고 `/apisix` prefix의 key가 0개가
아닌지 확인한다. key가 없거나 확인할 수 없으면
[`apisix-etcd-recovery.md`](apisix-etcd-recovery.md)의 bootstrap deadlock 가능성이
있으므로 중단한다. 이 key 검사는 데이터 보존 백업을 대신하지 않는다.
4. **PROPOSED runtime gate:** etcd snapshot을 운영자가 통제하는 영속 위치에 저장하고
복원 가능성을 확인한다. 현재 etcd의 데이터 볼륨은 `emptyDir`이므로 pod 안의 임시
파일만으로는 재시작·노드 손실에 견디는 체크포인트가 아니다. 지속 가능한 snapshot과
복구 리허설이 없으면 etcd를 건드리는 업그레이드는 승인 불가다.
5. APISIX와 Controller Deployment의 `spec.replicas`, `readyReplicas`, rollout
strategy, Service endpoints, PDB 존재 여부를 live cluster에서 확인한다. 현재 repo는
이 두 workload의 replica 수·PDB·topology spread를 명시하지 않는다. 그러므로 실제
ready gateway가 2개 미만이거나, 단일 gateway 제거를 막는 가용성 여유가 확인되지
않으면 rolling 중단 없는 트래픽을 보장할 수 없으며 작업을 시작하지 않는다.
6. `apisix-etcd`, gateway, Controller가 정상이고 모든 의도한 `ApisixRoute` 및
`ApisixTls`가 존재하는 상태를 기록한다. 기준 라우트는 `narwhal-platform` manifest의
`narwhal-portal`, `keycloak`, `gitea`, `prometheus` 이름을 사용한다. 클러스터에 실제
배포된 리소스와 비교하고, 존재하지 않는 host나 응답값을 가정하지 않는다.
7. 기준 트래픽에서 외부 HTTPS 접속과 OIDC 로그인 등 operator가 선택한 업무 경로가
성공하는지 기록한다. APISIX의 access log와 기존 Prometheus 알람을 기준선에 포함한다.
   **ASSUMPTION:** 운영 환경에서 이 경로들을 호출할 수 있는 점검 주체가 있다.

## 한 단계 실행 및 health gate

GitOps가 `automated` sync와 `selfHeal`을 사용하므로 변경 반영은 저장소의 승인된
변경을 Gitea에 push하는 경로다. 임시 `kubectl patch`는 지속 상태가 아니며
Argo CD가 되돌린다 ([`gitops-push.md`](gitops-push.md)). 이 설계는 자동화 명령을
새로 정의하지 않는다.

각 단계의 변경은 하나씩 적용하고 아래 gate가 통과한 뒤에만 다음 단계로 간다.

| Gate | 통과 기준 | 실패 시 |
|---|---|---|
| GitOps 적용 | Argo CD `apisix` Application이 `Synced` 및 `Healthy`; 의도한 chart revision 확인 | 다음 단계 금지, 변경 revision 보존 |
| Pod 준비 | APISIX와 Ingress Controller 각각 `readyReplicas == spec.replicas`; rollout 완료; 반복 재시작/`CrashLoopBackOff` 없음 | 즉시 중단 및 롤백 판단 |
| 저장소/제어 루프 | etcd `endpoint health`; `/apisix` key 존재; Controller가 startup sync 오류 없이 수렴; `ApisixRoute`/`ApisixTls` 상태가 의도한 값 | 즉시 중단. etcd 빈-prefix 또는 controller 404 증상이면 gateway 재시작 금지 |
| 데이터 경로 | baseline과 같은 대표 HTTPS 경로가 성공하고 인증서·OIDC 동작 유지; 5분 관찰 동안 새 5xx 급증 또는 route 장애 알람 없음 | rollback trigger |

**PROPOSED availability limit:** 계획된 APISIX 변경 중 확인된 외부 요청 실패는
0건이어야 한다. 점검 트래픽을 관찰할 수 없거나 기준선이 없으면 “최소중단 통과”로
판정하지 않는다. 이 문서는 현재 metrics에서 요청 손실을 자동 측정하는 기능이
있다고 주장하지 않는다.

## 중단 및 rollback

다음 중 하나면 이후 단계를 중단하고 새 변경을 시작하지 않는다: Application이
`Synced`/`Healthy`가 되지 않음, 모든 replica가 준비되지 않음, etcd 불건강 또는
`/apisix` key 소실, Controller sync 404/반복 실패, 대표 route/TLS/OIDC 실패, 기준선
대비 신규 5xx/사용자 요청 실패. 기존 APISIX 구성에서 etcd pod가 재생성되면
라우팅 설정이 비어도 gateway의 메모리 cache 때문에 일시 정상처럼 보일 수 있으므로,
gateway 재시작은 route key와 API 데이터 경로 검증 전까지 금지한다.

1. 실패한 GitOps revision, Application 상태, Deployment/pod 이벤트·로그, route key
존재 여부를 기록하고 이후 변경을 동결한다. **ASSUMPTION:** 담당자는 cluster read
권한과 실패 시각을 기록할 수 있다.
2. APISIX chart/app/Controller 변경이면, 체크포인트의 이전 pin·values를 GitOps에
복구하는 새 변경을 만들고 승인된 Gitea push 절차로 반영한다. Argo CD가 이전
revision을 적용했는지 확인한다. workload 내부에서 `helm rollback`을 직접 실행하는
절차는 GitOps desired state와 drift를 만들므로 사용하지 않는다.
3. 롤백 후 모든 pod readiness, `/apisix` key 존재, Controller 수렴, 기준 HTTPS/TLS/
OIDC 경로를 같은 health gate로 재검증한다. 이전 버전도 gate를 통과하지 못하면
반복 rollback/재시작을 멈추고 운영 장애 대응으로 넘긴다.
4. etcd 데이터 손실 또는 snapshot 복구가 필요한 경우 이 문서의 일반 rollback으로
복원하지 않는다. 현재 `emptyDir` 및 recovery deadlock 조건에서는 검증된 자동
복구 경로가 없으므로, 서비스 상태를 보존하고 별도 복구 절차를 따른다.

## 이 설계의 한계

- 현재 APISIX chart 값에서 고정된 replica/PDB/topology 배치가 확인되지 않는다.
  chart 기본값만으로 HA를 추정하지 않는다.
- `apisix-etcd`는 replica 1, `emptyDir`이며 별도 지속 백업/복구 자동화가 선언되지
  않았다. 따라서 전체 APISIX stack의 무중단/자동 롤백은 보장되지 않는다.
- 이 문서는 maintenance window, 승인 시스템, progress/audit 서비스, 다중 클러스터
  순차 실행, disruption 자동 측정, 자동 health gate/rollback 구현을 제공하지 않는다.
- `docs/common/upgrade-orchestration.md`의 cert-manager pilot은 별도 범위이며,
  이 문서는 해당 절차를 복제하거나 APISIX에 구현된 것으로 간주하지 않는다.
