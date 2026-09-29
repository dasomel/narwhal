# Workload Security Profile 정책

이 문서는 Narwhal workload의 namespace 분류, admission 정책, runtime 선택 및 검증 기준을 한 곳에서 다룬다. 현재 GitOps에 있는 enforcement와 아직 없는 capability를 구분한다. 아래의 `PROPOSED` 항목은 저장소 구현 현황을 설명하는 문장이 아니라 이후 수렴을 위한 정책 제안이다.

## 현재 저장소 기준선

| 영역 | 현재 확인되는 동작 | 근거 |
|---|---|---|
| Pod Security Admission (PSA) | `scripts/cluster/08-3-security.sh`가 `iam`, `devtools`, `monitoring`, `database`, `dev`, `default`, `cilium-secrets`에 `audit=baseline` 및 `warn=baseline`을 부여한다. `kube-system`, `istio-system`, `platform-system`, `storage`, `security-system`, `nfs-quota-agent`에는 `audit=privileged` 및 `warn=privileged`를 부여한다. 두 루프 모두 `enforce`를 설정하지 않는다. | `scripts/cluster/08-3-security.sh`의 `Applying Pod Security Admission labels` 절 |
| privileged / host namespace / host port | Kyverno `disallow-privileged-containers`, `disallow-host-namespaces`, `disallow-host-ports`가 `Enforce`다. 여러 system namespace를 제외하며, host namespace 정책은 monitoring의 node-exporter만 추가 제외한다. | `gitops/resources/kyverno-policies.yaml` |
| seccomp | Kyverno `add-default-securitycontext`는 제외 namespace 밖 Pod의 pod-level `spec.securityContext.seccompProfile.type`이 없을 때 `RuntimeDefault`를 주입한다. `runAsNonRoot`는 주입하지 않는다고 정책 주석에 명시되어 있다. 이는 manifest mutate이며 실행 중 커널 프로파일을 확인하지 않는다. | `gitops/resources/kyverno-policies.yaml`, `add-default-seccomp` rule |
| 예외 범위 | 주요 Kyverno 정책의 namespace 전체 제외가 존재한다. `restrict-tuning-job-admission`은 `devtools`에서 특별 권한 동작을 `narwhal-tuning-job` ServiceAccount에 한정한다. control-plane node guard는 `Audit`이다. | `gitops/resources/kyverno-policies.yaml`의 해당 정책 |
| RuntimeClass / AppArmor / SELinux | `gitops/resources` 및 관리 템플릿에서 workload용 `RuntimeClass` 정의, `appArmorProfile`, SELinux profile enforcement 또는 runtime effective-state verifier를 확인하지 못했다. 일부 차트에는 선택적 `runtimeClassName` 값과 seccomp 설정이 있으나 이는 cluster RuntimeClass inventory가 아니다. | `gitops/charts/kubernetes-dashboard/charts/ingress-nginx/values.yaml`, `gitops/charts/narwhal-apps/templates/*` |

Kyverno 정책은 `gitops/charts/narwhal-apps/templates/kyverno-policies.yaml`의 Argo CD Application이 `gitops/resources/kyverno-policies.yaml`을 배포한다. 따라서 정책 파일의 존재는 배포 정의의 증거이지, 현재 클러스터에서 동작 중이거나 기존 Pod가 소급 교정되었다는 증거가 아니다.

## 분류 및 목표 프로파일

아래 표의 클래스별 PSA `enforce` 목표와 profile mapping은 **PROPOSED**다. 현재 namespace 라벨은 앞 표를 따른다. 새 namespace는 해당 클래스 소유자가 분류할 때까지 production workload 배포를 허용하지 않는 것을 제안한다.

| Profile ID | 대상 workload 분류 | PSA 목표 | RuntimeClass | Kernel profile 목표 | 예외 |
|---|---|---|---|---|---|
| `restricted` | 애플리케이션/tenant namespace (`dev`, `database`, `iam` 등) | `enforce=restricted`, `audit=restricted`, `warn=restricted`; 세 label의 version을 동일한 승인 Kubernetes minor로 고정 | 생략 시 cluster default runtime. 격리 runtime은 별도 승인된 RuntimeClass만 허용 | seccomp `RuntimeDefault`; AppArmor `RuntimeDefault`는 노드 지원 시, 그 외 capability `unavailable` 기록 | namespace 전체 면제 금지 |
| `platform` | `monitoring`, `devtools`의 일반 platform workload | 기본 `baseline`; workload가 restricted 호환이면 restricted로 승격 | 기본 runtime; sandbox는 profile owner가 호환성 증거와 함께 선택 | seccomp `RuntimeDefault`; AppArmor 지원 상태 보고 | 특정 workload/service account 단위로 한정 |
| `node-agent` | `kube-system`, `cilium-secrets`, node exporter 등 호스트 통합이 필요한 시스템 구성요소 | PSA `privileged`가 필요한 namespace는 현재와 같이 명시하고, 개별 통제는 Kyverno/별도 정책으로 보완 | 지원 여부에 따라 명시. 미지원 조합은 `unavailable` | 예상 프로파일을 생략하지 말고 노드 지원 및 실제 적용 확인 | workload 이름, ServiceAccount, 필드, 사유, 만료일 명시 |
| `host-storage-agent` | `nfs-quota-agent`처럼 host filesystem을 관리하는 agent | 현재 `privileged` audit/warn 및 관련 Kyverno 예외를 유지하되, 좁은 범위로 전환하는 변경은 별도 검증 | 기본 RuntimeClass의 제약을 profile에 기록 | seccomp/AppArmor의 host 작업 호환성 검증 | 현재 privileged/hostPID/hostPath 사용은 명시 예외. `gitops/resources/kyverno-policies.yaml` 주석 참조 |

`PROPOSED` version pin은 cluster가 실제 지원하는 PSA version을 선택해야 한다. 구체 버전은 저장소에서 선언되지 않았으므로 여기서 숫자를 만들어 고정하지 않는다. PSA `enforce`는 admission 차단, `audit`는 audit annotation 기록, `warn`은 사용자 경고 목적이며, 현재 스크립트는 후자의 두 label만 설정한다.

## RuntimeClass 및 placement 계약 (PROPOSED)

각 사용 가능한 RuntimeClass inventory 항목은 다음 필드를 선언해야 한다. 현재 repo에는 이 inventory가 없다.

| 필드 | 확인 조건 |
|---|---|
| `name`, `handler` | API의 RuntimeClass 이름과 CRI handler가 실제 설정과 일치 |
| `owner`, `securityIntent`, `profileIDs` | 소유 팀, 격리 의도, 허용 workload profile이 비어 있지 않음 |
| `supportedNodes` | node label/OS/architecture/runtime version 조건을 명시하고 대상 node에서 handler 사용 가능 |
| `scheduling.nodeSelector`, tolerations | RuntimeClass 제약과 workload 제약의 교집합이 비어 있지 않음 |
| `overhead` | RuntimeClass `overhead.podFixed`를 requests/quota/placement 계산에 포함 |
| `lifecycle` | 승인 상태, 변경 이력, 제거 전 이전 계획 및 만료된 미사용 항목 탐지 |

Pod가 `runtimeClassName`을 지정하면 이름이 존재하고, 소속 namespace/profile이 사용을 허용하며, 스케줄 가능한 노드에서 handler가 지원되어야 한다. 불일치 또는 스케줄 대상 없음은 배포 차단 대상으로 제안한다. RuntimeClass는 컨테이너 보안 설정이나 PSS를 대신하지 않는다.

## SecurityContext 기본 및 effective state

| 항목 | 현재 상태 | 정책/확인 기준 |
|---|---|---|
| seccomp | Kyverno가 일부 namespace에서 Pod-level `RuntimeDefault`를 mutate한다. 기존 명시값을 덮어쓰는 동작은 `+(seccompProfile)` 앵커로 방지한다. | Pod spec의 pod-level 및 container override를 수집한다. 모든 init, app, ephemeral container의 실제 적용 결과가 기대값과 일치해야 한다. `Unconfined`, 잘못된 Localhost 경로, 확인 불가 상태는 실패 또는 capability `unavailable`로 보고한다. |
| AppArmor | 공통 GitOps 정책에서 기본값/검증을 확인하지 못했다. | 지원 노드에서는 `RuntimeDefault` 또는 승인된 `Localhost` profile을 명시하고 실행 상태를 확인한다. 미지원 노드는 `unavailable`로 명시하고 `restricted` workload 배치를 차단한다. |
| SELinux | 공통 GitOps 정책에서 profile/적용 검증을 확인하지 못했다. | SELinux 사용을 profile이 요구할 때 node/runtime 지원 및 실제 label 적용을 확인한다. 지원하지 않는 환경은 `not-applicable` 근거를 남긴다. |
| 권한 확장 | Kyverno privileged 정책은 privileged container를 검사한다. | `allowPrivilegeEscalation`, capabilities, `runAsUser`/`runAsNonRoot`, `hostNetwork`/`hostPID`/`hostIPC`, `hostPath`, host ports를 workload 전체(container/init/ephemeral 포함) 단위로 평가한다. 현재 저장소 정책이 각각을 전부 다룬다고 간주하지 않는다. |

Manifest admission 결과와 노드에서 관찰한 effective state는 서로 다른 증거다. RuntimeDefault mutate 성공만으로 seccomp가 실제 적용됐다고 판정하지 않는다. 관측 도구/CRI별 effective-state 수집 방법은 저장소에 구현되어 있지 않으므로 **PROPOSED verifier 입력**으로 둔다. 각 결과에는 cluster, node, kernel, kubelet, container runtime 및 버전, profile ID, workload UID, 관측 시각을 포함한다.

## 예외 절차 (PROPOSED)

1. workload owner가 예외 요청에 namespace/workload selector, ServiceAccount, 예외 필드, 필요 사유, 영향 profile, 대체 통제, 시작·만료일을 기록한다.
2. cluster/security owner가 selector가 정확히 필요한 workload만 일치하는지, 일반 tenant/service account를 포함하지 않는지 검토한다.
3. 정책 변경은 GitOps의 review 및 merge로 관리한다. 예외는 namespace 전체 제외보다 단일 workload/identity를 우선하며, 만료일 누락 또는 만료된 예외는 실패로 판정한다.
4. 배포 후 정상/위반 workload의 admission 결과와 effective-state evidence를 첨부한다. 예외 제거는 workload가 기본 profile로 통과한 후 수행한다.

현재 Kyverno 구성은 일부 예외가 namespace 단위(`kube-system`, `devtools` 등)이며 만료 metadata나 자동 만료 처리를 정의하지 않는다. 위 절차가 현재 자동화되어 있다고 간주하지 않는다. `nfs-quota-agent` 같은 기존 예외는 그 사실과 권한 사용 근거를 별도 추적한다.

## Conformance 판정 및 재현

각 workload의 conformance record는 다음 필드를 가진다.

```yaml
profileID: restricted | platform | node-agent | host-storage-agent
namespace: <actual namespace>
workload: <kind/name/uid>
admission: pass | fail | unavailable
psa: { enforce: <level>, audit: <level>, warn: <level>, version: <version-or-unpinned> }
runtimeClass: { name: <name-or-default>, handler: <handler-or-unknown>, node: <node> }
kernelProfiles:
  seccomp: supported | partial | unavailable | not-applicable
  apparmor: supported | partial | unavailable | not-applicable
  selinux: supported | partial | unavailable | not-applicable
effectiveState: pass | fail | unavailable
drift: none | detected
context: { cluster: <id>, node: <name>, kernel: <version>, kubelet: <version>, runtime: <name/version>, observedAt: <timestamp> }
evidence: [<offline bundle-relative paths>]
```

전체 workload 판정은 다음 조건을 모두 만족할 때만 `pass`다: namespace/profile mapping 존재, PSA target/version 충족, 해당 Kyverno 정책 위반 없음, RuntimeClass/handler/node 조합 지원, 예상 securityContext와 effective state 일치, 증거가 같은 workload UID 및 node/runtime context에 결부됨. 지원이 불가능한 필수 capability는 `unavailable`로 분리하고 pass로 승격하지 않는다. 선택 capability는 profile이 요구하지 않는다는 근거가 있어야 `not-applicable`이다. 기대값과 관측값 불일치, 미분류 production workload, 잘못된 Localhost profile, 미승인 RuntimeClass 또는 만료 예외는 실패/드리프트로 보고한다.

| Check | 실행/입력 | 통과 기준 |
|---|---|---|
| 선언 정적 검사 | GitOps namespace/Pod/RuntimeClass/ClusterPolicy YAML 및 profile record를 오프라인 bundle에 고정 | 모든 대상에 profile mapping; PSA level/version과 policy 예외가 파싱 가능; 없는 RuntimeClass 참조 없음 |
| PSA admission | 테스트 namespace에서 profile별 허용/위반 Pod를 API server에 제출 | 허용 케이스 통과, 위반 케이스는 지정 `enforce`에서 거부; audit/warn 결과는 각각 증거로 수집 |
| Kyverno | `gitops/resources/kyverno-policies.yaml` 정책으로 정상 및 privileged/host namespace/port/예외 범위 사례 replay | Enforce 정책은 부적합 사례 거부; Audit 정책은 거부했다고 오판하지 않고 report 기록; namespace 제외가 영향 범위로 기록 |
| RuntimeClass placement | 각 handler가 존재하는 node 집합과 RuntimeClass selector/overhead, workload requests 및 ResourceQuota 입력 비교 | 적어도 한 적격 node가 있고 overhead 반영 후 quota/placement 가능; 지원되지 않는 node는 배치되지 않음 |
| Effective runtime | 실행 중 workload의 CRI/node 관측값을 동일 UID와 manifest 기대값에 결합 | seccomp/AppArmor/SELinux 실제 값이 profile과 일치; 관측 불가 시 `unavailable`로 판정 |
| Drift 및 offline replay | GitOps snapshot, policy bundle, node/runtime capability snapshot, 기대 profile, 관측 evidence를 bundle에 포함해 동일 입력으로 재실행 | 입력 hash와 판정이 재현되고, 기대/관측 차이는 drift로 출력 |

위 검사는 **PROPOSED acceptance procedure**다. 현재 저장소에는 이 전체 bundle 생성기나 runtime effective-state 검사기가 없으며, 이 문서는 그런 기능이 있다고 주장하지 않는다. 예외 승인/만료, PSA version skew, ephemeral container, node별 runtime 차이는 별도 사례로 테스트해야 한다. 보안 profile drift는 #101 추적 체계에 연결하고, 승인된 evidence는 #125 컴플라이언스 evidence로 연결한다.

## 범위 경계

이 정책은 Kubernetes PSS/PSA, RuntimeClass, seccomp, AppArmor 또는 SELinux 구현을 대체하지 않는다. kernel/runtime 자체 구현, gVisor/Kata 설치, 상용 workload security 제품 기능은 범위 밖이다. 이슈가 요구하는 RuntimeClass inventory, PSS enforce, effective kernel profile verification, drift quarantine 및 offline conformance는 현재 GitOps에서 확인되지 않은 **구현 과제**다.
