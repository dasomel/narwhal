# Control-plane etcd 운영 기준

## 범위와 현재 상태

이 문서는 kubeadm이 control-plane 노드에 static Pod로 배치하는 **Kubernetes control-plane etcd**의 운영 기준이다. `gitops/charts/narwhal-platform/templates/apisix-infra.yaml`의 `apisix-etcd`는 별도 단일 멤버 애플리케이션 저장소이므로 이 기준의 quorum 및 member 교체 절차 대상이 아니다. APISIX etcd 복구는 [`apisix-etcd-recovery.md`](apisix-etcd-recovery.md)를 따른다.

| 영역 | 저장소에서 확인되는 상태 | 운영상 의미 |
|---|---|---|
| 토폴로지 | `scripts/cluster/02-init-cluster.sh`의 `MASTER_COUNT` 기본값은 3이며 `scripts/cluster/02-join-control-plane.sh`가 `kubeadm join --control-plane`으로 추가 노드를 붙인다. `docs/common/architecture.md`는 3-member etcd, quorum 2/3을 기술한다. | 현재 표준은 stacked etcd 3개 멤버다. 5-member까지의 production 요구는 **PROPOSED**이며 현재 자동 검증되지 않는다. |
| TLS/접근 | `scripts/test/verify-cluster.sh`와 `scripts/verify/etcd-encryption-check.sh`는 `https://127.0.0.1:2379`, `/etc/kubernetes/pki/etcd/ca.crt`, `server.crt`/`server.key`로 로컬에서 `etcdctl`을 실행한다. | 저장소 검증 경로는 TLS를 사용하지만 외부 접근 제한 및 모든 peer/client 플래그의 런타임 감사는 확인되지 않는다. |
| 암호화와 키 | `scripts/cluster/02-init-cluster.sh`는 API 서버 Secret 암호화를 설정하고, `scripts/cluster/02-join-control-plane.sh`는 같은 encryption config를 새 master에 복사한다. | 저장 데이터 암호화는 etcd peer TLS와 다른 통제다. 해당 키 수명주기는 `docs/common/compliance-hardening.md` 및 `scripts/ops/rotate-etcd-encryption-key.sh` 범위다. |
| 모니터링 | `gitops/charts/narwhal-apps/templates/prometheus-stack.yaml`에서 `kubeEtcd.enabled: false`이며, etcd metrics endpoint가 Prometheus에 노출되지 않는다고 주석에 적혀 있다. | 현재 Prometheus SLI/경보가 갖춰졌다고 간주하지 않는다. 아래 지표와 임계값은 **PROPOSED**다. |
| 정비/백업 | 확인한 `scripts/cluster/` 및 `scripts/ops/`에는 control-plane etcd의 자동 compaction/defrag/member replace 또는 snapshot 주기 설정이 없다. | 아래 일정·절차는 **PROPOSED** 운영 기준이다. Velero 백업은 etcd snapshot의 대체물이 아니다. |

## 결정 및 운영 기준

### D1 — quorum 토폴로지

- Production profile은 홀수 멤버만 허용한다: 3개는 quorum 2, 장애 허용 1개; 5개는 quorum 3, 장애 허용 2개. quorum은 `floor(N/2)+1`이다.
- **PROPOSED gate:** 등록 멤버 수가 요청된 control-plane 수와 일치하고 홀수이며, 모든 멤버가 `started` 상태이고 endpoint health가 모두 성공해야 배포/정비를 통과시킨다. 짝수 멤버 추가는 완료 상태로 승인하지 않는다.
- 저장소의 `scripts/test/verify-cluster.sh`는 멤버/health를 확인하지만 홀수 여부와 정확한 토폴로지 일치를 검증하지 않는다. 이 문서만으로 해당 자동 gate가 구현된 것은 아니다.

### D2 — member identity와 통신 경계

- Inventory는 각 멤버마다 Kubernetes node 이름, etcd member ID/name, peer URL, client URL, certificate subject/SAN 및 만료일, 마지막 health 시각을 기록한다. 멤버 이름과 node 이름의 대응이 불명확하거나 URL이 inventory와 다르면 정비를 중지한다.
- peer 트래픽은 승인된 control-plane 멤버 사이에서만 허용하고, client 트래픽은 API server의 control-plane 노드에서만 허용한다. 백업/진단 클라이언트가 필요하면 별도 승인과 최소 권한 인증서를 기록한다. 일반 워커·Pod 네트워크에 2379/2380을 개방하지 않는다.
- **PROPOSED 검증:** 각 control-plane 멤버에서 peer/client URL에 대해 TLS handshake와 trust chain, SAN, 만료를 확인하고, 승인되지 않은 worker 및 일반 workload에서 두 포트 연결이 실패하는 것을 증명한다. repo에는 이 네트워크 정책 검증기가 확인되지 않았다.
- `server.crt`를 peer 인증에 사용한다고 가정하지 않는다. kubeadm이 생성한 실제 static Pod manifest 및 인증서 역할을 대조한다. client cert/CA 개인키는 root 소유 최소권한 경로로 제한하고, 복사·회전은 승인된 PKI 절차로 기록한다. API 저장 Secret 암호화 키와 etcd CA/transport key는 별개의 자산이다.

### D3 — 관측과 성능 SLO

**PROPOSED:** etcd metrics를 인증된 경로로 수집할 때 아래 항목을 endpoint/member별로 보존한다. 메트릭을 외부에 노출하기 위해 client TLS/접근 경계를 낮춰서는 안 된다.

| 신호 | 경고 기준 | 긴급 기준 / 동작 |
|---|---|---|
| quorum 및 endpoint health | endpoint 하나가 1분 이상 unhealthy 또는 멤버 상태 불일치 | quorum 미달 즉시 긴급, 모든 변경·정비 중지 |
| leader | leader 없음 10초 초과 또는 15분 동안 leader change 3회 초과 | API 영향/leader 부재 지속 시 긴급 조사; 불필요한 재시작 금지 |
| peer RTT / 송수신 실패 | peer RTT p99 > 50 ms 5분 또는 송수신 실패율 > 1% 5분 | p99 > 100 ms 또는 연속 peer failure는 정비 중지 및 네트워크/노드 조사 |
| WAL fsync / backend commit | p99 > 10 ms 5분 | p99 > 25 ms 5분 또는 API latency 연계 시 긴급 조사 |
| 디스크 및 quota | 데이터 파티션 > 70% 또는 backend quota > 70% | > 85% 경고 강화; `NOSPACE` alarm은 즉시 긴급, 쓰기/정비 변경 중지 |

측정 이름은 etcd 버전별 노출 메트릭에 매핑하고 대시보드 정의에 그 매핑을 남긴다. 위 수치는 초기 **PROPOSED** 기준이며 workload benchmark, 스토리지 종류, 실제 baseline과 API SLO를 이용해 수용 전 조정해야 한다. Prometheus 7일 retention(`prometheus-stack.yaml`)은 Prometheus 데이터 보존 설정이지 etcd keyspace compaction 정책이 아니다.

### D4 — compaction, defrag, quota

- **PROPOSED compaction:** revision 기반 auto-compaction을 `periodic` 모드로 설정하고 1시간 retention으로 시작한다. 실제 watch/reconciliation 재시작 요구가 1시간보다 길다는 증거가 있으면 retention을 늘리고, 설정값·변경 승인·적용 버전을 기록한다. 현재 저장소에서 etcd compaction 설정은 확인되지 않았다.
- **PROPOSED defrag:** backend DB 크기/공간 회수 추이를 관찰하고, fragmentation으로 공간 회수가 필요한 경우 maintenance window에서 멤버를 하나씩 defrag한다. 매 멤버 후 endpoint health, quorum, leader 및 API readiness가 회복된 것을 확인한 뒤 다음 멤버로 진행한다. 한 번에 둘 이상의 멤버를 정지/defrag하지 않는다. defrag 자체는 compaction을 대신하지 않는다.
- **PROPOSED quota:** quota는 실측 DB 크기와 디스크 여유에 근거해 설정한다. quota 도달 전 경보를 두고 `NOSPACE` 발생 시 우선 안전한 compaction, 그 다음 순차 defrag 후 alarm 해제를 확인한다. blind write retry나 data-dir 삭제로 해결하지 않는다.
- 각 작업 전후에 `endpoint health`, `endpoint status`, `alarm list`, 디스크 사용량, API `/readyz`를 저장한다. 작업 중 quorum 저하, leader 미존재, 반복 leader change, latency 긴급 기준 초과 시 즉시 다음 단계로 진행하지 말고 현재 멤버 상태를 안정화한다.

### D5 — snapshot과 복구 증거

- **PROPOSED cadence:** 최소 매일 snapshot 1회 및 중요 control-plane 변경 직전 snapshot. 마지막 검증된 snapshot은 24시간 이내여야 한다. snapshot을 etcd data 디렉터리와 같은 장애 도메인 밖의 승인된 저장소에 복사하고 접근을 제한한다.
- 각 snapshot에 생성 시각, cluster ID, endpoint/member inventory, etcd 버전, 파일 크기 및 SHA-256, `etcdutl snapshot status` 결과, 저장 위치 식별자, 작업자/승인 및 상관 ID를 보관한다. 실제 snapshot bytes나 비밀키를 로그/증거 번들에 넣지 않는다.
- **PROPOSED 검증 cadence:** 분기마다 격리 환경에서 restore하고 snapshot status/hash를 재검증하며, Kubernetes API readiness와 대표 API object 목록/건수를 사전 기준과 비교한다. hash 불일치, restore 오류 또는 객체 검증 실패는 복구 준비 실패로 기록하고 그 snapshot을 production 복구에 쓰지 않는다.
- 이 저장소의 Velero 스케줄/리소스 백업은 etcd snapshot restore 검증을 입증하지 않는다. 별도 backup/DR 문서와 연결할 때 snapshot 증거 ID 및 복구 drill 결과를 남긴다.

### D6 — 멤버 교체 및 재가입

**PROPOSED 절차 — 아래 preflight 모두 참일 때만 한 번에 한 멤버를 처리한다.**

1. 작업 티켓/maintenance window, 승인자, 작업자, 영향 노드, etcd cluster ID, 현재 `member list`, 건강 멤버 수, leader, 마지막 snapshot ID/hash를 기록한다. 최근 검증된 snapshot이 24시간보다 오래됐거나 quorum이 건강하지 않으면 중지한다.
2. 현재 quorum을 유지할 수 있는지 계산한다. 단일 멤버 장애 상황에서 세 번째 멤버까지 제거/교체해야 하는 경우에는 자동으로 진행하지 않는다. 이미 quorum이 깨졌으면 이 순차 교체 절차가 아니라 승인된 snapshot disaster recovery 절차로 전환한다.
3. 정상 클러스터에서 제거 대상의 member ID와 node identity를 재확인하고, 나머지 모든 endpoint가 health를 통과하는지 확인한다. 실패하면 `member remove` 금지.
4. 기존 멤버 하나만 제거한 뒤 남은 멤버의 quorum/health/leader를 확인한다. 새 노드는 승인된 kubeadm control-plane join 절차로 한 대만 추가한다. `/var/lib/etcd`의 기존 데이터를 수동 복사하거나 삭제해 우회하지 않는다.
5. 새 member ID, peer/client URL, 인증서 identity를 inventory와 대조한다. 전체 endpoint health 및 `member list`에 기대한 홀수 멤버가 `started` 상태로 표시되고 API `/readyz`가 성공하기 전에는 다음 멤버를 교체하지 않는다.
6. 실패 시 추가 remove/add를 중지하고 현재 cluster ID, 멤버 목록, endpoint status, kubelet/static Pod 로그를 수집한다. 기존 데이터 디렉터리 삭제, quorum 강제 축소, 임의 `member add`는 별도 승인된 복구 계획 없이는 금지한다.

저장소의 join 스크립트는 이미 가입한 노드에서 `kubelet.conf` 존재 시 skip한다. 이는 idempotency guard이지 etcd member replacement/rejoin 검증이나 안전한 member removal 절차가 아니다.

### D7 — audit, 승인, 오프라인 재현

etcd 정비 및 복구 evidence bundle은 **PROPOSED**로 다음 필드를 가져야 한다: `operation_id`, `correlation_id`, `actor`, `approver`, `action`, `target_member_ids`, `cluster_id`, `started_at`, `completed_at`, `pre_state`, `post_state`, `result`, `snapshot_id/hash`, `evidence_refs`. 인증서/키/Secret 본문은 제외한다. 이 문서는 #42 상관/audit 및 #108 승인 연결을 요구사항으로 정리할 뿐, 해당 연동이 현재 구현되었다고 주장하지 않는다.

오프라인 복구 패키지는 필요한 etcd 도구/버전, 승인된 인증서와 별도 보관된 snapshot, 해시 manifest, 단계별 명령/기대 결과, 격리 복구 환경을 포함해야 한다. 성공 판정은 integrity check 통과, endpoint health 및 quorum 복원, API `/readyz` 성공, 객체 검증 통과다. 인터넷 연결이 필요하거나 증거 필드가 빠지면 drill은 실패로 처리한다.

## 수행 전 확인 명령과 제한

현재 검증 스크립트가 사용하는 경로는 master에서 `kubectl exec -n kube-system <etcd-pod> -- etcdctl ...`이며, 기본 TLS 재료 경로는 `/etc/kubernetes/pki/etcd/`이다. `etcdctl`은 host 설치를 전제로 하지 않는다(정적 Pod 안에서 실행; `scripts/test/verify-cluster.sh`, `docs/common/lessons-log.md`). 명령 실행 시 먼저 static Pod manifest의 실제 endpoint와 인증서 역할을 확인한다. 이 문서는 아직 존재하지 않는 metrics endpoint, 스케줄러, backup 클라이언트, 네임스페이스를 구현 사실로 전제하지 않는다.
