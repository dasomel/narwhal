# Kakao NFS export ext4 → XFS(prjquota) 이전 기록 (2026-10-16)

> 절차서: [`../runbooks/nfs-xfs-migration.md`](../runbooks/nfs-xfs-migration.md).
> 사건별 교훈: [`../common/lessons-log.md`](../common/lessons-log.md) (2026-10-16 행).
> 아래 시각은 모두 UTC이며 실행 로그(운영자 세션의 `nfs-migration/log.md`)에서 옮겼다. 비밀값과 bastion 공인 IP는 싣지 않았다.

## 1. 왜

- Kakao NFS export(`/srv/nfs/k8s`)가 ext4 루트 디스크에 있었다. XFS project quota는 최초 mount 시점에만 켤 수 있어
  `nfs-quota-agent`가 쿼터를 집행할 수 없었다(재mount로 해결 불가).
- 같은 날 앞선 PR로 agent 자체의 chart/image 불일치(#404, #406)를 고쳤다. 그 뒤에도 집행 불가 원인은 파일시스템이었다.

## 2. 결정

| ID | 결정 | 이유 / 대안 |
|----|------|-------------|
| D-NFS-VOL | 전용 200GiB XFS 블록 볼륨을 master-1에 attach | 대안: ext4 루트에서 `tune2fs`로 쿼터 조정, 별도 NFS VM. 둘 다 채택하지 않았다(에이전트가 XFS project quota 전제, VM 분리는 클러스터 토폴로지 변경) |
| overmount | 같은 경로 `/srv/nfs/k8s`에 XFS를 mount | PV/PVC의 서버·경로가 그대로라 클라이언트 변경 없음. 기존 ext4 데이터는 mount 아래에 롤백 사본으로 남음 |
| squash | `no_root_squash` 유지, `root_squash` 전환은 보류 | SeaweedFS 등 uid 0 쓰기 주체가 깨진다(런북 "root_squash alignment") |

## 3. Phase 1 — 볼륨 준비 (PR #407, #408)

- 볼륨 `<master_name>-1-nfs-data`(id `da767022-…`)를 두 단계 `-target` apply로 생성·attach했다. 전체 plan은 master-1 in-place
  갱신 때문에 `addresses`가 unknown이 되어 LB 멤버 4개(etcd, master, master_http, master_https)를 교체하려 하므로
  `-target`이 필요했다(`csp/kakao-cloud/terraform/README.md`).
- `/dev/disk/by-id/virtio-<볼륨 id 앞 20자>`로 디바이스를 확인했다.
- 임시 mount(`/mnt/nfs-new`, `prjquota`)에서 쿼터 smoke test 후 본 작업을 진행했다.

## 4. Phase 2 — 타임라인과 게이트

| 시각 | 게이트 | 결과 |
|------|--------|------|
| 06:55 | G0 사전 점검 | 42 앱 Synced/Healthy, 비Running pod 0, PV/PVC 15개 Bound, CNPG 2/2 (primary `narwhal-db-2`), `/srv/nfs/k8s` 12G, 신규 볼륨 197G free |
| 06:56 | G1 백업 | `pg_dumpall`을 master-1 `/root/nfsmig-backup/narwhal-db-2026-10-16.sql.gz`(127,957 B, 0600, NFS 밖)에 저장 |
| 06:59 | G2 rsync 1차(온라인) | rc=0, 27,834 파일, 11.96GB, 84MB/s |
| 07:00:20 | 다운타임 시작 | ArgoCD 자동 동기화 중지 7개 앱(원본 `orig-*.txt` 보관) |
| 07:05 | G3 쓰기 주체 정지 | gitea, harbor, grafana/loki/tempo, prometheus/alertmanager CR, seaweedfs, openbao, CNPG hibernate, nfs-quota-agent 순서로 정지. 전 노드 NFS mount 0, `:2049` 연결 0 |
| 07:06 | G4 서버 정지 + rsync 2차 | `--delete` rc=0 (생성 25, 삭제 30, 전송 233, 678MB), dry-run 출력 0줄, 양쪽 항목 27,829 |
| 07:06:53 | G5 전환 | fstab `UUID=… /srv/nfs/k8s xfs defaults,prjquota,nofail 0 2`, `requires-mount.conf`, `nfs-server` 07:06:41 기동, `exportfs -v`가 G0과 동일(`no_root_squash`) |
| 07:07-07:26 | G6 복구 | nfs-quota-agent, CNPG(07:09 un-hibernate), openbao(unseal), seaweedfs(07:11), 나머지 순차 기동. harbor 07:25:27, gitea 07:25:51. ArgoCD 자동 동기화 07:26:04 복원(원본과 diff 없음) |
| 07:35 | G6 확인 / G7 쿼터 | 42/42 Synced/Healthy, pod 148개 Ready, PV/PVC 15개 Bound. CNPG 2/2 (primary `narwhal-db-1`로 변경), `ContinuousArchiving=True`. nfs-quota-agent 15개 `Quota applied successfully`, error 0, 계정·집행 ON, `report -p`의 hard 한도 = PVC 크기 |

- 다운타임: 07:00:20 ~ 07:25:51, 약 25.5분.
- G7 쿼터 집행: uid 26 비root, 100Mi PVC에 150M을 쓰자 서버 사용량 100M/100M에서 멈추고 `No space left on device`(ENOSPC)가 반환됐다.
  XFS project quota는 NFS 너머에서 EDQUOT가 아니라 ENOSPC로 보인다. 쓰기 자체는 성공(비root 쓰기 OK). 테스트 ns/PV/디렉터리는 삭제했다.

## 5. 이탈 사항(Deviations)

1. **SeaweedFS volume server 재배치.** volume server는 노드 로컬 hostPath `/ssd/object_store`를 쓰고 노드 고정이 없다.
   scale-down/up 뒤 데이터 23G가 있는 `.22`가 아니라 빈 `.21`에 올라가 새 볼륨(60-73, 168K)을 썼고, Loki가
   `unexpected EOF loki_index_20742`로 CrashLoop했다. 로컬 tsdb 파일은 ext4와 XFS가 md5 동일해 복사 결함이 아니었다.
   복구: volume 정지 → `.21`의 새 볼륨(id 충돌 없음)을 `.22`로 옮기고 `.21` 사본은 `/ssd/object_store.nfsmig-misplaced-20261016`에 보관 →
   `.21`/`.23` 임시 cordon으로 `.22`에 기동(07:20) → S3 gateway가 stale vidMap(`volume 59 not found`)이라 `seaweedfs-s3` rollout restart →
   loki-0 2/2 Running(07:23).
2. **`vol_dir.uuid` 덮어쓰기(자체 유발).** 위 복구의 tar가 `.22`의 `vol_dir.uuid`를 덮어써 원본을 복구할 수 없다.
   영향은 낮다: 런타임 중복 디렉터리 검사용 식별자이며 데이터는 영향 없음.
3. **CNPG primary 전환.** hibernate/해제 뒤 primary가 `narwhal-db-2`에서 `narwhal-db-1`로 바뀌었다. 정상 동작이며 런타임에 반영됐다.
4. **루트 앱.** `idp-apps`가 하위 Application의 `automated` 정책을 소유하므로 하위 앱만 멈추면 안 된다(런북에 반영).

## 6. 남은 일

- **SeaweedFS 고정**: 이 기록과 같은 PR에서 `narwhal.io/seaweedfs-volume=true` nodeSelector를 추가했다. 라이브 적용은
  노드 `host-172-17-0-22`에 라벨을 붙인 뒤 ArgoCD sync(PR 본문 절차).
- **옛 ext4 데이터 12G**: mount 아래(루트 디스크)에 숨어 남아 있다. 삭제하지 않았다. soak 기간(운영자 판단) 뒤, XFS가 mount된 상태를
  확인하고 bind mount로 접근해 지운다.

  ```bash
  findmnt -no FSTYPE /srv/nfs/k8s            # xfs 여야 한다
  sudo mkdir -p /mnt/rootfs-old && sudo mount --bind / /mnt/rootfs-old
  sudo du -sh /mnt/rootfs-old/srv/nfs/k8s    # 약 12G 확인 후에만 진행
  sudo rm -rf --one-file-system /mnt/rootfs-old/srv/nfs/k8s/*
  sudo umount /mnt/rootfs-old && sudo rmdir /mnt/rootfs-old
  ```

- **root_squash 정렬**: 보류. uid 0 항목이 깊이 3 이하에서 약 72개(`filerldb2/*`, `m9333/snapshot`, `alertmanager-db`)이며,
  SeaweedFS에 `runAsUser`/`fsGroup`을 주는 GitOps PR이 선행돼야 한다(런북 선택 절).
- **`/etc/projects` 점검**: 쿼터 smoke test로 만든 임시 project 항목이 남았는지 확인한다(`/etc/projects`, `/etc/projid`).
- `scripts/verify/nfs-nonroot-write-check.sh`는 no_root_squash 하에서 공유 루트가 777이므로 이제 FAIL이 아니라 WARN을 낸다.

## 7. 롤백 경로

절차는 런북 7절. 요지: 모든 쓰기 주체 정지 → NFS 클라이언트 0 확인 → `nfs-server` 정지 → 이전 이후 쓰기를 bind mount한 옛 ext4 사본으로 역동기화 →
`umount /srv/nfs/k8s`, fstab 줄과 `requires-mount.conf` 제거 → `nfs-server` 기동. 옛 ext4 사본을 지우면 이 경로는 사라진다.

## 8. 관련 링크

- 런북: [`docs/runbooks/nfs-xfs-migration.md`](../runbooks/nfs-xfs-migration.md)
- Terraform: [`csp/kakao-cloud/terraform/README.md`](../../csp/kakao-cloud/terraform/README.md) "NFS data volume (Kakao)"
- PR: #404, #406 (agent chart/image 불일치), #407 (전용 XFS 볼륨), #408 (롤백 역동기화 보호)
