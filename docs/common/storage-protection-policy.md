# 스토리지 보호·펜싱·일관성 그룹 정책

> 범위: 저장소에 선언된 NFS CSI, CNPG, Velero, SeaweedFS 구성을 기준으로 보호 경계와 DR 판단 조건을 정의한다. 런타임 실측값이나 미선언된 사이트 토폴로지는 현재 보장으로 취급하지 않는다.

## 1. 현재 배포 근거와 보장

| 계층 | 저장소에서 확인되는 구성 | 현재 보장 및 한계 |
|---|---|---|
| NFS PV | `scripts/cluster/04-addons.sh`의 `nfs-csi` StorageClass는 provisioner `nfs.csi.k8s.io`와 `${NFS_SERVER_IP}`, `${NFS_SHARE_PATH}`를 사용한다. `scripts/cluster/01-nfs-server.sh`는 기본 공유 경로 `/srv/nfs/k8s`를 만들고 `sync,root_squash` export를 설정한다. | export의 `sync`는 NFS 서버 쓰기 응답 정책이지 애플리케이션 트랜잭션 일관성이나 원격 사이트 복제가 아니다. 저장소 설정에는 NFS 서버/공유의 원격 복제나 fencing이 없다. |
| local PV | 저장소에서 local PV/host-local StorageClass 선언을 확인하지 못했다. | **ASSUMPTION:** 현재 추적 범위의 배포에는 local PV 백엔드가 없다. 환경에 별도 추가된 경우 노드 상실 시 다른 노드에서 데이터가 자동 제공된다고 가정하지 않는다. |
| CNPG | `gitops/resources/narwhal-db.yaml`은 `narwhal-db` 2 인스턴스, `storageClass: nfs-csi`, S3 `destinationPath: s3://cnpg-backup/narwhal-db`, WAL 및 data gzip 압축, `retentionPolicy: "7d"`를 선언한다. | CNPG 인스턴스 복제/자동 장애조치는 데이터베이스 HA 기능이다. 저장소에는 사이트 간 동기 복제 계약 또는 실제 측정 RPO가 없다. CNPG의 백업 대상 SeaweedFS가 같은 클러스터의 NFS-backed 구성에 의존한다. |
| Velero | `gitops/charts/narwhal-apps/templates/velero.yaml`은 백업 위치를 `seaweedfs-s3.storage.svc.cluster.local:8333`의 `velero` 버킷으로 지정, `uploaderType: kopia`, `defaultVolumesToFsBackup: true`, `snapshotsEnabled: false`, node-agent 활성화를 선언한다. 다섯 스케줄은 `storage/velero-volume-policy`로 `emptyDir` 데이터를 제외하며, PVC는 기존 제외 어노테이션을 적용한 기본 fs-backup을 유지한다(#422). | PVC 파일시스템 백업 경로는 설정되어 있지만 CSI 스냅샷 기반 일관성 그룹은 활성화되지 않았다. 저장소 위치와 보호 대상이 같은 클러스터 장애 영역을 공유하므로 이 설정만으로 클러스터/사이트 재해 복구를 주장할 수 없다. |
| 스케줄 | 같은 Velero 설정의 `daily-full`은 매일 02:00, `daily-databases`는 01:00, `daily-gitea` 03:00, `daily-harbor` 04:00, `daily-openbao` 05:00이며 각각 TTL 7일 또는 14일이다. `gitops/resources/cnpg-backup.yaml`은 `narwhal-db-daily` ScheduledBackup을 매일 02:00 UTC (CNPG는 초 필드가 앞서는 6필드 cron이므로 `0 0 2 * * *`), `barmanObjectStore`, cluster `narwhal-db`, target `prefer-standby`로 선언한다. | 스케줄 선언은 성공한 백업, 백업 신선도, 복구 가능성을 증명하지 않는다. 실제 RPO는 마지막 검증 성공 시각과 장애 시각의 차이로 측정해야 한다. |

`docs/common/database.md`의 설명은 CNPG Barman Full Backup/WAL 및 Velero PVC 보호를 요약한다. 구체적인 현재 스케줄과 스냅샷 비활성 상태는 위 GitOps 설정을 기준으로 판정한다. 이 문서와 설정이 충돌하면 실행 가능한 선언을 우선한다.

## 2. 보호 모드와 일관성 그룹

| 모드 | 의미 | 이 저장소의 상태 |
|---|---|---|
| 애플리케이션 일관성 | 애플리케이션이 쓰기를 중단/flush하고 애플리케이션 경계를 확인한 뒤 백업한다. | **미구현/미검증.** Velero 설정에 workload별 quiesce hook 또는 공통 트랜잭션 경계가 선언되지 않았다. |
| Crash-consistent | 장애 시점에 저장된 블록/파일 상태를 복구한다. 여러 볼륨은 같은 순간의 원자적 스냅샷일 때만 그룹 일관성을 가진다. | Velero Kopia 파일시스템 백업 설정만으로 여러 PVC의 원자적 시점 보장은 확인되지 않는다. 이를 crash-consistent 그룹 백업이라고 표시하지 않는다. |
| CNPG 복구 | CNPG Barman object store의 base backup 및 WAL을 이용하는 데이터베이스 복구 경로다. | 독립된 DB 복구 경로로 다루며, Kubernetes PVC 복원이나 다중 앱 PVC의 공통 시점과 동등하지 않다. 복구 지점은 복구 산출물과 WAL 연속성을 검증해야 한다. |

**PROPOSED — 그룹 계약:** workload별 보호 정의는 `workload`, `namespace`, `PVC 목록`, `상태 저장 컴포넌트`, `protectionMode`, `consistencyGroup`, `writer/quiesce 절차`, `backup location`, `마지막 성공 시각`, `복구 검증`을 기록한다. 동일한 `consistencyGroup`의 PVC 중 일부라도 빠지거나 공통 시점을 증명할 수 없으면 그룹 복구를 `INCOMPLETE`로 판정하고 쓰기 서비스를 열지 않는다. 현재 Kubernetes API/CRD에 이 메타데이터 모델이 있다는 뜻은 아니다.

## 3. 실패 모드와 쓰기 소유권 게이트

| 실패 | 감지/위험 | 운영 판정 |
|---|---|---|
| NFS server 또는 master-1 상실 | `nfs-csi` PV의 공통 서버 경로가 끊겨 다수 PVC I/O가 정지하거나 미완료될 수 있다. PV/PVC 오브젝트가 남아 있어도 데이터 접근 가능성은 증명되지 않는다. | NFS 복구 또는 별도 데이터 복원 전까지 해당 데이터를 `UNAVAILABLE`로 격리한다. 다른 사이트에서 같은 PV를 붙여 쓴다고 추정하지 않는다. |
| Worker 비정상 종료 | Pod 종료/재스케줄이 완료되지 않았거나 stale attachment 상태가 남을 수 있다. NFS RWX 접근과 애플리케이션 writer 중복은 다른 문제다. | 기존 writer 종료/격리를 확인한다. 노드가 통신 불능이면 control-plane 상태만으로 그 노드의 외부 쓰기가 차단됐다고 간주하지 않는다. |
| 로컬 디스크/노드 데이터 손실 | local PV를 추가 배포한 환경에서는 데이터가 노드에 묶일 수 있다. | 노드 복구 또는 검증된 백업 복원 전 새 노드에서 writable failover 금지. |
| SeaweedFS 장애/손실 | Velero와 CNPG의 S3 백업이 현재 같은 클러스터의 SeaweedFS 서비스에 의존한다. | 보호 대상과 백업 위치의 공통 장애를 조사한다. S3 객체/버킷의 존재만으로 복구 가능하다고 하지 않는다. |
| Velero 백업 실패/불완전 | Schedule 존재와 백업 성공은 별개이며 파일시스템 백업 노드 agent 경로에도 실패 가능성이 있다. | `Backup` 완료 상태, 경고/오류, 포함 리소스, 백업 대상 객체 접근을 확인한다. 하나라도 확인 불가면 freshness를 `UNKNOWN` 처리한다. |
| CNPG WAL 단절 또는 백업 부재 | 연속 WAL 및 base backup이 없으면 요청 시점 복구를 보장할 수 없다. | CNPG 복구 절차의 backup/WAL 가용성과 복구 완료 후 DB 검증을 통과하기 전 서비스 쓰기 개방 금지. |

**PROPOSED — failover fencing gate:** 대체 writer의 쓰기 허용은 아래 모두가 참일 때만 가능하다.

1. 기존 사이트/노드의 애플리케이션 writer를 종료했거나, 전원/네트워크/스토리지 경로 등 데이터 plane 수준에서 쓰기를 차단했다는 증거가 있다.
2. 기존 writer가 다시 연결되어 동시에 쓰지 못하도록 소유권을 단일화했다.
3. 선택한 복구 경로와 복구 지점이 식별되고, 그룹 구성원 모두 복구 가능 상태다.
4. 대체 writer를 read-only/격리 상태로 기동해 데이터 및 애플리케이션 검증을 통과했다.
5. 운영 승인, 시각, 수행자, fencing 증거, 데이터 손실 추정치를 기록했다.

이 저장소에는 사이트 간 fencing controller, lease 기반 독점권, 원격 전원/스토리지 차단 자동화가 선언되어 있지 않다. Kubernetes Pod 삭제, Node taint, RWO access mode만을 독립적인 fencing 증거로 인정하지 않는다. gate 증거가 없으면 상태는 `FENCING_REQUIRED`이며 failover 쓰기 개방은 금지한다.

## 4. 복구 상태와 오류 처리

각 보호 대상의 판정 상태는 다음 중 하나로 기록한다.

| 상태 | 기준 | 다음 동작 |
|---|---|---|
| `PROTECTED` | 백업 성공 및 저장 위치 확인, 요구된 consistency 증거 확보 | RPO 계산 대상에 포함 |
| `STALE` | 백업은 있으나 허용 freshness 초과 | 승인된 데이터 손실 한도보다 오래되면 failover 금지 또는 예외 승인 기록 |
| `UNKNOWN` | 성공 시각/객체/로그를 확인할 수 없음 | 보호 상태로 보고하지 말고 수동 확인 |
| `INCOMPLETE` | PVC/리소스 누락, 그룹 구성 불완전, CNPG 복구 체인 불연속 | writer 기동 금지; 누락분 복구 또는 그룹 격리 |
| `FENCING_REQUIRED` | 기존 writer의 쓰기 차단 미확인 | 대체 writer 쓰기 개방 금지 |
| `RESTORE_FAILED` | 복원, 무결성 또는 애플리케이션 검증 실패 | 실패 artifact/log 보존, 격리, 원본 복구 경로 유지 |
| `VERIFIED` | fencing, 복원, 데이터 검증, 서비스 검증 모두 증거로 확인 | 승인된 서비스 개방 및 RTO 종료 기록 |

부분 볼륨 실패에서는 성공한 일부 PVC를 먼저 writable 서비스로 내지 않는다. 가능한 경우 복원본을 격리해 재시도하고, 불가능하면 영향 workload 전체를 `INCOMPLETE`로 유지한다. 원본과 복구본을 동시에 writable로 두는 절차는 이 정책에서 허용하지 않는다.

### Velero 진단 메시지 (#422, #431)

- 스케줄 실행 시 `storage/velero-volume-policy` ConfigMap이 없거나 읽을 수 없으면 당일 백업은 `FailedValidation`으로 끝나며 다음 cron까지 재시도하지 않는다. `kubectl -n storage get backups.velero.io`의 phase와 `velero backup describe`로 확인한다. **#456의 VeleroBackupValidationFailed가 이 실패를 감지한다. 아래 Velero backup alerts 참조(라이브 검증 전).**
- 정책이 있으면 Velero v1.18.1의 `internal/volumehelper/volume_policy_helper.go` `ShouldPerformFSBackup`은 모든 PVC 볼륨의 PV를 조회한다. 정책에 매칭되지 않는 PVC에 사용 가능한 PV가 없으면(Pending 또는 PV 삭제) 해당 볼륨은 오류가 된다. 기존 경로는 PV를 조회하지 않았다. 2026-10-23 확인 당시 kakao-narwhal의 PVC는 모두 Bound였다.

- `No present log retrieved ... cannot get resource "pods/log"`: velero-server가 PodVolumeBackup 실패 원인을 `storage` 네임스페이스의 node-agent 호스팅 파드 로그에서 읽지 못할 때 남는다. `gitops/resources/velero-server-rbac.yaml`의 `Role/velero-pod-logs`(+ RoleBinding)가 `storage` 한 네임스페이스에서 `pods/log`의 `get`만 `velero-server`에 부여한다(PR #434). 적용 후에도 이 메시지가 남으면 해당 Role/RoleBinding의 sync 상태를 먼저 확인한다. 이 grant는 읽기 전용 진단용이며 백업 대상·쓰기 권한을 넓히지 않는다.
- `Server rejected event`: velero-server는 `events` `create`/`patch`를 받지 못한다. 의도적으로 부여하지 않았으며(쓰기 verb 확대 회피) 잃는 것은 Event 기록뿐이다. 백업 결과는 Backup/PodVolumeBackup 상태로 판단한다.
- Kyverno `require-labels/check-for-labels`(Audit): node-agent ConfigMap `velero-node-agent-config`의 `podLabels`가 data mover/호스팅 파드에 `app.kubernetes.io/name=velero-data-mover`를 붙인다(Velero 1.18). 저장소 유지보수 Job(`*-kopia-maintain-job-*`)은 1.18에서 라벨을 지정할 수 없어 Audit 이벤트가 남을 수 있다.

## 5. RPO/RTO 산정 및 오프라인 증거

- **RPO 실측:** 장애 기준 시각에서 복구본에 검증된 최신 데이터 시각을 뺀 값. Velero는 마지막 `Completed` 백업 시각을 하한 정보로 기록하되 실제 데이터 손실량을 대체하지 않는다. CNPG는 복구 완료 후 DB가 가진 검증 가능한 트랜잭션 시각/업무 기준점을 기록한다.
- **RTO 실측:** 장애 선언부터 fencing, 복원, 데이터/애플리케이션 검증을 끝내고 승인된 쓰기를 연 시각까지다. 백업 다운로드 시작만으로 RTO를 종료하지 않는다.
- **오프라인에서 가능한 검증:** 코드/manifest의 StorageClass, PV access mode 선언, schedule/TTL, Velero snapshot 설정, CNPG destination/WAL 설정, 파일 내 경로/리소스 참조 확인. 이는 구성 검증이며 백업 성공이나 실제 복구 증거는 아니다.
- **런타임이 필요한 검증:** 최근 백업의 완료/오류, SeaweedFS 객체 접근성, NFS export 및 마운트 상태, RWO attachment 잔존, 복원 후 체크섬/DB 검사와 애플리케이션 smoke 검증. 오프라인 문서 검토로 이를 주장하지 않는다.
- **오프사이트 복구:** 현재 선언만으로 SeaweedFS 데이터/Velero catalog를 별도 클러스터에서 가져올 수 없다. **PROPOSED:** 암호화된 오프라인 export/import 형식, CRD/manifest/secret 의존성 목록, catalog 재등록, checksum 검증, 격리된 replacement-cluster 복구 드릴을 별도 구현·검증하고 그 결과를 기록한다. 비밀 값은 증거에 포함하지 않는다.

**PROPOSED — 드릴 기록 필드:** `scenario (planned|unplanned|failback)`, `cluster/backend/version`, `workload/group`, `backup identifier`, `backup completedAt`, `failureDeclaredAt`, `latest verified data point`, `fencing method/evidence`, `restore start/end`, `RPO`, `RTO`, `estimated data loss`, `integrity/application checks`, `failed steps`, `approver`, `failback/resync convergence`. 값이 없으면 추정 대신 `UNKNOWN`을 기록한다. 허용 RPO/RTO 수치는 코드/요구사항에 정의되어 있지 않으므로 **PROPOSED:** workload owner가 수치를 승인하기 전까지 SLA 적합으로 판정하지 않는다.

## 6. 오프라인 확인 절차

저장소 루트에서 다음 명령은 선언된 설정만 확인한다.

```bash
rg -n 'kind: StorageClass|name: nfs-csi|provisioner: nfs.csi.k8s.io|server:|share:' scripts/cluster/04-addons.sh
rg -n 'root_squash|sync,no_subtree_check|NFS_SHARE_PATH' scripts/cluster/01-nfs-server.sh
rg -n 'storageClass:|destinationPath:|wal:|retentionPolicy:' gitops/resources/narwhal-db.yaml
rg -n 'kind: ScheduledBackup|name: narwhal-db-daily|schedule:|cluster:|name: narwhal-db|method:|target:' gitops/resources/cnpg-backup.yaml
rg -n 'schedule:|ttl:|snapshotsEnabled:|defaultVolumesToFsBackup:|uploaderType:|volumeSnapshotLocation:' gitops/charts/narwhal-apps/templates/velero.yaml
rg -n 'local-path|local PV|kind: PersistentVolume' scripts gitops/resources gitops/charts/narwhal-apps/templates
```

마지막 검색의 빈 결과는 검색 범위에 선언이 없다는 뜻일 뿐, 런타임 클러스터에 수동 생성된 리소스 부재를 증명하지 않는다. 클러스터 검증은 `kubectl get sc,pv,pvc -A`, `kubectl get backup -A`, CNPG `Cluster`/`Backup` 상태, 그리고 격리된 실제 복원 검증을 별도로 수행해야 한다.

## 7. 명시적 범위

이 정책은 현재 NFS/CNPG/Velero 구성의 근거 기반 평가와 제안된 control gate를 문서화한다. Ceph/Longhorn/object-store 복제, local PV 구현, CSI snapshot 활성화, site relationship/fencing 자동화, 자동 failover/failback, replacement-cluster 복구 기능, 성능 수치 및 호환성 매트릭스는 저장소에서 확인되지 않아 구현 또는 검증된 기능으로 간주하지 않는다.

## 8. OpenBao raft snapshot 백업과 수동 복구 (#431)

`daily-openbao` Velero fs-backup은 OpenBao `data` 볼륨을 읽지 못한다(NFS `root_squash`에서 kopia가 nobody로 매핑되어 owner-only raft 파일 접근 불가). 그래서 `data`는 `backup.velero.io/backup-volumes-excludes`로 제외되어 있고(R277), 대신 애플리케이션 수준 스냅샷을 만든다. 근거 파일은 `gitops/resources/openbao-backup.yaml`이다.

| 항목 | 선언 |
|---|---|
| 주기 (D-rpo) | `openbao-snapshot` CronJob, 매일 04:30 UTC. Velero `daily-openbao`(05:00 UTC) 직전. Velero 큐 지연이 있으면 RPO는 24시간보다 길어질 수 있다. |
| 인증 (D-auth) | OpenBao Kubernetes auth 롤 `openbao-backup`(SA `storage/openbao-backup`, audience는 13-2의 `OPENBAO_K8S_AUTH_AUDIENCE` 기본값 `vault`와 Job의 projected 토큰이 일치해야 하며 R279가 검사, 기본 정책 없음, 토큰 10분·갱신 없음) → 정책 `openbao-backup` = `sys/storage/raft/snapshot` `read` 하나. root 토큰과 unseal 키는 사용하지 않는다. 롤/정책은 `scripts/cluster/13-2-narwhal-portal-bindings.sh`가 만들고 저장된 값을 다시 읽어 검증한다. |
| 암호화 (D-cms, D-oaep) | initContainer(bao)가 스냅샷을 메모리 emptyDir `work`(256Mi)에 저장하고, 메인 컨테이너가 `openssl cms -encrypt -binary -aes-256-cbc -outform DER -stream ... -keyopt rsa_padding_mode:oaep`로 수신자 **인증서(공개키)** 에 암호화해 PVC에 임시 파일로 쓴 뒤 `openbao-<ts>.snap.cms`로 원자적 rename하고 평문을 삭제한다. 평문은 NFS PVC에 쓰이지 않는다. 세션 키는 RSA-OAEP로 감싼다. 개인키는 운영자가 클러스터 밖(오프라인)에 보관하며 Secret/ConfigMap/백업 어디에도 없다. `-stream`을 쓰지 않으면 입력의 약 5배 메모리를 쓴다(30MB 입력에서 약 170MiB 대 5.5MiB 측정). 암호문 형식 검사는 전체 파싱(약 5배 메모리) 대신 DER 선두의 envelopedData 헤더와 끝의 EOC만 확인하므로 복호 가능성 자체는 검증하지 않는다. |
| 평문 보호 (D-plaintext) | Job 파드는 라벨 `velero.io/exclude-from-backup: "true"`와 어노테이션 `backup.velero.io/backup-volumes-excludes: work`로 Velero에서 제외한다(`daily-openbao`가 `defaultVolumesToFsBackup`이고 Velero 지연이 길 수 있어 겹침이 가능하다). 정기 스케줄의 `emptyDir` skip 정책도 적용되지만 수동 백업을 위해 이 제외 설정은 유지한다. `work`는 256Mi로 제한되어 더 큰 스냅샷은 쓰기 실패(ENOSPC)로 Job이 실패한다. 메모리 한도(initContainer 512Mi, 암호화 128Mi) 초과로 OOM이 나도 Job이 실패해 `kubectl get jobs -n storage`에 남는다. **신선도 알림은 아직 없다(후속 과제): `last-success` 표식 파일만 있다.** |
| 수신자 인증서 | ConfigMap `storage/openbao-backup-recipient`의 `recipient.pem`. `scripts/ops/openbao-backup-keygen.sh [--no-passphrase] <개인키 경로>`가 로컬에서 RSA-4096 자체서명 인증서(기본 10년)를 만들고 **인증서만** 적용한다. 개인키는 기본적으로 패스프레이즈로 암호화되며(`--no-passphrase`로만 해제), 지정 경로에만 0600으로 쓰고 출력하지 않으며 덮어쓰기를 거부한다. Job은 인증서가 없음/비어 있음/형식 오류/만료(24시간 이내 만료 포함)/RSA 3072비트 미만/개인키 포함/PEM 블록이 둘 이상이면 **실패하고 아무것도 쓰지 않는다**(평문 폴백 없음). 인증서가 만료되면 백업이 멈추므로 갱신 전에 새 키로 스크립트를 다시 실행하고, 이전 스냅샷 복호화용으로 이전 개인키를 보관한다. |
| 인증서 고정 (D-fpr) | ConfigMap을 쓸 수 있는 주체가 수신자 인증서를 바꿔 이후 스냅샷을 읽을 수 있다. CronJob env `RECIPIENT_FPR`(Git/Argo로만 변경)에 키젠 스크립트가 출력한 SHA-256 지문을 넣으면 불일치 시 Job이 실패한다. 비어 있으면 Job 로그에 WARNING만 남기고 고정하지 않는다(기본값은 비어 있음). 운영 환경에서는 설정해야 한다. |
| 이미지 (D-image) | 암호화 컨테이너는 이미 에어갭 목록에 있는 `docker.io/valkey/valkey:8-alpine`(다이제스트 핀, amd64+arm64, Alpine 3.24, OpenSSL 3.5.8, `cms` 포함)를 재사용한다. 새 이미지는 추가하지 않았다. Job은 `command -v openssl`과 `openssl cms -help` 가드가 실패하면 중단한다. |
| 보관 위치 (D-location) | PVC `openbao-snapshots`(nfs-csi, RWX)의 `raft/` 디렉터리. 최신 7개 유지, `last-success`(타임스탬프+파일명)가 신선도 표식이다. 별도 장애 도메인 사본은 후속 과제이며 이번 범위가 아니다. |
| Velero 전달 (D-holder) | fs-backup은 실행 중인 파드의 볼륨만 읽으므로 상시 파드 `openbao-snapshot-holder`가 PVC를 읽기 전용으로 마운트한다. 이 볼륨은 제외 목록에 없다. |
| 권한 (D-perms) | 암호문 파일 0644, 디렉터리 0755(root_squash된 Velero 데이터 무버가 읽어야 함). Velero 복원 후 `raft/`가 다른 uid 소유가 되면 chmod는 경고만 하고, 쓰기 불가이면 Job이 실패하므로 디렉터리를 다시 만든다. |

**KNOWN LIMITATION — unseal 키와 root 토큰은 보호되지 않는다.** 스냅샷은 오프라인 개인키로 보호되지만, unseal 키와 root 토큰이 들어 있는 `openbao-init` Secret은 여전히 `daily-openbao` Velero 백업(`storage` 네임스페이스 전체)에 들어 있다. 이 백업(SeaweedFS `velero` 버킷)을 읽을 수 있는 주체는 unseal 키와 **root 토큰(라이브 클러스터 접근)** 을 얻는다. 스냅샷 복호화에는 개인키가 필요하지만 클러스터 자격은 노출되어 있다. 후속 과제이며 이번 변경에서 해결되지 않았다. 개인키 분실 시 모든 스냅샷은 복구 불가이다.

**기존 클러스터 적용 순서.** 새 CronJob은 아래 단계가 끝나기 전까지 실패한다(롤이 없거나 ConfigMap이 없어 파드가 시작되지 않음). 공유 클러스터에서는 각 단계가 클러스터 변경이므로 사전 승인이 필요하다.

1. master-1에서 `scripts/cluster/13-2-narwhal-portal-bindings.sh`를 다시 실행해 정책/롤 `openbao-backup`을 만든다(root 토큰 사용, 멱등).
2. 운영자 워크스테이션에서 `scripts/ops/openbao-backup-keygen.sh <개인키 경로>`를 실행한다. 출력된 지문을 `gitops/resources/openbao-backup.yaml`의 `RECIPIENT_FPR`에 Git으로 반영한다.
3. GitOps 반영(`scripts/gitops/push-to-gitea.sh`) 후 ArgoCD가 `openbao-backup` 앱을 동기화한다.
4. 첫 실행 확인은 클러스터를 변경하는 `kubectl create job --from=cronjob/openbao-snapshot` 수동 Job으로 한다. `daily-openbao`가 새 PVC를 처리하는지는 다음 Velero 실행의 PodVolumeBackup으로 확인한다.

**수동 복구 절차 (복구 드릴 미검증).** 이 절차는 실제 클러스터에서 한 번도 수행·검증되지 않았다. 처음 수행은 드릴 환경에서 하고 결과를 `docs/common/lessons-log.md`에 남긴다.

1. 전제: 대상 OpenBao가 초기화·unseal 상태이고(`bao status`), root 권한 토큰이 있으며, 운영자 개인키(`key.pem`)와 인증서(`cert.pem`)를 가지고 있다. 원본 클러스터의 unseal 키(`openbao-init`)가 스냅샷 생성 시점의 것과 같아야 일반 복구가 된다.
2. 스냅샷 확보: 살아 있는 PVC에서 `last-success`가 가리키는 `.snap.cms` 파일을 쓰거나, 클러스터 재해 시 Velero `daily-openbao` 백업에서 `storage/openbao-snapshots` 볼륨을 복원한다.
3. 운영자 워크스테이션에서 복호화: `openssl cms -decrypt -inform DER -in x.snap.cms -recip cert.pem -inkey key.pem -out x.snap`(패스프레이즈를 물어본다). 평문 `x.snap`은 안전한 임시 위치에만 두고 사용 후 삭제한다.
4. 쓰기 클라이언트 중지(포털, 시크릿을 쓰는 워크로드) 후 `kubectl cp x.snap storage/openbao-0:/tmp/restore.snap`.
5. 일반 복구(같은 클러스터, 같은 unseal 키). root 토큰을 명령행 인자에 넣지 않도록 표준입력으로 전달하고 서버 CA를 검증한다:
   `printf '%s' "$ROOT_TOKEN" | kubectl exec -i -n storage openbao-0 -- sh -c 'read -r BAO_TOKEN; export BAO_TOKEN BAO_ADDR=https://127.0.0.1:8200 BAO_CACERT=/openbao/tls/ca.crt; bao operator raft snapshot restore /tmp/restore.snap'`.
   `-force` 없이 실행하며 키 일치 검사가 유지된다. 거부되면 다른 클러스터의 파일이거나 키가 다르다는 뜻이므로 `-force`로 우회하지 말고 원인을 확인한다.
   재해 복구(새로 초기화한 OpenBao에 원본 스냅샷을 되살림): 원본 unseal 키를 안전하게 확보해 둔 경우에만 같은 명령에 `restore -force`를 쓴다. **경고:** `-force`는 키 일치 검사를 건너뛰므로 정상 동작하는 저장소를 다른 클러스터의 파일로 덮어쓸 수 있다. 대상이 비어 있거나 폐기 예정인지 확인한 뒤에만 사용한다. 복원 직후 OpenBao는 원본 unseal 키를 요구하므로, 새 초기화로 만들어진 `openbao-init` Secret(새 unseal 키·root 토큰)을 **원본 값으로 되돌려야** 자동 unseal(`openbao-auto-unseal`)이 동작한다: 원본 `openbao-init`(Velero `daily-openbao` 백업의 `storage` 리소스 또는 운영자가 따로 보관한 사본)에서 `unseal_keys_b64`와 `root_token`을 꺼내 `kubectl create secret generic openbao-init -n storage --from-file=unseal_keys_b64=... --from-file=root_token=... --dry-run=client -o yaml | kubectl replace -f -`(값은 파일로만 전달, 명령행에 직접 넣지 않음) 후 2분 내 unseal을 확인한다. 새 root 토큰은 복원 후 무효가 되므로 이후에는 원본 root 토큰을 쓴다.
6. 복원 후 `bao status`로 unseal 상태를 확인하고, `auth/kubernetes` 롤(`narwhal-portal`, `openbao-backup`)과 KV 데이터를 읽어 검증한 뒤 클라이언트를 다시 연다. `/tmp/restore.snap`과 워크스테이션의 평문 파일을 삭제한다.

운영 순서·점검·키 교체·문제 해결의 상세는 `docs/runbooks/openbao-snapshot-backup.md`를 따른다(위 정책 표와 다르면 이 절이 우선).

미검증 항목: 실제 스냅샷 생성, 인증 롤 동작과 13-2의 `bao read -field=...` 출력 형식, 클러스터 안에서의 `openssl cms` 암호화(로컬 OpenSSL로만 확인; 이미지의 busybox `od`/`head -c`/`tail -c`도 미실행), Velero 제외 라벨 동작, Velero PodVolumeBackup 완료, 복원 드릴. 스냅샷 신선도 알림도 아직 없다.

## Velero backup alerts

이 절의 변경(#456)은 GitOps의 `prometheus-alerts.yaml`에 다음 알림을 추가한다. 현재 증거는 정적 검사와 렌더링뿐이며 라이브 적용은 아직 검증하지 않았다.

| 알림 | 조건 / 심각도 |
| --- | --- |
| `VeleroBackupStale` | 스케줄별 마지막 성공이 26시간 초과, 5분 지속 / critical |
| `VeleroBackupNeverSucceeded` | attempts 시리즈가 있지만 성공 timestamp가 없거나 0인 상태가 26시간 지속 / critical |
| `VeleroBackupFailed` | 최근 1시간 failure counter 증가, 5분 지속 / warning |
| `VeleroBackupPartiallyFailed` | 최근 1시간 partial failure counter 증가, 5분 지속 / warning |
| `VeleroBackupValidationFailed` | 최근 1시간 validation failure counter 증가, 5분 지속 / warning |
| `VeleroMetricsUnavailable` | Velero target이 없거나 전부 down, 15분 지속 / warning |

차트 키는 [Velero chart 12.0.3 values](https://github.com/vmware-tanzu/helm-charts/blob/velero-12.0.3/charts/velero/values.yaml)의
`metrics.enabled`, `metrics.serviceMonitor.enabled`, `autodetect`, `additionalLabels`를 확인했다.
`autodetect: false`는 Argo 렌더 시 CRD discovery 여부와 관계없이 ServiceMonitor를 생성한다. 설치에는 ServiceMonitor CRD가 필요하다.
`release: prometheus-stack`은 [kube-prometheus-stack 86.2.3 selector](https://github.com/prometheus-community/helm-charts/blob/kube-prometheus-stack-86.2.3/charts/kube-prometheus-stack/templates/prometheus/prometheus.yaml)에 맞춘다.
Deployment·Service·ServiceMonitor 템플릿을 upstream tag에서 가져와 해당 values로 렌더했다(전체 차트 설치 검증은 아님).

`storage`는 `09-istio-ambient.sh`에서 ambient 등록되고, Prometheus는 `podMetadata`로 mesh를 벗어난다.
현재 storage AuthorizationPolicy는 없고 NetworkPolicy는 egress 전체 허용뿐이다.
`narwhal-platform` `istio-ambient-policies.yaml`의 `velero-metrics` PeerAuthentication은 Velero 서버의 8085만 PERMISSIVE로 설정한다.
동명의 NetworkPolicy는 monitoring의 `app.kubernetes.io/name=prometheus`,
`prometheus=prometheus-stack-kube-prom-prometheus` 파드에서 8085(및 모든 ambient 정책이 요구하는 HBONE 15008)로 오는 연결만 허용한다.
`name: velero`까지 선택해 공통 app label을 가진 `name: node-agent` 파드는 제외한다. 기존 ambient kubelet probe CCNP와 노드 probe 경로는 유지한다.
다른 storage 워크로드의 mTLS 설정과 Velero의 outbound mesh 연결은 바꾸지 않는다.

메트릭 이름과 `schedule` 라벨은 [Velero v1.18.1 metrics.go](https://github.com/vmware-tanzu/velero/blob/v1.18.1/pkg/metrics/metrics.go)를 확인했다.
`InitSchedule`은 counter만 초기화하므로 `attempt_total unless last_successful_timestamp > 0`으로 성공 이력이 없는 스케줄을 감지한다.
InProgress 전용 gauge는 추가하지 않았다. items 개수나 누적 attempt-success 차이는 실행 상태·시작 시각을 보장하지 않으므로 staleness를 시간 상한으로 쓴다.
초기 설치의 첫 성공 전에는 26시간 유예한다. Prometheus 재시작·수집 공백으로 pending 시간이 초기화될 수 있고,
첫 scrape 전 발생한 counter 증가나 두 scrape 사이 재시작으로 사라진 증가는 놓칠 수 있다.
삭제되어 모든 시리즈가 사라진 개별 스케줄은 이 규칙만으로 감지하지 못한다. 일일 스케줄 주기를 바꾸면 26시간 기준도 재검토한다.

진단: `kubectl -n storage get backups.velero.io`로 phase를 보고 `velero backup describe <name> --details -n storage`와
서버/node-agent 로그, `kubectl -n storage get podvolumebackups.velero.io`를 확인한다.
FailedValidation은 `velero-volume-policy` 존재/RBAC, PartiallyFailed는 PVC/PV 바인딩·볼륨 오류,
Stale은 장기 InProgress와 뒤에 대기하는 스케줄을 먼저 확인한다. 성공 백업 및 restore 검증 전에는 오류만 숨기지 않는다.

**OpenBao #443 후속:** `openbao-snapshot`은 아직 recipient 인증서 미등록으로 fail-closed 상태다.
현재 manifest에 suspend/구성 완료 라벨이 없어 CronJob 성공 시각과 suspend 메트릭만으로 의도적 미구성과 실제 장애를 구별할 수 없다.
따라서 snapshot 신선도 알림은 추가하지 않았다. 다음 클러스터에서 `kube_cronjob_status_last_successful_time` 노출을 확인하고,
구성 완료 및 비-suspend 상태를 명시적으로 gate한 뒤 성공 없음/26시간 초과를 감지해야 한다.
Velero `daily-openbao` 성공은 snapshot 내용의 신선도를 보장하지 않는다.

다음 클러스터의 필수 확인 순서:

1. 승인된 `scripts/gitops/push-to-gitea.sh` 경로로 반영하고 velero/network-policies/prometheus-alerts 앱 Sync 및 CRD/리소스 생성을 확인한다.
2. 실제 Velero·Prometheus 파드 라벨과 namespace ambient 상태, PeerAuthentication의 8085 예외를 확인한다.
3. Prometheus에서 Velero `/metrics:8085` target `up=1`, 다섯 schedule의 attempts와 성공 timestamp, 세 failure counter를 확인한다.
4. 허용된 Prometheus에서 scrape 성공, 다른 monitoring 파드 및 다른 namespace에서 8085 접근 차단을 확인한다. Velero probe·S3 연결·정상 백업도 확인한다.
5. 격리된 시험 백업으로 Failed/PartiallyFailed/FailedValidation 발생 및 5분 후 alert, 복구 후 해제를 확인한다. 운영 스케줄의 ConfigMap은 삭제하지 않는다.
6. 시험 규칙/시리즈로 26시간 staleness·성공 이력 없음·target 손실의 pending/firing/resolved와 Alertmanager 실제 수신을 확인한다.
7. OpenBao recipient 등록 후 Job 성공, staging 파일 신선도, Velero PVB 및 복구를 확인하고 #443 알림 조건을 별도로 적용한다.
