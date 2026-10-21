# Runbook: OpenBao raft snapshot 암호화 백업 활성화·점검·키 교체 (live cluster)

**실행 전 소유자의 명시적 승인이 필요하다.** 공유 클러스터에서 아래 3~5절의 쓰기 단계는 모두 클러스터를 변경한다(OpenBao 롤/정책, ConfigMap, GitOps 배포, Job 생성). 저장소 변경(PR #443)은 정적 검사와 로컬 OpenSSL 실행으로만 확인되었고, **이 문서의 어떤 단계도 실제 클러스터에서 실행·검증된 적이 없다**(8절). 결정 근거는 `docs/common/storage-protection-policy.md` 8절(D-rpo, D-auth, D-cms, D-plaintext, D-fpr, D-oaep, D-image, D-location, D-holder, D-perms)이며, 이 문서는 그 절을 운영 순서로 풀어 쓴 것이다. 두 문서가 다르면 정책 문서와 `gitops/resources/openbao-backup.yaml`이 우선한다.

## 1. 목적과 범위

왜: NFS `root_squash` 아래에서 Velero fs-backup(kopia)은 owner-only raft 파일을 읽지 못해 OpenBao `data` 볼륨을 백업할 수 없다(#431, R277). 그래서 `data`는 Velero에서 제외하고, 대신 `openbao-snapshot` CronJob이 매일 04:30 UTC에 raft 스냅샷을 만들어 PVC `openbao-snapshots`(`raft/`)에 둔다. Velero `daily-openbao`(05:00 UTC)가 상시 파드 `openbao-snapshot-holder`를 통해 이 PVC를 읽는다.

위협 모델(짧게): 스냅샷은 KV 데이터 전체이고, 이 PVC는 Velero 백업(SeaweedFS `velero` 버킷)에도 들어간다. 평문 스냅샷을 거기 두면 백업 저장소를 읽을 수 있는 주체가 모든 비밀을 얻는다. 그래서 스냅샷은 클러스터 밖(오프라인)에 있는 개인키의 **인증서(공개키)** 로 `openssl cms`로 암호화한다. 클러스터에는 공개키만 있으므로 클러스터나 백업을 읽어도 복호화할 수 없다. 평문은 메모리 emptyDir에만 존재하고 NFS PVC에는 쓰이지 않는다.

| 보호됨 | 보호되지 않음 / 한계 |
|---|---|
| OpenBao raft 데이터(KV 등)의 일 1회 스냅샷, 최신 7개 보관 | **Gitea `data`, SeaweedFS master `data-storage`, Harbor jobservice `job-logs`**: 여전히 Velero fs-backup으로 보호되지 않는다(#431은 열려 있음). |
| 스냅샷 파일은 오프라인 키로만 복호화 가능 | **KNOWN LIMITATION:** unseal 키와 root 토큰이 든 `openbao-init` Secret은 여전히 `daily-openbao` Velero 백업(`storage` 네임스페이스 전체)에 있다. 그 백업을 읽을 수 있는 주체는 unseal 키와 root 토큰(라이브 클러스터 접근)을 얻는다. 스냅샷 복호화에는 여전히 개인키가 필요하다. |
| 인증서 오류 시 fail closed(평문 폴백 없음) | **복구는 드릴로 검증되지 않았다.** 개인키 분실 시 모든 스냅샷은 복구 불가이다. 같은 장애 도메인(NFS/SeaweedFS)이며 외부 사본은 후속 과제이다. |
| | **신선도 알림이 없다.** `last-success` 표식 파일만 있다(4절). |

## 2. 사전 조건

- 대상 클러스터 소유자의 승인. 3절 각 단계가 무엇을 바꾸는지 미리 공유한다(`AGENTS.md` Boundaries: 공유·프로덕션·자격증명·외부 변경은 명시적 승인 필요).
- OpenBao가 초기화·unseal 상태이고 `storage/openbao-init` Secret의 root 토큰이 있다(13-2가 이 토큰으로 정책/롤을 만든다).
- 운영자 워크스테이션에 `openssl`, `kubectl`(대상 클러스터 컨텍스트), 이 저장소 체크아웃이 있다. 키젠 스크립트는 `CONTEXT` 환경변수로 kubectl 컨텍스트를 고를 수 있다.
- 개인키를 보관할 **서로 다른 오프라인 위치 최소 두 곳**(예: 암호화된 이동식 매체 둘). 패스프레이즈는 키와 분리해 보관한다.
- 지문 고정(3절 3단계)을 반영할 Git 리뷰(PR) 경로.
- 클러스터에 `nfs-csi` StorageClass와 `storage` 네임스페이스의 Velero가 있다(`daily-openbao` 스케줄은 `storage` 네임스페이스에서 조회된다).

## 3. 활성화 절차

`docs/common/storage-protection-policy.md` 8절 "기존 클러스터 적용 순서"와 같은 순서이다. 새 CronJob은 1~2단계가 끝나기 전까지 실패한다(롤이 없거나 ConfigMap이 없어 파드가 시작되지 않음). 표시: **[읽기]** = 클러스터를 바꾸지 않음, **[변경]** = 클러스터를 변경함.

### 3.1 OpenBao 정책/롤 생성: 13-2 재실행 [변경]

`scripts/cluster/13-2-narwhal-portal-bindings.sh`를 다시 실행하면 정책/롤 `openbao-backup`을 만든다(root 토큰 사용, 멱등). 롤은 SA `storage/openbao-backup`, audience `OPENBAO_K8S_AUTH_AUDIENCE`(기본 `vault`), 기본 정책 없음, 토큰 10분·갱신 없음이고, 정책은 `sys/storage/raft/snapshot`의 `read` 하나뿐이다.

실행 위치에 대해 저장소가 문서화한 것: 정책 문서 8절은 "master-1에서" 실행하라고 하고, 스크립트는 `KUBECONFIG=/home/vagrant/.kube/config-local`을 하드코딩한다(Vagrantfile은 `scripts/`를 `/home/vagrant/scripts`로 동기화). README는 `vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/..."` 형태로 노드 스크립트를 호출한다. **그러나 13-2를 기존 클러스터에서 수동 재실행하는 정확한 명령은 저장소 어디에도 문서화되어 있지 않다.** 클러스터 종류(Vagrant 로컬/Kakao 등)에 맞는 실행 방법은 운영자가 확인해야 한다. 이 스크립트는 `openbao-backup` 외에 portal Secret·Keycloak 클라이언트 등 다른 단계도 함께 수행한다(헤더는 재실행 안전을 명시하지만 이 변경으로 라이브 재실행이 검증된 것은 아니다).

확인(실행 로그에서 [읽기]):

- `openbao-backup policy written and verified` 와 `OpenBao Kubernetes auth role 'openbao-backup' 설정 및 검증 완료` 두 줄이 있어야 한다.
- `WARN: OpenBao pod 없음 — OpenBao Kubernetes auth role 설정 생략` 이 보이면 이 단계는 **건너뛰어진 것**이다(스크립트는 계속 진행한다). 롤이 없으므로 활성화를 중단하고 OpenBao 상태부터 확인한다.
- `ERROR: auth/kubernetes/role/openbao-backup 영속화 확인 실패` 이면 저장된 롤 값(정책/SA/네임스페이스/audience)이 의도와 다르다.
- Phase 2 러너는 13-2의 종료 코드 3(`PORTAL_BINDINGS_DEGRADED_RC`)을 degraded로 취급한다(`scripts/cluster/06-phase2-start.sh`). 수동 재실행 시에도 종료 코드와 로그를 확인한다.

### 3.2 수신자 키 생성: 운영자 워크스테이션 [변경: ConfigMap 1개]

```bash
# 개인키는 이 경로에만 0600으로 쓰이고, 출력되지 않으며, 기존 파일은 덮어쓰지 않는다
scripts/ops/openbao-backup-keygen.sh /안전한/로컬/경로/openbao-backup.key.pem
# 인증서 경로를 따로 주려면: scripts/ops/openbao-backup-keygen.sh <개인키 경로> <인증서 경로>
# 여러 클러스터를 쓰면: CONTEXT=<kubectl 컨텍스트> scripts/ops/openbao-backup-keygen.sh ...
```

스크립트가 하는 일(코드 기준): 로컬에서 RSA-4096 자체서명 인증서(CN `narwhal-openbao-backup-recipient`, 기본 3650일, `DAYS`로 변경)와 개인키를 만들고, 기본적으로 **패스프레이즈**로 개인키를 암호화한다(openssl이 두 번 묻는다). `--no-passphrase`를 줄 때만 평문 키를 쓴다(권장하지 않음). 인증서 경로를 생략하면 `<개인키 경로에서 확장자 제거>.cert.pem`에 쓴다. **인증서만** ConfigMap `storage/openbao-backup-recipient`(키 `recipient.pem`)로 적용한다(`kubectl create configmap --dry-run=client -o yaml | kubectl apply`). 개인키는 Secret/ConfigMap/클러스터 어디에도 가지 않는다.

출력에서 반드시 기록할 것: `RECIPIENT_FPR=<SHA-256 지문>` 한 줄, 그리고 만료일.

개인키 보관(필수):

- 개인키 파일과 패스프레이즈를 **최소 두 곳의 오프라인 위치**에 따로 보관한다. 클러스터, Git, Secret, 메신저, 이 저장소에는 두지 않는다.
- **개인키를 잃으면 모든 스냅샷이 복구 불가이다.** 대체 수단이 없다.
- 인증서(`*.cert.pem`)는 공개해도 되지만 복호화(6절)에 필요하므로 같이 보관한다.

확인 [읽기]:

```bash
kubectl -n storage get configmap openbao-backup-recipient -o jsonpath='{.data.recipient\.pem}' | openssl x509 -noout -subject -enddate -fingerprint -sha256
```

기대: subject `CN=narwhal-openbao-backup-recipient`, 만료일이 먼 미래, 지문이 스크립트 출력과 같다(콜론 구분 형식).

### 3.3 지문을 Git으로 고정 (배포 전에) [변경: Git]

`gitops/resources/openbao-backup.yaml`의 CronJob `encrypt` 컨테이너 env `RECIPIENT_FPR` 값을 3.2에서 출력된 지문으로 바꾸고 리뷰된 PR로 병합한다. 비교는 콜론·공백·대소문자를 무시한다. 이유(D-fpr): ConfigMap을 쓸 수 있는 주체가 인증서를 바꿔 이후 스냅샷을 읽을 수 있게 되는 것을 막는다. 값이 비어 있으면 Job 로그에 `WARNING: RECIPIENT_FPR is empty ... not pinned`만 남기고 통과한다(기본값이 비어 있음). **운영 환경에서는 반드시 채운다.**

이 지문은 ConfigMap을 `kubectl`로 고치는 방식으로는 바뀌지 않는다. `RECIPIENT_FPR`은 Git/Argo로만 바꾼다.

### 3.4 배포: push-to-gitea.sh [변경]

ArgoCD는 클러스터 안 Gitea를 보며 selfHeal이 켜져 있어, 로컬 커밋이나 `kubectl apply`로는 지속되지 않는다(`scripts/gitops/push-to-gitea.sh` 헤더). 사용법: `scripts/gitops/push-to-gitea.sh "커밋 메시지" [gitops/ 아래 경로 ...]`, 경로를 주면 그 파일만 반영한다. `kubectl`(cluster-admin 컨텍스트), `git`, `curl`이 필요하다.

```bash
scripts/gitops/push-to-gitea.sh "feat(openbao): enable encrypted raft snapshot backup (#443)" \
  resources/openbao-backup.yaml \
  charts/narwhal-apps/templates/openbao-backup.yaml
```

- `resources/openbao-backup.yaml`: SA, PVC, CronJob, holder Deployment 본체.
- `charts/narwhal-apps/templates/openbao-backup.yaml`: 위 파일을 동기화하는 Application `openbao-backup`(네임스페이스 `devtools`, 대상 `storage`, 자동 sync·prune·selfHeal). 이 Application이 이미 클러스터의 Gitea에 있다면 첫 경로만으로 충분하다. 어느 쪽인지는 이 저장소만으로 알 수 없으므로 의도적으로 둘 다 적었다(이미 있으면 같은 내용이 덮어쓰일 뿐이다).
- 스크립트는 파일을 복사만 하고 삭제는 하지 않는다. 이 변경은 추가뿐이라 영향이 없다.

확인 [읽기]:

```bash
kubectl -n devtools get application openbao-backup          # Synced / Healthy 기대
kubectl -n storage get cronjob openbao-snapshot             # SCHEDULE 30 4 * * *, SUSPEND False
kubectl -n storage get pvc openbao-snapshots                # Bound 기대
kubectl -n storage get deploy openbao-snapshot-holder       # 1/1 Available 기대
```

### 3.5 첫 실행: 수동 Job [변경: Job 1개 생성]

04:30 UTC 전후(약 04:00~05:15 UTC)에는 피한다. 수동 Job은 CronJob의 `concurrencyPolicy: Forbid` 대상이 아니고, Velero 05:00 실행과 겹치지 않게 하기 위해서이다.

```bash
kubectl -n storage create job --from=cronjob/openbao-snapshot openbao-snapshot-manual-1
kubectl -n storage wait --for=condition=complete job/openbao-snapshot-manual-1 --timeout=300s
kubectl -n storage logs job/openbao-snapshot-manual-1 -c encrypt
```

기대: Job `Complete`, `encrypt` 로그 마지막에 `snapshot OK: openbao-<UTC 타임스탬프>.snap.cms (<바이트> bytes, CMS/AES-256-CBC)`. `RECIPIENT_FPR`을 채웠다면 `WARNING: RECIPIENT_FPR is empty`가 없어야 한다. 실패하면 파드가 둘까지 생길 수 있다(`backoffLimit: 1`), 전체 제한 시간은 600초(`activeDeadlineSeconds`).

실패 시 로그: `-c snapshot`(initContainer, bao 로그인과 스냅샷 저장)과 `-c encrypt`(인증서 검증·암호화) 둘을 모두 본다. `snapshot` 단계가 실패하면 `encrypt`는 시작되지 않는다. 증상별 원인은 7절.

결과 읽기 [읽기]: PVC는 holder 파드가 읽기 전용으로 마운트하고 있다.

```bash
kubectl -n storage exec deploy/openbao-snapshot-holder -- cat /snapshots/raft/last-success
kubectl -n storage exec deploy/openbao-snapshot-holder -- ls -la /snapshots/raft
```

기대: `last-success`는 `<YYYYMMDDTHHMMSSZ> openbao-<YYYYMMDDTHHMMSSZ>.snap.cms` 한 줄이고, `raft/` 아래에 같은 이름의 `.snap.cms` 파일(0644)이 있다. `*.snap`(평문)이나 `.tmp` 파일이 남아 있으면 안 된다. 파일은 최신 7개만 유지된다. 이 파일은 openssl이 쓴 CMS DER이며 내용 확인은 개인키가 있어야 가능하다(복호화 검증은 이 절차에 없다. 8절).

마무리 [변경]: 확인 후 수동 Job을 지워도 된다(`ttlSecondsAfterFinished: 86400`이라 24시간 뒤 자동 삭제).

```bash
kubectl -n storage delete job openbao-snapshot-manual-1
```

### 3.6 Velero가 스냅샷 볼륨을 가져가는지 확인 [읽기]

다음 `daily-openbao` 실행(05:00 UTC) 이후 확인한다. fs-backup은 실행 중인 파드의 볼륨만 읽으므로, 확인 대상은 CronJob이 아니라 **holder 파드의 PodVolumeBackup**이다(lessons-log 2026-10-20).

```bash
kubectl -n storage get backups.velero.io | grep daily-openbao
kubectl -n storage get podvolumebackups.velero.io \
  -o custom-columns=NAME:.metadata.name,POD:.spec.pod.name,VOLUME:.spec.volume,PHASE:.status.phase | grep -E 'openbao|NAME'
```

기대: `POD`가 `openbao-snapshot-holder-...`, `VOLUME`이 `snapshots`, `PHASE`가 `Completed`인 행이 있고, 가장 최근 `daily-openbao-*` Backup이 `Completed`이다. **없어야 할 것:** Job 파드의 `work` 볼륨(평문)이나 OpenBao `data` 볼륨의 PodVolumeBackup. (필드 경로는 Velero CRD 기준이며 이 저장소에서 라이브로 확인되지 않았다.) 이상이면 정책 문서 4절의 Velero 진단 메시지(#422, #431)를 함께 본다.

## 4. 정기 점검

알림이 없으므로(후속 과제) 사람이 확인한다. 신선도 기준: **`last-success` 타임스탬프가 26시간보다 오래되면 실패로 본다**(일 1회 + 여유 2시간; 26시간은 이 문서의 운영 판단이며 코드가 강제하는 값이 아니다. CronJob은 `startingDeadlineSeconds: 1200`이라 20분 안에 시작하지 못하면 그날 실행을 건너뛴다).

```bash
# [읽기] 표식과 파일
kubectl -n storage exec deploy/openbao-snapshot-holder -- cat /snapshots/raft/last-success
date -u +%Y%m%dT%H%M%SZ          # 현재 UTC와 비교
# [읽기] CronJob 마지막 실행/성공
kubectl -n storage get cronjob openbao-snapshot
kubectl -n storage get jobs      # 실패한 Job은 최근 3개, 성공은 최근 1개가 남는다
kubectl -n storage get pods -l app.kubernetes.io/name=openbao-snapshot   # OOMKilled, Error 확인
# [읽기] holder와 인증서 만료
kubectl -n storage get deploy openbao-snapshot-holder
kubectl -n storage get configmap openbao-backup-recipient -o jsonpath='{.data.recipient\.pem}' | openssl x509 -noout -enddate
# [읽기] Velero 쪽
kubectl -n storage get backups.velero.io | grep daily-openbao
```

권장 주기: 활성화 직후 며칠은 매일, 이후 주 1회, 그리고 인증서 만료 90일 전부터는 매주 만료일을 확인한다. 인증서가 만료 24시간 이내이면 Job이 실패해 백업이 멈춘다(`DAYS` 기본 10년). 실패 Job이 보이면 7절 표로 간다.

## 5. 키 교체와 회전

설계가 직접 다루는 부분: 인증서가 만료되면 백업이 멈추므로 **갱신 전에 새 키로 키젠 스크립트를 다시 실행하고, 이전 스냅샷 복호화용으로 이전 개인키를 보관한다**(정책 문서 8절 "수신자 인증서"). 스크립트는 기존 파일을 덮어쓰지 않으므로 새 경로를 준다. 이 절차 전체는 **설계에 근거는 있으나 드릴로 검증된 적이 없다.**

1. 새 키 생성 [변경: ConfigMap]: `scripts/ops/openbao-backup-keygen.sh <새 개인키 경로>`. 3.2와 같은 보관 규칙(오프라인 두 곳)을 새 키에도 적용한다. 새 지문을 기록한다.
2. 지문 갱신 [변경: Git]: `RECIPIENT_FPR`을 새 지문으로 바꾸는 PR을 리뷰·병합하고 `push-to-gitea.sh`로 `resources/openbao-backup.yaml`을 배포한다.
3. **공백 구간 주의:** 키젠이 ConfigMap을 즉시 교체하므로, 새 지문이 배포되기 전에 Job이 돌면 `does not match RECIPIENT_FPR`로 실패한다(fail closed, 그날 스냅샷 없음). 반대로 지문을 먼저 배포해도 같은 이유로 실패한다. 둘 사이의 간격을 04:30 UTC 실행과 겹치지 않게 짧게 잡고, 직후 3.5의 수동 Job으로 확인한다.
4. **이전 개인키는 지우지 않는다.** 교체 이전에 만들어진 `.snap.cms`는 이전 인증서로 암호화되어 있다. 보관본은 최신 7개이므로 교체 후 성공 실행이 7번 쌓이면 PVC에서는 모두 새 키 것이 된다. 단 Velero 백업(`daily-openbao`)에는 더 오래된 PVC 사본이 있을 수 있어, 이전 키의 폐기 시점은 Velero 백업 보존 기간 이후로 잡아야 한다. 그 보존 기간은 이 문서의 근거 파일에서 확인하지 않았다. **폐기 시점의 확정은 not designed/verified**이다.
5. 교체 후 확인: 3.5의 수동 Job 결과, 이어서 새 개인키로 한 파일을 6절 3단계 방식으로 복호화해 보는 것은 좋은 검증이지만, 복호화 검증 자체가 이 변경에서 라이브로 실행된 적이 없다(8절).

개인키 유출이 의심될 때의 절차(이미 만들어진 스냅샷의 재암호화 등)는 설계에 없다. **not designed.**

## 6. 복구

정확한 복구 절차는 **`docs/common/storage-protection-policy.md` 8절 "수동 복구 절차"** 가 유일한 원본이다. 갈라지지 않도록 여기에 단계를 복제하지 않는다. 그 절차는 **실제 클러스터에서 한 번도 수행·검증되지 않았다.** 처음 수행은 드릴 환경에서 하고 결과를 `docs/common/lessons-log.md`에 남긴다.

시작 전 전제(요약; 상세는 원본):

- 대상 OpenBao가 초기화·unseal 상태이고(`bao status`) root 권한 토큰이 있다.
- 운영자 개인키와 인증서, 그리고 개인키의 패스프레이즈가 있다. 스냅샷 시점에 쓰인 인증서에 대응하는 개인키여야 한다(교체 이력이 있으면 5절).
- 일반 복구는 원본 클러스터의 unseal 키(`openbao-init`)가 스냅샷 생성 시점의 것과 같아야 한다.
- 복호화는 운영자 워크스테이션에서 한다(`openssl cms -decrypt -inform DER ... -recip cert.pem -inkey key.pem`). 평문은 안전한 임시 위치에만 두고 사용 후 삭제한다.
- 쓰기 클라이언트(포털, 시크릿을 쓰는 워크로드)를 먼저 멈춘다.

**경고: `-force`.** 복구 명령은 `-force` 없이 실행하며, 거부되면 다른 클러스터의 파일이거나 unseal 키가 다르다는 뜻이므로 `-force`로 우회하지 말고 원인을 확인한다. `-force`는 키 일치 검사를 건너뛰므로 정상 동작하는 저장소를 다른 클러스터의 파일로 덮어쓸 수 있다. 재해 복구(새로 초기화한 OpenBao에 원본 스냅샷을 되살림)에서 원본 unseal 키를 안전하게 확보했고 대상이 비어 있거나 폐기 예정임을 확인한 경우에만 쓴다. 복원 후 `openbao-init`을 원본 값으로 되돌리는 단계는 원본 8절을 그대로 따른다.

## 7. 문제 해결

모든 메시지는 `gitops/resources/openbao-backup.yaml`의 `fail()`(또는 WARNING)이 내는 문자열이다. 로그는 `kubectl -n storage logs job/<이름> -c encrypt`(`snapshot` 단계 문제는 `-c snapshot`). 실패한 Job은 `kubectl -n storage get jobs`에 남는다.

| 증상 / 메시지 | 원인 | 조치 |
|---|---|---|
| Job 파드가 시작되지 않고(`ContainerCreating`, 이벤트에 `openbao-backup-recipient` 마운트 오류) 600초 후 Job 실패 | ConfigMap `storage/openbao-backup-recipient`가 없다(볼륨 `optional: false`, fail closed) | 3.2 키젠을 실행한다. 이미 키가 있다면 새 키를 만들지 말고 인증서 파일로 ConfigMap을 다시 만든다. |
| `recipient.pem missing or empty (run scripts/ops/openbao-backup-keygen.sh)` | ConfigMap은 있으나 `recipient.pem` 키가 비었거나 없다 | 3.2 키젠으로 다시 적용한다. |
| `recipient.pem contains a private key` | ConfigMap에 개인키가 들어갔다 (키젠은 이를 직접 거부함: `BUG: certificate file contains a private key; not applying`) | **개인키가 클러스터에 노출된 것이다.** 해당 키를 폐기하고 5절로 새 키를 만든다. 노출 기간의 스냅샷은 보호되지 않은 것으로 본다. |
| `recipient.pem must hold exactly one PEM block`, `recipient.pem is not a certificate`, `recipient.pem is malformed` | 인증서가 아니거나 PEM 블록이 둘 이상이거나 형식 오류 | 인증서 파일 자체를 확인(`openssl x509 -in <파일> -noout`)하고 3.2로 다시 적용한다. |
| `recipient certificate is expired or expires within 24h` | 인증서 만료 또는 24시간 이내 만료 | 5절로 새 키를 만들고 지문을 갱신한다. 그 동안 스냅샷은 만들어지지 않는다. |
| `recipient key must be RSA` | RSA가 아닌 키의 인증서 | 키젠 스크립트(RSA-4096)로 만든 인증서를 쓴다. |
| `recipient RSA key must be >= 3072 bits (got N)` | RSA 3072비트 미만 | 키젠 스크립트로 새로 만든다. |
| `recipient certificate fingerprint <A> does not match RECIPIENT_FPR` | ConfigMap의 인증서가 고정된 지문과 다르다: 누군가 ConfigMap을 바꿨거나(침해 가능성), 키 교체 중 Git 반영이 어긋남 | 먼저 변경이 의도된 것인지 확인한다. 의도된 교체라면 `RECIPIENT_FPR`을 Git으로 갱신(5절). 의도하지 않았다면 보안 사고로 보고, ConfigMap을 원래 인증서로 되돌리기 전에 접근 기록을 확인한다. |
| `WARNING: RECIPIENT_FPR is empty; the recipient certificate (fingerprint ...) is not pinned` | 실패가 아님. 고정이 꺼져 있다 | 출력된 지문이 3.2의 것과 같은지 확인하고 3.3으로 고정한다. |
| `openssl not found in image`, `openssl has no cms support` | 암호화 컨테이너 이미지가 다른 것으로 바뀌었다(기대: 다이제스트 핀된 `docker.io/valkey/valkey:8-alpine`) | 이미지 참조를 원래 핀으로 되돌린다. 이미지 변경은 에어갭 목록과 R279를 함께 확인한다. |
| `no snapshot in the work volume` / `snapshot is empty` (`snapshot` 단계) | 스냅샷 저장 단계가 산출물을 남기지 않았다 | `-c snapshot` 로그를 본다(아래 로그인 항목). |
| `-c snapshot` 로그에 `bao`의 로그인 오류(예: 권한 거부, 롤 없음, audience 불일치)가 보임 (메시지는 bao CLI가 내며 저장소가 정한 문자열이 아니다) | 13-2의 `openbao-backup` 정책/롤이 없거나(3.1 건너뜀), 롤의 audience·SA·네임스페이스가 Job과 다르다(projected 토큰 audience는 `vault`, 롤은 `OPENBAO_K8S_AUTH_AUDIENCE`) | 13-2 로그에서 `... 설정 및 검증 완료`를 확인하고 필요하면 3.1을 다시 실행한다. audience를 바꿨다면 Job의 `audience: vault`와 일치시킨다(R279가 검사). |
| `-c snapshot`에서 TLS 오류 | `openbao-tls` Secret의 `ca.crt`가 없거나 서버 인증서가 CA와 맞지 않음 | `kubectl -n storage get secret openbao-tls`에서 `ca.crt` 키를 확인한다. (원인은 이 변경이 만들지 않는 영역이라 라이브 확인이 필요하다.) |
| 파드 상태 `OOMKilled` | 메모리 한도 초과: initContainer 512Mi(`work` tmpfs 최대 256Mi 포함), 암호화 컨테이너 128Mi | 128Mi는 `-stream` 덕분에 스냅샷 크기와 무관하게 설계되었다. initContainer가 죽으면 스냅샷이 설계 상한(256Mi)을 넘은 것이다. 한도 변경은 설계 변경이므로 정책 문서 8절을 먼저 갱신한다. |
| 스냅샷 저장 중 쓰기 실패(`No space left on device` 류) | 스냅샷이 `work` 메모리 볼륨 256Mi를 초과 (ENOSPC로 Job이 실패하는 것이 설계 동작) | 위와 같이 설계 상한 문제. 임의로 키우지 않는다. |
| `/snapshots/raft is not writable by uid <uid>; recreate the directory` (Job은 uid 100으로 실행됨), 로그의 `WARNING: cannot chmod ...` | Velero 복원 후 `raft/` 디렉터리가 다른 uid 소유가 되었다(D-perms). chmod는 경고만 하고, 쓰기 불가이면 실패한다 | PVC에서 `raft/` 디렉터리를 다시 만든다. 지우기 전에 안의 `.snap.cms`가 필요한지 확인하고 다른 곳에 복사해 둔다. 복구 후 첫 Job으로 확인한다. (uid 100의 NFS 쓰기는 라이브 미검증, 8절.) |
| `ciphertext is empty`, `ciphertext is not CMS EnvelopedData`, `ciphertext is truncated` | 암호화가 중간에 실패했거나 출력이 비정상. 최종 이름의 파일은 만들어지지 않는다 | `encrypt` 로그의 앞선 openssl 오류를 확인한다. PVC 용량(5Gi)과 메모리 한도도 본다. 이 검사는 복호화 가능성을 증명하지 않는다. |
| `daily-openbao`의 PodVolumeBackup에 `openbao-snapshot-holder`/`snapshots`가 없음 | holder 파드가 실행 중이 아니거나(`openbao-snapshot-holder` Deployment 확인), Velero 쪽 문제 | `kubectl -n storage get deploy,pod -l app.kubernetes.io/name=openbao-snapshot-holder`. holder가 Running인데도 없으면 원인을 이 저장소만으로 단정할 수 없다. 정책 문서 4절의 Velero 진단 메시지와 Velero 로그를 본다. |
| 같은 날 Job이 실행되지 않음 | CronJob이 20분(`startingDeadlineSeconds: 1200`) 안에 시작하지 못함, 또는 `SUSPEND`, 또는 앞선 실행이 아직 도는 중(`Forbid`) | `kubectl -n storage describe cronjob openbao-snapshot`. 필요하면 3.5처럼 수동 Job을 만든다. |

## 8. 미검증 목록

이 runbook과 8절 정책 문서는 정적 검사(R279 등)와 로컬 OpenSSL 실행에만 근거한다. 다음은 **실제 클러스터에서 확인되지 않았다.** 이 문서를 운영 증거로 읽지 않는다.

- 실제 스냅샷 생성(`bao operator raft snapshot save`)과 그 크기·소요 시간
- Kubernetes auth 로그인(롤 `openbao-backup`, audience 일치)과 13-2의 `bao read -field=...` 출력 형식
- 클러스터 안에서의 `openssl cms` 암호화(로컬 OpenSSL로만 확인), 이미지의 busybox `od`/`head -c`/`tail -c` 동작
- NFS PVC에 uid 100으로 쓰기, 0644/0755 권한을 root_squash된 Velero 데이터 무버가 읽는 것
- Job 파드의 Velero 제외 라벨·`work` 볼륨 제외의 실제 효과, holder 파드의 PodVolumeBackup 완료
- 복호화와 복구 드릴(5절의 키 교체 후 복호화 포함), 이전 키 폐기 시점
- 신선도 알림은 아직 없다(4절은 수동 점검이다)
- `push-to-gitea.sh`로 두 경로를 반영했을 때 기존 클러스터의 ArgoCD 동작, 13-2 수동 재실행 명령(저장소에 문서화되어 있지 않음)

관련: `CHANGELOG.md` Unreleased "Upgrade notes"(OpenBao snapshot backup), `docs/common/lessons-log.md`의 2026-10-20 행(#431, #443).
