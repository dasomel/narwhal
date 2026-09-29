# Control Plane Static Pod Manifest / HostPath 보호 정책

- 상태: 현행 동작과 제안 정책을 분리한 설계 기준
- 범위: kubeadm control plane의 host-side static Pod manifest 무결성, hostPath 검토, API 가시성 대조
- 저장소 근거: `scripts/cluster/02-init-cluster.sh`, `scripts/cluster/02-join-control-plane.sh`, `scripts/cluster/harden-node-files.sh`, `docs/common/compliance-hardening.md`

## 1. 기준과 한계

Static Pod의 실행 원본은 API object가 아니라 노드의 kubelet manifest 경로다. 따라서 API에서 mirror Pod가 없거나 삭제됐다는 사실만으로 host workload가 없다고 결론 내리지 않는다. `docs/common/compliance-hardening.md`에도 kubelet이 `/etc/kubernetes/manifests/`의 모든 파일을 읽으며 백업 파일이 실제 manifest를 shadow할 수 있다고 기록돼 있다.

이 문서는 해당 경로의 파일 보호 기준을 정의한다. 현재 스크립트가 실제로 보장하는 수준과 아직 구현되지 않은 통제를 구분한다. 이 저장소에서 확인되지 않은 자동 탐지기, alert 경로, SELinux/AppArmor 프로파일은 구현된 것으로 간주하지 않는다.

## 2. Host-side inventory

### Static Pod manifest 입력

| 노드/경로 | 기대 workload | 근거 및 조건 |
|---|---|---|
| 각 control-plane 노드 `/etc/kubernetes/manifests/etcd.yaml` | `etcd` | kubeadm stacked-etcd control plane 기준. `02-init-cluster.sh`의 kubeadm `ClusterConfiguration`이 별도 external-etcd endpoint를 설정하지 않음. **ASSUMPTION:** 현재 문서화된 기본 배포는 stacked etcd다. |
| 각 control-plane 노드 `/etc/kubernetes/manifests/kube-apiserver.yaml` | `kube-apiserver` | kubeadm control plane. audit/encryption 설정은 `02-init-cluster.sh`의 apiserver `extraArgs` 및 `extraVolumes`로 생성된다. |
| 각 control-plane 노드 `/etc/kubernetes/manifests/kube-controller-manager.yaml` | `kube-controller-manager` | kubeadm control plane. |
| 각 control-plane 노드 `/etc/kubernetes/manifests/kube-scheduler.yaml` | `kube-scheduler` | kubeadm control plane. |
| Vagrant control-plane 노드 `/etc/kubernetes/manifests/kube-vip.yaml` | `kube-vip` | `02-init-cluster.sh`/`02-join-control-plane.sh`의 Vagrant 경로에서 생성. Kakao 경로는 LB 사용으로 생성하지 않는다. |

재고 기준은 디렉터리 안의 **모든 항목**이다. kubelet은 확장자 필터 없이 파일을 감시하므로 `.bak`, 임시 파일, editor swap, 알 수 없는 하위 디렉터리도 검토 대상이다. 승인된 manifest 이름 외 항목은 drift로 분류한다. manifest backup은 이 디렉터리 바깥에 보관한다.

### kubeadm control-plane hostPath mount 인벤토리

| Pod | Host path | Mount path | 읽기/쓰기 | 근거 |
|---|---|---|---|---|
| kube-apiserver | `/etc/kubernetes/enc` | `/etc/kubernetes/enc` | read-only | `02-init-cluster.sh`의 `extraVolumes.enc` |
| kube-apiserver | `/etc/kubernetes/audit` | `/etc/kubernetes/audit` | read-only | `02-init-cluster.sh`의 `extraVolumes.audit-policy` |
| kube-apiserver | `/var/log/kubernetes/audit` | `/var/log/kubernetes/audit` | writable | `02-init-cluster.sh`의 `extraVolumes.audit-log` |
| etcd | `/var/lib/etcd` | `/var/lib/etcd` | writable | kubeadm stacked-etcd 기본 manifest. **ASSUMPTION:** 기본 kubeadm 생성값이며 repo에 별도 `etcd.local.extraVolumes`가 없음. |
| kube-vip (Vagrant만) | host network 사용; 이 repo 생성 manifest에서 hostPath mount 없음 | — | — | `02-join-control-plane.sh`의 kube-vip manifest |

정확한 실제 inventory는 각 노드 manifest의 `spec.volumes[].hostPath.path`, `spec.containers[].volumeMounts[].mountPath` 및 `readOnly` 값을 수집해 이 기준과 비교해야 한다. 기본 inventory에 없는 경로, 읽기 전용에서 쓰기로의 변경, `DirectoryOrCreate`/`FileOrCreate` 타입 변경은 승인 전 보안 drift다. kubelet이 hostPath를 막지 않으며 API admission은 host에서 직접 읽는 static Pod manifest를 통제하지 못한다.

## 3. 현행 강제 수준

| 통제 | 현재 repo에서 관찰한 동작 | 판정 |
|---|---|---|
| Manifest 파일 mode | `scripts/cluster/harden-node-files.sh`가 `/etc/kubernetes/manifests/*.yaml`에 `chmod 600` 실행. 디렉터리 자체와 소유자/그룹은 바꾸지 않음. 오류는 non-fatal 처리될 수 있음. | 부분 적용 |
| 적용 시점 | `docs/common/compliance-hardening.md`는 live 적용이며 fresh install 후 자동 재적용되지 않는다고 명시. 초기화/조인 스크립트에서 hardening 스크립트 호출은 확인되지 않음. | 지속성 미보장 |
| 소유권 및 디렉터리 | manifest 파일 `root:root`, 디렉터리 mode/owner를 검증·설정하는 검사 없음. | 미구현 |
| SELinux/AppArmor | manifest 경로 context baseline/검증 또는 control-plane static Pod 전용 profile 없음. | 미구현 |
| API hostPath admission | `gitops/resources/narwhal-portal-policy.yaml`은 portal SA의 hostPath를 제한하지만 control-plane 정책은 아님. static Pod 자체에도 적용되지 않음. | 범위 제한 |
| 무결성/drift 및 hash | manifest 전용 hash baseline, actor 기록, watcher/alert/evidence 수집기 없음. | 미구현 |
| Mirror/API 대조 | 이 slice 범위의 host manifest 대 API inventory 대조 verifier 없음. | 미구현 |
| 복구/rollback | 기존 `docs/vagrant/disaster-recovery.md`는 수동 manifest 이동 및 etcd 복구 절차를 포함하나 last-known-good hash 검증/actor 증적 절차는 정의하지 않음. | 부분 runbook |

주의: `harden-node-files.sh`의 주석은 kubeadm이 파일을 기본 0600으로 만든다고 설명하지만, 이를 해당 버전/모든 provision 경로에서 자동 검증하는 테스트로 간주하지 않는다.

## 4. 제안 baseline과 판정 규칙 (PROPOSED)

아래는 새로 요구하는 상태 기준이며 현행 자동 enforcement를 뜻하지 않는다.

### 파일시스템 보호

| 대상 | 요구 baseline | 실패 조건 |
|---|---|---|
| `/etc/kubernetes/manifests` 디렉터리 | `root:root`, mode `0700` | owner/group 불일치 또는 group/other 접근 비트 존재 |
| 승인된 각 manifest 파일 | `root:root`, mode `0600`, 일반 파일, symlink 아님 | owner/group/mode 불일치, symlink/non-regular, 허용 목록 밖 파일 |
| SELinux/AppArmor | 배포 OS가 지원하는 경우 실제 label/profile을 inventory하고 노드별 승인 baseline과 비교 | 비어 있거나 예상과 다른 label/profile. **PROPOSED:** OS별 구체 type/profile 값은 플랫폼 검증 후 별도 승인한다. |

변경 권한은 root를 획득한 노드 관리 경로로 한정한다. API RBAC에서 `nodes/proxy`를 통한 kubelet 접근은 `docs/common/compliance-hardening.md`의 #113 범위와 교차 검토한다. root 또는 control-plane 노드가 침해된 경우 이 baseline 자체를 우회할 수 있으므로, 노드 외부에 보관된 승인 hash/감사 증적이 필요하다.

### hostPath 검토 및 API admission

1. **Static Pod:** kubelet이 host manifest를 직접 실행하므로 Kyverno/Pod Security Admission으로 이 입력을 보호할 수 없다. host filesystem 보호, 파일 integrity 검증, host-side inventory가 통제 지점이다.
2. **일반 API Pod:** `hostPath` 사용을 기본 거부하는 admission 정책을 **PROPOSED** 한다. 예외는 명시된 workload identity/namespace 및 정규화된 경로 allowlist, 최소 read-only mount로 제한한다. `pathPrefix`만 비교하지 말고 symlink 및 path traversal을 포함한 실제 host 해석 경로를 검사할 수 있어야 한다.
3. 현재 `narwhal-portal-policy.yaml`은 portal Pod의 hostPath 차단 사례일 뿐 cluster-wide allow/deny 정책의 증거가 아니다. 플랫폼은 hostPath 의존성이 존재하므로 blanket allow는 적합하지 않다. 예외 inventory에는 workload, namespace, host path, mount path, readOnly, 목적, 승인자/만료 시점을 기록한다.

### Drift 이벤트와 offline evidence (PROPOSED)

노드별 검사 결과는 다음 필드를 남긴다.

| 필드 | 의미 |
|---|---|
| `node`, `observedAt`, `collectorVersion` | 관찰 위치/시각 및 검사기 식별 |
| `path`, `kind`, `uid`, `gid`, `mode`, `securityContext` | 파일/디렉터리 속성과 OS가 제공하는 SELinux/AppArmor label/profile |
| `sha256`, `baselineSha256` | 현재 bytes와 승인 버전 hash. hash는 manifest 파일별로 기록 |
| `classification`, `reason` | `PASS`, `DRIFT`, `UNKNOWN`, `ERROR` 및 차이 설명 |
| `actor`, `changeReference`, `beforeSha256`, `afterSha256` | 변경 주체/작업 참조. 확인할 수 없는 값은 추정하지 말고 `unknown` |

판정은 `PASS` = 파일 집합, hash, owner/mode/context가 모두 승인 baseline과 일치; `DRIFT` = 승인 집합 외 파일 또는 값/hash 변경; `UNKNOWN` = 수집 결과와 mirror/API 대조가 불완전하여 결론 불가; `ERROR` = 경로 열람/해시 계산 실패로 증적을 만들지 못함이다. `UNKNOWN`/`ERROR`를 PASS로 낮추지 않는다. manifest 변경은 configuration drift와 security event 양쪽으로 기록한다. Offline 검증은 노드에서 외부 API/network 없이 파일 목록, 속성, hash를 계산하고 서명되었거나 보호된 baseline과 비교한 결과를 보존해야 한다.

## 5. Mirror Pod 및 API-visible 차이 처리 (PROPOSED)

대조 키는 `node + namespace + pod name`으로 한다. host manifest의 metadata에 namespace가 없으면 API-visible 여부를 가정하지 말고 `UNKNOWN`/검토 필요로 기록한다. kubeadm control-plane manifests는 일반적으로 `kube-system` mirror Pod 이름을 사용하며 repo의 복구 절차도 `etcd-master-1` 등을 조회하지만, 이 규칙을 임의 manifest의 namespace 검증으로 일반화하지 않는다.

| Host 관찰 | API 관찰 | 분류/조치 |
|---|---|---|
| 승인 host manifest 있음 | 대응 mirror 있음 | hash/속성 검사가 통과하면 일치. mirror 존재만으로 manifest 무결성을 증명하지 않음 |
| 승인 host manifest 있음 | mirror 없음 | `DRIFT` 또는 API 장애 시 `UNKNOWN`; host CRI/runtime와 kubelet 상태를 함께 확인. API에서 mirror를 삭제해도 실제 static Pod 제거 증거가 아님 |
| 승인 host manifest 있음 | 같은 키의 spec 불일치 mirror | `DRIFT`; host manifest가 실행 원본이므로 양쪽 spec/hash를 보존 |
| 승인 manifest 없음 | control-plane/API inventory에 Pod 있음 | `DRIFT`; 일반 workload 가능성을 검토하고 자동 삭제하지 않음 |
| 승인 manifest 외 host manifest 또는 malformed YAML | API에서 없음/파싱 불가 | `DRIFT`; 파일명, hash, parser 오류를 보존하고 격리/복구 전 실행 여부를 CRI에서 확인 |
| host 또는 API 수집 실패 | 상대 데이터만 있음 | `UNKNOWN` 또는 `ERROR`; 부재를 증명한 것으로 처리하지 않음 |

API-visible workload의 기대 집합은 Kubernetes API의 Pod 목록만으로 정의하지 않는다. 승인된 control-plane static manifest inventory와 운영상 승인된 일반 workload inventory를 함께 사용한다. `namespace` 필드가 누락/비정상인 manifest, 승인되지 않은 이름, YAML decode 실패는 별도 finding으로 남긴다. 자동 격리나 삭제는 control-plane 가용성 영향이 있으므로 이 문서만으로 수행하지 않는다.

## 6. 승인 변경 및 복구 증적 (PROPOSED)

manifest 갱신 전 변경 참조, 영향 노드, 승인자, 이전 hash, 계획 hash, 변경 이유를 노드 외부 기록에 남긴다. 편집은 임시 파일을 manifest 디렉터리 밖에서 수행하고 검증 후 원자적으로 교체한다. 교체 뒤 owner/mode/context/hash를 다시 기록하고 API mirror와 실제 runtime health를 확인한다. kubelet은 디렉터리의 임의 파일도 감시하므로 backup/temp를 경로 안에 두지 않는다.

불일치 또는 노드 침해 의심 시 제안 절차:

1. 해당 control-plane 노드의 manifest 디렉터리 목록, 속성, hash, kubelet journal, runtime container ID 및 시각을 보존한다. API 접근 불가도 별도 기록한다.
2. `docs/common/compliance-hardening.md`의 #111 privileged access 절차 및 노드 격리 절차를 적용한다. **이 repo에서 검토한 근거에는 이 manifest 전용 자동 containment 절차가 없다.**
3. 독립 보관된 last-known-good manifest를 서명/hash 검증하고, 격리 상태에서 승인된 복구 경로로 복원한다. 출처/hash 검증 실패 시 복원하지 않고 새 노드 재구축을 우선 검토한다.
4. kubelet reconciliation 이후 각 control-plane static Pod의 runtime 상태, `/readyz`, etcd health/quorum, mirror/API 대조, 복원 후 hash를 기록한다. `docs/vagrant/disaster-recovery.md`의 etcd snapshot 복구는 etcd 데이터 복구 참고 자료이며 manifest integrity verifier/rollback 테스트를 대신하지 않는다.
5. 증적에는 노드, actor/context(확인 불가하면 unknown), 시간, before/after hash, 파일 속성, 승인/incident 참조, 복구 결과 및 health 결과를 포함한다.

이 저장소에는 last-known-good manifest 저장소, 자동 rollback, tamper alert, 위 절차의 통합 검증이 없으므로 acceptance criteria 중 해당 항목은 현재 미충족이다. 이 문서는 요구 기준을 정한 것이며 구현 완료를 선언하지 않는다.
