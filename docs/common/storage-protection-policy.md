# 스토리지 보호·펜싱·일관성 그룹 정책

> 범위: 저장소에 선언된 NFS CSI, CNPG, Velero, SeaweedFS 구성을 기준으로 보호 경계와 DR 판단 조건을 정의한다. 런타임 실측값이나 미선언된 사이트 토폴로지는 현재 보장으로 취급하지 않는다.

## 1. 현재 배포 근거와 보장

| 계층 | 저장소에서 확인되는 구성 | 현재 보장 및 한계 |
|---|---|---|
| NFS PV | `scripts/cluster/04-addons.sh`의 `nfs-csi` StorageClass는 provisioner `nfs.csi.k8s.io`와 `${NFS_SERVER_IP}`, `${NFS_SHARE_PATH}`를 사용한다. `scripts/cluster/01-nfs-server.sh`는 기본 공유 경로 `/srv/nfs/k8s`를 만들고 `sync,root_squash` export를 설정한다. | export의 `sync`는 NFS 서버 쓰기 응답 정책이지 애플리케이션 트랜잭션 일관성이나 원격 사이트 복제가 아니다. 저장소 설정에는 NFS 서버/공유의 원격 복제나 fencing이 없다. |
| local PV | 저장소에서 local PV/host-local StorageClass 선언을 확인하지 못했다. | **ASSUMPTION:** 현재 추적 범위의 배포에는 local PV 백엔드가 없다. 환경에 별도 추가된 경우 노드 상실 시 다른 노드에서 데이터가 자동 제공된다고 가정하지 않는다. |
| CNPG | `gitops/resources/narwhal-db.yaml`은 `narwhal-db` 2 인스턴스, `storageClass: nfs-csi`, S3 `destinationPath: s3://cnpg-backup/narwhal-db`, WAL 및 data gzip 압축, `retentionPolicy: "7d"`를 선언한다. | CNPG 인스턴스 복제/자동 장애조치는 데이터베이스 HA 기능이다. 저장소에는 사이트 간 동기 복제 계약 또는 실제 측정 RPO가 없다. CNPG의 백업 대상 SeaweedFS가 같은 클러스터의 NFS-backed 구성에 의존한다. |
| Velero | `gitops/charts/narwhal-apps/templates/velero.yaml`은 백업 위치를 `seaweedfs-s3.storage.svc.cluster.local:8333`의 `velero` 버킷으로 지정, `uploaderType: kopia`, `defaultVolumesToFsBackup: true`, `snapshotsEnabled: false`, node-agent 활성화를 선언한다. | PVC 파일시스템 백업 경로는 설정되어 있지만 CSI 스냅샷 기반 일관성 그룹은 활성화되지 않았다. 저장소 위치와 보호 대상이 같은 클러스터 장애 영역을 공유하므로 이 설정만으로 클러스터/사이트 재해 복구를 주장할 수 없다. |
| 스케줄 | 같은 Velero 설정의 `daily-full`은 매일 02:00, `daily-databases`는 01:00, `daily-gitea` 03:00, `daily-harbor` 04:00, `daily-openbao` 05:00이며 각각 TTL 7일 또는 14일이다. `gitops/resources/cnpg-backup.yaml`은 `narwhal-db-daily` ScheduledBackup을 매일 02:00 UTC (`0 2 * * *`), `barmanObjectStore`, cluster `narwhal-db`, target `prefer-standby`로 선언한다. | 스케줄 선언은 성공한 백업, 백업 신선도, 복구 가능성을 증명하지 않는다. 실제 RPO는 마지막 검증 성공 시각과 장애 시각의 차이로 측정해야 한다. |

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
