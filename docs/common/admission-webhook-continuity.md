# Admission Webhook 연속성 정책

> 범위: Narwhal GitOps가 관리하는 admission webhook의 설정 검토·장애 검증 계약이다. 이 문서는 현재 클러스터 상태를 증명하지 않는다. 저장소에서 확인한 선언과 제안 기준을 분리한다.

## 1. 현재 배포 inventory

Inventory 근거는 `gitops/charts/narwhal-apps/templates/`의 Argo CD `Application`, `gitops/charts/narwhal-platform/`의 자체 리소스 및 `gitops/README-ko.md`다. 런타임에 생성되는 `ValidatingWebhookConfiguration`/`MutatingWebhookConfiguration` 목록은 각 클러스터에서 다시 확인해야 한다.

| 컴포넌트 | admission 역할 및 범위 | 저장소에서 확인한 설정 | 연속성 분류 / 결정 |
|---|---|---|---|
| Kyverno (`platform-system`) | 정책 admission: `gitops/resources/kyverno-policies.yaml`의 privileged/host namespace/host port/latest tag 등 ClusterPolicy. Chart가 webhook configuration 생성 | `gitops/charts/narwhal-apps/templates/kyverno.yaml`: admission controller 3 replicas, `webhookTimeout: 5` 및 인자 5초, `config.webhooks.namespaceSelector`가 `kube-system`, `istio-system`, `platform-system`을 제외. `failurePolicy` 및 webhook `timeoutSeconds`는 이 Application에서 명시하지 않음. 정책 파일의 주석은 Kyverno 기본값이 Fail이라고 기록 | **Fail-closed (기존 기본 동작 유지)**. 보안 정책과 워크로드 보호 정책을 가용성 장애로 우회시키지 않는다. 5초는 webhook 처리 timeout 선언이나 API server webhook `timeoutSeconds`가 같은 값인지 저장소만으로 확인되지 않음 (**확인 필요**) |
| cert-manager (`platform-system`) | cert-manager API validation 및 `CertificateRequest` mutation. 실제 chart templates에 validating/mutating configuration 존재 | `gitops/charts/narwhal-apps/templates/cert-manager.yaml`: webhook 2 replicas, RollingUpdate `maxSurge: 1`/`maxUnavailable: 1`, PDB `minAvailable: 1`, hostname topology spread `DoNotSchedule`, cainjector 2 replicas. Narwhal은 chart `v1.20.2`를 선언하지만 repo 내 cert-manager templates/values는 Dashboard subchart `v1.19.1` vendoring이므로 이 자료는 semantics 참고이며 production render의 직접 근거가 아님. 해당 templates는 `failurePolicy: Fail`, `.Values.webhook.timeoutSeconds`를 사용하고 vendored 기본값은 30초다. Narwhal Application은 timeout override 없음. CA bundle은 cainjector 주입이며 ArgoCD가 drift 무시 | **Fail-closed 권고**. 실제 `v1.20.2` render/live configuration에서 `Fail`과 timeout을 확인해야 한다. Exact generated failurePolicy/timeout은 repo만으로 완전히 입증되지 않는다. |
| Istio `istiod` 및 `base` (`istio-system`) | Istio 리소스 validation. `gitops/charts/narwhal-apps/templates/istiod.yaml`에서 webhook configuration의 `/webhooks/0/failurePolicy` drift를 무시 | Istiod 2 replicas와 preferred pod anti-affinity; `gitops/charts/narwhal-apps/templates/istio-base.yaml`에도 `failurePolicy` drift ignore가 있음. 레포는 해당 필드의 원하는 값 또는 timeout/CA 주입 방식을 설정하지 않음 | **Fail-closed 권고 (PROPOSED)**. service mesh 보안/네트워크 리소스의 잘못된 구성을 허용하지 않는다. 현재 동작은 upstream chart/runtime 확인 전 미확정이며, ArgoCD가 `failurePolicy`를 복구하지 않으므로 drift 탐지가 별도 필요 |
| Chaos Mesh (`chaos-testing`) | Chaos 실험 리소스 validation/mutation. Application이 양 webhook configuration의 CA bundle을 무시 | `gitops/charts/narwhal-apps/templates/chaos-mesh.yaml`: controllerManager replicaCount 1, 런타임 생성 CA bundle drift ignore. timeout/failurePolicy/selector 값은 Application에 없음 | **Fail-closed 권고 (PROPOSED)**. 승인되지 않은/잘못된 장애 주입 정의가 통과하지 않도록 한다. 단일 controller와 CA 회전 동작은 실험 기능의 가용성 조건으로 별도 점검 |
| OpenBao chart webhook (`storage`) | Chart가 `MutatingWebhookConfiguration`의 `/webhooks/0/clientConfig/caBundle`을 생성·관리하는 것으로 ArgoCD ignore 설정이 확인됨. 구체적인 mutation/selector는 이 저장소에 없음 | `gitops/charts/narwhal-apps/templates/openbao.yaml`: 해당 CA bundle drift ignore. Application은 OpenBao HA를 끄고 단일 인스턴스를 선언하지만 이것만으로 webhook backend replica 수를 판단할 수 없음 | **Fail-closed 권고 (PROPOSED)**. 주입 mutation을 안전하게 생략하면 의도한 workload 설정과 달라질 수 있다. Chart render와 실제 webhook의 rules/selector/Service를 확인해 failurePolicy를 확정할 것 |

### 현재 미배포 또는 판정 제외

- Dashboard subchart의 ingress-nginx는 `gitops/charts/kubernetes-dashboard/values.yaml`에서 `nginx.enabled: false`이며, 해당 values의 `cert-manager.enabled: false`다. 따라서 그 subchart의 webhook 설정은 현재 production inventory가 아니다.
- 같은 values에서 Kong `ingressController.enabled: false`다. Kong admission webhook 템플릿이 존재한다는 사실만으로 배포 중이라고 판정하지 않는다.
- **ASSUMPTION:** 위 표는 GitOps 선언상 기본 플랫폼의 production 후보를 나타낸다. 클러스터 overlay, 수동 리소스 또는 설치 후 잔존 configuration이 없다는 보장은 없다. `kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations` 결과를 승인된 클러스터별 inventory와 대조해야 한다.

## 2. Failure policy 및 timeout 계약

Kubernetes webhook의 `failurePolicy` 허용 값은 `Fail`과 `Ignore`다. 둘 다 webhook이 timeout되거나 연결/TLS 오류가 나면 적용된다. `Fail`은 admission 요청을 거부하고 `Ignore`는 webhook 결과 없이 계속 진행한다. 따라서 business validation 실패 응답 자체를 `Ignore`로 우회할 수는 없다. 이 정책은 장애 때의 API 요청 의미를 정하는 값이다.

| 위험 등급 | 기준 | 장애 시 의미 | 기본 결정 |
|---|---|---|---|
| Critical | 보안 경계, 권한/격리, 인증서 신뢰, 정책 우회가 가능한 admission | webhook 실패 시 요청이 통과하면 보호가 사라짐 | `Fail` |
| High | mutation/validation 결과가 workload 실행 의미를 바꾸거나 필수 구성 보장을 담당 | fail-open 시 불완전한 리소스가 저장될 수 있음 | `Fail`; workload 영향은 운영 유지보수 상태로 처리 |
| Availability-only | 통과 여부가 보안/격리 경계에 영향을 주지 않고 누락을 허용 가능한 webhook | fail-open 위험이 문서화되고 소유자가 승인한 경우에만 `Ignore` | 기본 없음; 예외 승인 필수 |

위 표의 분류에 따라 Kyverno는 저장소에 Fail 기본값 근거가 있고, cert-manager는 Fail 권고이나 exact production render 확인이 필요하다. Istio/Chaos Mesh/OpenBao는 값 확인 전까지 fail-open 예외를 허용하지 않는다. 미확정 webhook을 `Ignore`로 바꾸는 것은 **PROPOSED 금지 규칙**이다. 예외는 webhook configuration 이름, webhook 항목 이름, rules, selector, 기간, 위험 수용자, 복구 검증을 기록하고 Narwhal #117 waiver 절차와 연결한다. 예외 만료 시 원래 Fail 상태를 재검증한다. #117의 저장소 구현 여부는 이 문서에서 확인하지 않았으므로 승인 시스템이 이미 있다고 간주하지 않는다.

Timeout은 Kubernetes API의 webhook `timeoutSeconds`(유효 범위 1–30초)와 엔진 내부 처리 timeout을 구분한다. 현재 Kyverno 내부 값 5초와 cert-manager chart 기본값 30초는 서로 다른 계층의 값일 수 있다. API-server 전체 admission latency SLO는 현재 확인되지 않음 (**PROPOSED**): webhook별 API timeout 목표를 명시적으로 pin하고, p95/p99 latency 및 timeout/error 비율을 측정해 가장 짧은 상위-level budget 안에서 끝나도록 한다. webhook timeout에 별도 자동 재시도 횟수를 가정하지 않는다. Kubernetes API 요청 재시도는 클라이언트 동작이며 검증 시 같은 요청의 무한 재시도를 허용하지 않는다.

## 3. CA trust, selectors 및 가용성 요구사항

1. `clientConfig.service`의 namespace/name/path/port가 Ready endpoint를 가리키는지 확인한다. `ValidatingWebhookConfiguration`과 `MutatingWebhookConfiguration` 각각의 `webhooks[].clientConfig.caBundle`이 실제 serving certificate의 issuer를 신뢰하는지, 인증서 SAN이 Service DNS와 맞는지, 유효기간 내인지 확인한다. CA bundle이 비어 있거나 stale이면 정상 상태로 판정하지 않는다.
2. cert-manager는 `cainjector`가 bundle을 주입한다. 관련 Application의 `ignoreDifferences`는 ArgoCD가 필드를 덮어쓰지 않게 할 뿐, CA 유효성 검사가 아니다. Narwhal은 cert-manager `v1.20.2`를 선언하지만 repo 내 webhook templates는 Dashboard subchart `v1.19.1`이므로 exact production injection behavior는 release render/live object로 확인해야 한다. Chaos Mesh와 OpenBao도 각각 CA bundle drift를 무시하므로 동일한 독립 검증이 필요하다. Istio CA/serving cert lifecycle은 본 repo 선언만으로 확정할 수 없다.
3. CA 회전은 **overlap 절차 (PROPOSED, #107 lifecycle과 정합 필요)**로 수행한다: 새 CA를 기존 신뢰 bundle과 동시에 제공 → 새 CA로 발급된 serving cert 배포 및 endpoint readiness 확인 → API server를 통한 실제 admission 성공 확인 → 구 CA 제거 후 재검증. 어느 단계에서든 admission/TLS 오류가 발생하면 rollout/promotion을 중지하고 마지막 정상 trust 조합으로 복구한다.
4. 모든 Fail webhook backend는 최소 2 Ready endpoints, 서로 다른 `kubernetes.io/hostname`에 분산, PDB `minAvailable: 1`을 요구한다 (**PROPOSED**). 현재 설정상 cert-manager webhook은 2 replicas/PDB/spread 충족, Kyverno admission 3 replicas이나 별도 PDB/spread 선언은 확인되지 않음, Istiod 2 replicas와 preferred anti-affinity는 hard spread 보장이 아님, Chaos Mesh controller 1 replica다. OpenBao webhook backend는 실제 chart render로 확인해야 한다.
5. 모든 webhook 항목의 `rules`, `matchPolicy`, `namespaceSelector`, `objectSelector`, `matchConditions`, `sideEffects`를 렌더링 결과와 live object에서 추출해 inventory와 비교한다. selector가 비어 있는 것은 광범위 매치일 수 있으므로 무조건 안전한 기본값으로 취급하지 않는다. selector 변경은 기존 대상/신규 대상/제외 대상의 집합 diff를 검토하고 예상치 못한 scope 확대·축소 모두 차단한다.
6. 호출 webhook들의 ordering이나 다른 webhook mutation 결과를 고정 순서로 가정하지 않는다. 재호출에도 안정적인 idempotent mutation인지, mutating webhook 재호출 설정이 필요한지, 상호 mutation이 무한 변화를 만들지 확인한다. 이 repo에서 구체적인 webhook ordering 계약은 확인되지 않음.

## 4. 연속성 상태와 승격 gate (PROPOSED)

| 상태 | 진입 조건 | 허용 동작 / 전이 |
|---|---|---|
| `ENFORCING` | inventory 일치, Fail profile 확인, CA trust 유효, 최소 endpoint 수 충족, 합성 admission pass/deny 결과 일치 | 정상 promotion 허용 |
| `DEGRADED` | endpoint 부족, timeout/TLS error, CA drift, selector drift, policy/config drift 중 하나 | 보안 중요 workload promotion 중지. Incident 기록, 진단/복구 작업만 허용 |
| `MAINTENANCE_APPROVED` | 변경 대상/영향 selector/시간창/rollback 담당/위험 수용자가 승인하고 #117 기록 연결 | 승인된 scope와 시간 동안만 maintenance. 별도 승인 없이는 `Ignore`로 바꾸지 않음 |
| `RECOVERING` | backend/CA/policy 수리 뒤 재검증 진행 중 | 정상 promotion 계속 차단 |
| `ENFORCING` 복귀 | live configuration 대조 + 장애 시나리오 재검증 + 보안 소유자 evidence 승인 완료 | incident/예외를 종료하고 promotion 재개 |

Rollout 중에는 구 버전/새 버전 endpoint 모두 같은 admission contract와 trust chain을 제공해야 한다. replicas를 한 번에 0으로 내리거나, CA 교체와 policy 변경을 같은 미검증 단계에서 묶거나, selector를 넓혀 maintenance를 우회하는 방식은 허용하지 않는다. 저장소는 일부 컴포넌트의 replica/PDB를 선언하지만 전체 webhook에 대한 promotion gate 구현은 확인되지 않음 (**PROPOSED**).

## 5. 검증 및 evidence

검증 전후에 GitOps 선언, 렌더링 결과, live 상태를 같은 commit/chart revision과 함께 보관한다. 아래 failure injection은 격리된 disposable 환경에서 실행하고, production cluster에 수행하려면 별도 변경 승인이 필요하다.

| 단계 | 실행/관찰 | 통과 기준 |
|---|---|---|
| Inventory | `helm template`/ArgoCD rendered manifests와 live `kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o yaml` 비교. webhook별 service, rules, selectors, failurePolicy, timeoutSeconds, caBundle hash 기록 | 선언/실제 차이 설명 완료; 비관리 webhook 및 미등록 항목 0 |
| 정상 결정 | 대표 허용/거부 입력을 API server에 제출해 Kyverno, cert-manager, Istio, Chaos Mesh 및 실제 확인된 나머지 webhook의 결과 관찰 | 예상 allow/deny와 audit evidence 일치 |
| Endpoint down / timeout | 격리 환경에서 해당 Service endpoint를 0으로 만들거나 webhook 응답을 timeout 유발. probe는 각 webhook의 실제 매치 rules 대상 리소스를 사용 | Fail profile은 명확한 admission 거부, `Ignore` 예외만 승인 scope에서 통과. API 오류에 webhook/timeout 원인 식별 가능 |
| TLS / CA | 잘못된 serving cert, 만료 cert, 빈/stale `caBundle`을 격리 환경에서 각각 주입 | trust 실패는 성공으로 오인되지 않고 fail policy 결과로 결정됨. CA 복구 후 양방향 정상 검증 |
| Selector drift | namespace/object label 및 selector를 전/후로 변경하고 webhook match 집합 비교 | 허용된 대상 집합만 달라짐; 의도치 않은 축소/확장은 promotion gate가 실패 |
| HA / upgrade | endpoint별 순차 제거, rolling upgrade 동안 반복 admission, cert/key 회전 overlap 검증 | 가용 endpoint 유지 및 기존/신규 요청의 동일 policy 결과. 결과 변동이나 enforcement gap 없음 |
| Recovery | 복구 후 ArgoCD sync 상태, Ready endpoint, CA/SAN/expiry, selectors, 정책 revision 및 positive/negative admission 재확인 | 상태가 `ENFORCING`으로 판정되기 전 promotion 재개 금지 |
| Offline replay | 같은 리소스 입력과 policy/chart revision을 offline 검증기에 입력. 클러스터 API/webhook 결과와 비교 | 입력·정책 revision이 같을 때 동일 allow/deny와 실패 모드. 현재 이 저장소에 이 계약을 충족하는 replay 구현은 확인되지 않음 (**PROPOSED**) |

Evidence record 최소 필드 (**PROPOSED**): `timestamp`, `cluster`, `git commit`, `chart revision`, `webhook configuration`, `webhook name`, `operation/resource`, `namespace/object labels`, `selector/rules digest`, `policy revision`, `caBundle fingerprint`, `serving certificate issuer/SAN/notAfter`, `ready endpoint count`, `timeoutSeconds`, `failurePolicy`, `request outcome`, `error class`, `test scenario`, `operator/approval reference`. Admission 요청 payload나 secret은 기록하지 않는다. #42/#125의 구체적인 evidence schema와 이 필드의 자동 연결은 이 저장소 자료에서 검증되지 않았으므로 통합 구현이 된 것으로 서술하지 않는다.

## 6. 근거 파일과 범위 한계

- 배포 inventory/namespace/version: `gitops/README-ko.md`, `gitops/charts/narwhal-apps/templates/{kyverno,cert-manager,istio-base,istiod,chaos-mesh,openbao}.yaml`
- 참고용 cert-manager webhook semantics 및 vendored chart 기본 timeout (Dashboard chart v1.19.1; Narwhal production chart v1.20.2와 다름): `gitops/charts/kubernetes-dashboard/charts/cert-manager/templates/webhook-{validating,mutating}-webhook.yaml`, `gitops/charts/kubernetes-dashboard/charts/cert-manager/values.yaml`
- Kyverno policy 예시와 fail 기본값 근거: `gitops/resources/kyverno-policies.yaml`; 정책별 enforcement는 `gitops/resources/kyverno-policies.yaml`의 `validationFailureAction`과 개별 rule을 기준으로 판단한다.
- selector 및 dashboard 하위 chart 비활성 상태: `gitops/charts/kubernetes-dashboard/values.yaml`; ingress-nginx 기본값/템플릿은 현재 배포 설정 근거가 아니다.
- 이 문서는 #107 인증서 수명주기, #117 waiver, #42/#125 evidence와 연결하는 운영 계약을 제안하지만 해당 issue의 구현/승인 워크플로를 검증하지 않았다. API server latency SLO, automated selector drift gate, admission evidence pipeline, offline deterministic replay는 모두 현재 구현으로 확인되지 않은 제안이다.
