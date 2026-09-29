# ADR: Cluster Lifecycle 관리 방식

- 상태: 채택
- 결정일: 2026-09-29
- 범위: Narwhal의 VM/인프라와 kubeadm 기반 Kubernetes 노드 수명주기
- 결정: 당장은 `PROVIDER` 분기와 OpenTofu/Vagrant 실행 구조를 유지하고, 그 위에 명시적 lifecycle 계약을 추가한다. CAPI는 도입하지 않는다. provider별 어댑터 경계와 상태 계약은 아래처럼 설계하며, CAPI 전환 조건이 충족되면 재검토한다.

## 배경과 현재 동작

현재 저장소는 하나의 클러스터 API가 아니라 두 실행 경로를 가진다. `Vagrantfile`이 로컬 VM과 provisioner 순서를 관리하고, Kakao 경로는 `csp/kakao-cloud/terraform/`의 OpenTofu 상태 및 `scripts/cloud/provision-kakao.sh`가 리소스 출력, SSH 전달, 단계 실행을 관리한다. 후자는 `base`, `mirror`, `init`, `join`, `phase1`, `phase2` 등 단계를 노드별 `.narwhal-stage/` sentinel로 건너뛰고 `FORCE=1`로 재실행한다.

공통 클러스터 구성은 `scripts/cluster/02-init-cluster.sh`, `02-join-control-plane.sh`, `02-join-worker.sh`와 이후 단계 스크립트가 맡는다. `PROVIDER=kakao` 분기는 VIP 인터페이스, 노드 주소, join artifact 전달 같은 환경 차이를 처리한다. Vagrant 경로의 `scripts/up.sh`와 Kakao runner는 서로 다른 재시도/단계 관리 구현이다. `configs/cluster.env`는 현재 `BASE_DOMAIN`만 선언하므로 issue가 암시하는 lifecycle 설정 인벤토리는 아직 없다.

`02-init-cluster.sh`는 기존 `/etc/kubernetes/admin.conf`가 있으면 kubeadm init을 건너뛰고 join 명령을 재생성한다. `02-join-worker.sh`는 `/etc/kubernetes/kubelet.conf`가 있으면 join을 건너뛴다. 이는 각 동작의 제한된 멱등성 증거이지, 모든 lifecycle 작업의 재시도 보장은 아니다. `cluster-heal.sh`는 master-1 부팅 후 API/노드 상태를 기다리며 일부 비정상 pod를 best-effort 정리하지만, 노드 교체나 인프라 복구 controller는 아니다.

저장소 내 CAPI `Cluster`/`Machine` 리소스, CAPI controller 설치, infrastructure provider 구현은 확인되지 않았다. 이 ADR은 CAPI를 현재 기능인 것처럼 표현하지 않는다. Velero는 `docs/vagrant/operations.md`에 백업/복원 절차가 있고 `gitops/charts/narwhal-apps`에서 배포되지만, Kubernetes/control-plane upgrade 전후 backup hook 또는 자동 rollback 실행기는 확인되지 않는다. `docs/common/upgrade-orchestration.md`도 Kubernetes upgrade 실행을 이 pilot 범위 밖으로 명시한다.

## 결정 기준

가중치가 아닌 필수 통과 기준으로 평가한다. RFP의 수 분 내 배포 목표는 현재 측정치가 아니므로 숫자 목표를 임의로 계약하지 않는다.

| 기준 | 통과 조건 | 현재 근거/격차 |
|---|---|---|
| Provider 경계 | Vagrant와 Kakao의 VM 생성/삭제, 주소 및 접근 경로를 lifecycle 호출자가 provider 분기 없이 사용할 수 있음 | 현재는 `Vagrantfile`, OpenTofu, `provision-kakao.sh`가 분리됨 |
| 멱등성/재개 | 작업 ID와 원하는 topology가 같을 때 재시도해도 중복 노드/삭제가 없고, 실패 단계부터 재개 가능 | 일부 init/join guard 및 Kakao sentinel만 존재 |
| 건강 게이트 | 단계 전환 전 API readiness, etcd quorum, 노드 Ready, 핵심 workload 상태를 구분해 판정하고 실패 시 후속 단계 중단 | `cluster-heal.sh`의 best-effort 대기는 lifecycle 전이 게이트가 아님 |
| air-gap | 필요한 OS 패키지, 실행 바이너리, 이미지, 차트와 controller/provider 의존성을 반입하고 격리 검증 가능 | `scripts/airgap/` 번들은 현재 OS 패키지/바이너리/이미지/차트를 다룸. CAPI 구성요소는 포함 근거 없음 |
| 데이터 안전 | decommission과 upgrade는 백업/복구 가능성을 확인하고, 데이터가 포함된 자원은 명시적 보호 절차가 있음 | Velero 절차 존재. 인프라 state와 영속 볼륨 보존 정책은 작업별로 다름 |
| 운영부담/가역성 | 현재 실행 경로를 유지하며 단계적으로 바꾸고 provider controller 장애 시 기존 접근/복구 경로 유지 | 현 provider 경로는 운영 중이며 CAPI 도입 시 별도 controller와 provider 조합 필요 |

## 대안 비교

| 항목 | 기존 provider 추상화 확장 (선택) | CAPI 채택 |
|---|---|---|
| 제어 방식 | Vagrant/OpenTofu가 인프라를 만들고, 공통 lifecycle runner가 기존 shell 단계를 명시적 상태/게이트로 감싼다 | Kubernetes API의 CAPI 리소스와 controller reconciliation을 중심으로 cluster/machine 수명주기를 관리한다. 인프라별 infrastructure provider와 bootstrap/control-plane provider 조합도 운영해야 한다 |
| 현 코드 재사용 | 높음: `PROVIDER` 분기, kubeadm 스크립트, Kakao runner를 adapter 뒤에 단계적으로 수용 | 제한적: kubeadm 설정과 후속 플랫폼 스크립트 일부를 재사용할 수 있어도 VM, bootstrap, control-plane ownership 연결 및 전환 설계가 필요 |
| lifecycle 일관성 | 공통 계약/상태 저장/오류 규칙을 별도 구현해야 함 | reconciliation이 원하는 상태와 관측 상태를 연결하는 기본 모델을 제공하지만 Narwhal의 모든 단계/정책이 자동으로 해결되는 것은 아님 |
| air-gap 영향 | 현 번들 확장: 새 runner 의존성만큼 반입 및 검증 | CAPI core 및 각 provider/controller 이미지, CRD/manifest, 버전/호환성 자료를 번들에 추가하고 controller가 설치 전에 로컬에서 동작하는지 검증해야 함. 현재 bundle 완전성 검사에는 CAPI 입력이 없음 |
| 소유권/위험 | OpenTofu state와 Vagrantfile이 계속 VM source of truth. 새 controller가 동일 리소스를 동시에 소유하지 않음 | 기존 OpenTofu와 CAPI가 같은 VM을 관리하면 drift/경합/의도치 않은 삭제 위험. resource import/ownership handoff가 필요 |
| 판정 | 현재 제품 요구의 첫 단계로 적합. controller 도입 없이도 lifecycle 계약과 회귀 검증을 만들 수 있음 | 향후 여러 provider/다중 클러스터가 Kubernetes API 기반 self-service를 요구하고, 지원 provider 및 air-gap 패키징이 검증된 때 재검토 |

## 수명주기 계약 (제안)

아래는 구현된 기능 설명이 아니라 다음 구현이 따라야 할 **PROPOSED 계약**이다. 외부 API, 새 환경 변수, Kubernetes namespace를 현재 존재하는 것처럼 정의하지 않는다. durable 상태 저장 위치와 사용자 호출면은 별도 설계 항목이다.

### 상태와 전이

각 실행은 불변 `operation_id`, `cluster_id`, `operation` (`Create|Register|Scale|Upgrade|Heal|Decommission`), 요청 topology/version, 현재 단계, 시작/종료 시각, 시도 횟수, 마지막 오류 코드/단계 참조를 기록한다. 비밀값과 join token은 상태 기록에서 제외한다.

| 작업 | 상태 순서 | 성공 조건 |
|---|---|---|
| Create | `Requested → InfrastructureProvisioning → InfrastructureReady → ControlPlaneBootstrapping → ControlPlaneReady → WorkersJoining → ClusterHealthy → Succeeded` | 요청한 노드 수가 등록되고 아래 health gate 통과 |
| Register | `Requested → AccessPreflight → IdentityRecorded → HealthChecked → Succeeded` | 명시적 cluster identity와 대상 API 연결/인증/CA 검증 완료. 기존 cluster를 수정하거나 재생성하지 않음 |
| Scale | `Requested → CapacityCheck → NodesProvisioning → NodesJoining` 또는 `NodesDraining → NodesRemoving → InfrastructureRemoving → ClusterHealthy → Succeeded` | 증설은 새 노드 Ready 확인, 축소는 drain 및 보호된 workload/PDB 확인 후 대상 인프라만 제거 |
| Upgrade | `Requested → Preflight → BackupRequested → BackupVerified → ControlPlaneRolling → WorkersRolling → PostUpgradeGate → Succeeded` | 버전 skew와 순차 진행 정책 통과, backup이 `Completed`이고 대상 범위/복구 가능성 확인, 사후 gate 통과 |
| Heal | `Requested → Diagnose → Repairing → HealthGate → Succeeded` | 원인과 대상 자원 식별 후 최소 범위 복구 및 health gate 통과. 무조건적인 노드/데이터 삭제 금지 |
| Decommission | `Requested → OwnershipCheck → BackupDecision → WorkloadDrain → ClusterRemoval → InfrastructureRemoval → Succeeded` | 대상 식별과 백업 보존 결정을 기록하고 의존 리소스/공유 자원을 보호한 뒤 제거 확인 |

모든 작업은 `FailedRetryable`, `FailedTerminal`, `BlockedHealthGate`, `Cancelled` 중 하나로 종료될 수 있다. `FailedRetryable`은 transport timeout, 일시적인 API 미가용처럼 동일 입력 재시도가 안전한 오류에만 부여한다. 설정/인증/버전 불일치, 소유권 충돌, backup 미검증, health gate 실패는 자동 진행을 멈추고 `FailedTerminal` 또는 `BlockedHealthGate`로 남긴다. 재시도는 같은 `operation_id`와 단계별 완료 marker를 사용하며, 완료 증거가 불명확하면 같은 작업을 재실행하지 않고 관측 상태부터 재조정한다. 삭제 작업은 자동 재시도 전에 실제 리소스 소유자를 재확인한다.

### Health gate

`ClusterHealthy` 판정은 다음을 각각 관측해야 한다. 기본 topology 및 workload 수치는 구현 시 provider/클러스터 profile에서 입력받으며 본 ADR은 새 숫자를 기본값으로 고정하지 않는다.

1. API endpoint에서 인증서 검증을 포함한 `/readyz` 성공.
2. control-plane/etcd 멤버가 요청 topology와 일치하고 etcd quorum이 유지됨.
3. 요청된 모든 노드가 Kubernetes `Ready=True`; 미등록/중복 node identity가 없음.
4. 변경 대상 workload가 모두 Ready이며, 기존 upgrade gate가 정한 관찰 기간 중 새 CrashLoop/가용성 저하가 없음.
5. Upgrade는 API version, node version, CNI 및 핵심 플랫폼 gate가 목표 버전/정책을 만족. Backup은 해당 변경을 복구할 범위로 성공했음.

이 중 하나라도 확인 불가이면 성공으로 간주하지 않고 다음 단계 실행을 중지한다. `cluster-heal.sh`처럼 오류를 기록하고 0으로 종료하는 best-effort 동작은 gate evaluator가 성공 결과로 해석해서는 안 된다.

### Provider adapter 경계

**PROPOSED:** 공통 runner는 `cluster_id`, provider 식별자, master/worker topology, 네트워크/CIDR, Kubernetes 목표 버전, air-gap bundle 참조와 작업 ID를 입력받는다. adapter 책임은 `validate`, `plan`, `provision_nodes`, `discover_nodes`, `remove_nodes`, `destroy_cluster_infrastructure`이며 각 응답에 대상 자원 ID와 완료/오류 증거를 포함한다. 실제 구현은 기존 Vagrant 호출과 Kakao의 OpenTofu 출력/SSH runner를 감싸며, shell script가 OpenTofu/Vagrant state를 직접 대체하지 않는다. 공통 runner는 단계 상태, 멱등 키, health gate, backup 정책, 감사 기록을 소유한다.

`PROVIDER`는 현재 `vagrant` 기본값과 `kakao` 분기를 가진 코드상의 구분이다. 이를 새 범용 provider API나 `PROVIDER=vagrant|kakao` 외 값까지 지원하는 계약으로 간주하지 않는다. provider별 네트워크/LB/DNS 차이와 플랫폼 GitOps 소유권은 adapter 및 기존 플랫폼 경계에 남긴다.

## Air-gap 결정

air-gap은 CAPI 선택 여부와 별개인 필수 설치 경로다. `scripts/airgap/README.md`, `scripts/airgap/09-verify-bundle-completeness.sh`, `Vagrantfile`, `scripts/cloud/provision-kakao.sh`는 각각 이미지/차트/바이너리/apt 패키지 번들, completeness gate, 로컬 마운트 또는 Kakao 전달 및 mirror 구성을 보여준다. Kakao의 기본 `AIRGAP=0`은 프록시 설치 경로이며 격리망 검증과 동일하지 않다.

CAPI를 도입하는 경우 controller 이미지와 CRD/manifest 및 그 의존 이미지가 같은 반입·digest/SBOM·완전성 검증 경로에 추가되어야 한다. controller를 클러스터에 넣기 전에 provider 리소스를 생성해야 하는 bootstrap 순서가 성립하는지도 폐쇄망에서 검증해야 한다. 온라인 registry 조회가 남거나 필요한 provider 바이너리가 누락되면 해당 air-gap 시나리오는 배포 불가로 판정한다. 이 조건은 현재 충족됐다는 주장이 아니라 도입 전 필수 acceptance 조건이다.

## 마이그레이션과 재검토 조건

1. **계약 먼저:** 현재 shell 단계별 선행조건, 출력 artifact, 재실행 안전성, 실패 코드를 목록화한다. `configs/cluster.env`에 이미 정의된 값만 그대로 참조하고 새 lifecycle 필드는 별도 승인/구현 전까지 PROPOSED로 둔다.
2. **공통 runner pilot:** 우선 기존 경로를 호출하는 lifecycle 상태 기록, operation 중복 방지, 단계별 health gate와 오류 분류를 구현한다. 초기에는 Vagrant create와 Kakao create를 각각 별도 검증하고 `Scale`, `Upgrade`, `Heal`, `Decommission`을 지원한다고 광고하지 않는다.
3. **회귀/운영 검증:** 최소 acceptance는 HA control-plane 3대, worker 증설/축소, 중간 단계 재시도, API/노드 health gate 실패, upgrade 전후 Velero backup 검증과 실패 중단, 대상 외 인프라 보존 확인이다. 격리망은 완성 bundle만으로 설치/복구되는지 별도 수행한다. issue의 자동검증 요구는 이 ADR만으로 충족되지 않는다.
4. **CAPI 재검토:** 서로 다른 지원 대상에서 동일 lifecycle API/self-service가 실제 요구되고, 수동 단계 조정이 반복 운영 비용/오류 원인으로 측정되며, 목표 provider의 CAPI provider 구현·지원 버전·폐쇄망 배포·기존 리소스 import 경로를 검증할 수 있을 때 새 ADR로 결정한다.
5. **가역 전환:** CAPI pilot은 새 disposable cluster에서 시작한다. 동일 VM/클러스터를 OpenTofu와 CAPI가 동시에 관리하지 않는다. 기존 cluster 전환은 소유권 handoff와 state backup/복구 절차, 무중단/재생성 여부가 입증되기 전 허용하지 않는다. 실패 시 CAPI controller가 기존 리소스를 reconcile하지 않게 한 후 기존 Vagrant/OpenTofu + shell 운영 경로로 복귀한다. 실제 import 및 rollback 절차는 미검증이므로 전환 조건으로 남긴다.

## 결과 및 미결 사항

- 단기적으로 익숙하고 검증된 provider별 실행기를 보존하며, 사용자 요구를 state/gate/retry 계약으로 먼저 정리할 수 있다.
- runner 상태 저장소, lifecycle 진입점, `cluster_id`와 topology의 source of truth, operation 동시성 제어는 구현 설계가 필요하다.
- 자동 rollback은 일반적인 노드/클러스터 복구로 가정하지 않는다. Upgrade는 backup 확인 후 실패 시 정지하고, 복구는 명시적 restore 또는 이전 버전 절차가 지원되는 경우에만 수행한다.
- 자동화된 3-control-plane 및 worker 증감/upgrade/heal 검증, backup 복구 검증, 실측 배포시간, CAPI air-gap completeness는 미수행이다.
- Portal/Next.js lifecycle UI/API, 새 외부 API나 환경 변수, CAPI 설치/운영, 다른 클라우드 provider, Velero의 cluster 전체 복구 능력 주장은 이 결정 범위 밖이다.
