# Changelog

이 프로젝트의 주요 변경 사항은 이 파일에 기록됩니다.

이 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르며,
이 프로젝트는 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 준수합니다.

[English](CHANGELOG.md) | 한국어

## [Unreleased]

## [1.3.0] - 2026-10-22

보안 강화 중심의 릴리스. 에어갭 번들과 오프라인 업그레이드 번들에 무결성 검증 체인이 추가되어 승격 전에 검사되며,
ArgoCD, Gitea, RBAC, OpenBao, Keycloak, APISIX, NFS, `database` 신뢰 경계가 강화되었고, 이 경계가 퇴행하지 않도록
정적 CI 게이트가 다수 추가됨. 기존 클러스터나 번들에서 운영자 조치가 필요한 변경이 여러 건 있으므로 먼저
**업그레이드 참고**를 읽을 것.

### 업그레이드 참고

- **다이제스트 인벤토리 도입 이전에 만든 에어갭 번들은 다시 저장해야 함 (#337).** `02-save-images.sh`가 이제
  `images.digests.json`을 기록하며, `09-verify-bundle-completeness.sh`는 이 파일이 없거나, 형식이 잘못되었거나,
  정렬되지 않았을 때, 이미지에 항목이 없을 때, `oci/` 디렉터리가 기록되어 있지 않을 때, 다이제스트 또는 플랫폼이
  일치하지 않을 때 실패(fail closed)함. 이전에 저장한 번들은 `02`를 다시 실행하기 전까지 `09`에서 실패하며,
  `05`(로드)는 변경 없음.
- **`09`가 모든 OCI 레이아웃의 바이트를 읽음 (#336, #338).** manifest, index, config blob의 해시를 재계산하고,
  레이어 존재 여부와 크기를 확인하며, 심볼릭 링크된 blob을 거부하고, 다이제스트로 고정된 ref가 고정된 다이제스트를
  유지하도록 요구하며, 이미지 config의 `os/architecture`가 기록된 `collapsed_to` 플랫폼과 다르면 실패함
  (amd64 번들에 arm64 이미지가 들어간 경우 이제 거부됨).
- **업그레이드 번들 승격이 번들 생성 시각과 출처에 대해 fail closed로 동작 (#335).**
  `promote-upgrade-bundle.sh`는 `created_at`이 `UPGRADE_BUNDLE_MAX_AGE_DAYS`(기본 90, 허용 범위는 0 초과 3650
  이하이며 0은 검사를 끄지 않음)보다 오래되었거나, 없거나, 형식이 잘못되었거나, 미래 시각이거나,
  `provenance.author` / `provenance.source_repo`가 비어 있는 번들을 거부함. 이 값을 올리는 것은 검토를 거친
  결정이어야 함. `--rollback`은 검증기를 실행하지 않으므로 신선도 검사를 적용하지 않음.
  `scripts/airgap/lib/`의 샘플 매니페스트는 2026-08-01과 2026-08-25 날짜이므로 기본 상한을 통과하는 기간이
  각각 2026-10-30, 2026-11-23까지임. CI는 이 상한을 적용하지 않음.
- **업그레이드 번들 매니페스트 검증이 더 엄격해짐 (#318, #321, #324, #327, #331).**
  아티팩트 ID는 유일하고 비어 있지 않아야 하며, 다이제스트 길이는 알고리즘과 일치해야 하고, 승격 시 검토된
  라이선스 정책이 강제되며, 라이선스만 바뀐 변경도 dry-run diff에 나타나고, 후보 번들에서 제거된 의존성이
  보고됨. ID가 중복되거나 비어 있거나, 다이제스트 길이가 틀렸거나, 라이선스 정책을 위반한 매니페스트는 승격 시
  거부됨.
- **이미지 목록 수집이 엄격해짐 (93161df, #222).** 번들된 차트를 렌더링하지 못하면(따라서 hook/Job 이미지를
  수집하지 못하면) `01-generate-image-list.sh`가 중단됨. 로컬 반복 작업에서는 `AIRGAP_ALLOW_INCOMPLETE=1`로
  해제할 수 있음. `up.sh`는 VM 작업 전에 `check-bundle.sh`를 실행하며 호스트 아키텍처용 번들이 없으면 일찍 중단됨.
- **에어갭 Trivy가 클러스터 내 Harbor에서 DB를 가져옴 (9377dee, #302, #320).** 취약점 DB, Java DB, 정책 번들을
  더 이상 `ghcr.io`에서 실시간으로 받지 않음. `scripts/airgap/lib/fetch-security-db.sh`,
  `promote-security-db.sh`, `push-security-db.sh`로 준비해야 함(Harbor 프로젝트 `trivy-db`). 미래 시각이거나
  형식이 잘못된 신선도 증거는 거부됨.
- **ArgoCD Application이 더 이상 `default` AppProject에서 실행되지 않음 (6ead267).** `13-argocd.sh`가 새
  `platform`, `tenants` AppProject(`gitops/resources/argocd-projects.yaml`)를 적용하며 적용하지 못하면 실패함.
  목록에 없는 네임스페이스나 리포지토리를 지정한 Application은 동기화 시점에 거부됨. `developer` 역할은 `13-argocd.sh`와 `narwhal-platform`
  GitOps 차트(`argocd-config.yaml`) 양쪽에서 `tenants/*`만 `sync`할 수 있으며, 자가 복구 Application이 제한을 더 이상
  되돌리지 않음(#342). `portal-reader`의 `sync */*`는 의도적으로 유지됨. R246이 전체 정책 줄 집합을 포함해 두 소스를
  고정함. 라이브 클러스터에서는 확인하지 않음. 알려진 이슈 참고.
- **`narwhal-gitops`가 비공개 Gitea 리포지토리가 됨 (52dbb8d).** ArgoCD는 전용 `argocd` 계정과
  `read:repository` 토큰(리포지토리 Secret으로 등록)으로 읽으며, 최초 push 이후 `main`은 push 허용 목록으로
  보호됨. `14-gitops-bootstrap.sh`를 다시 실행하면 기존 리포지토리에도 `private: true`가 재적용됨.
  리포지토리 생성, clone, 설정 복사, push 실패는 더 이상 무시되지 않고 부트스트랩을 중단시킴 (65d07b1).
- **`developer` ClusterRole이 읽기 전용이 됨 (7551c21).** 변경 동사와 `exec` / `portforward` / `attach`는 새
  `developer-workload-admin` 역할로 옮겨졌고 `dev`(및 테넌트 네임스페이스, ed06e82)에만 바인딩됨. 그 외 모든
  네임스페이스는 직접 `kubectl` 사용 시 읽기 전용이고 개발자는 더 이상 ArgoCD Application을 `kubectl patch`할 수
  없으며, 포털은 자체 ServiceAccount를 쓰므로 영향 없음.
- **포털의 OpenBao 접근이 1시간짜리 Kubernetes 인증 토큰이 됨 (e923d7b, 2b589b7).**
  `13-2-narwhal-portal-bindings.sh`가 `narwhal-portal` Kubernetes 인증 역할(TTL 1h, audience 검증)을 만들고,
  `storage:openbao`에 `system:auth-delegator`를 바인딩하며, 8760h 토큰을 더 이상 저장하지 않음. 레거시 토큰은
  `ENABLE_LEGACY_OPENBAO_TOKEN=true`일 때만 발급됨. projected ServiceAccount 토큰은 스크립트가 아니라
  `narwhal-platform` 차트(`narwhal-portal-k8s.yaml`)가 마운트함. 포털 정책은 KV-v2 메타데이터 접근만 유지하고
  `secret/data/narwhal-portal/*`는 더 이상 부여하지 않음. 기존 배포에서 무엇을 다시 적용해야 하는지는 확인되지
  않음(unverified). 해당 커밋은 여기서 라이브 클러스터에 실행되지 않았음.
- **1년짜리 Kubernetes API 토큰이 기본적으로 더 이상 발급되지 않음 (77d6ce3).**
  `13-2-narwhal-portal-bindings.sh`는 `ENABLE_LEGACY_K8S_SA_TOKEN=true`(기본값 `false`, 설정하면 WARN 출력)가 아니면 8760h
  `K8S_SA_TOKEN`을 만들지 않으며, 포털은 대신 1시간짜리 projected 토큰(audience
  `https://kubernetes.default.svc.cluster.local`)을 읽음. 정적 `K8S_SA_TOKEN`에 의존하는 배포는 이 변수를
  설정하거나 projected 토큰으로 옮겨야 함.
- **Keycloak OIDC TLS가 검증되며 실패 시 차단됨 (5352bc2, dae7237, 3950512, 6790454).** ArgoCD OIDC 설정에서
  `insecureSkipVerify`가 제거되었고 `rootCA`가 `argocd-cm` / `argocd-secret`에 주입됨.
  `11-3-keycloak-clients.sh`는 `narwhal-wildcard-tls`와 `narwhal-ca-cert` 어디에서도 CA를 얻지 못하면 exit 1로
  종료함. `test-sso.sh`는 검증이 성공하기를 기대함.
- **APISIX Admin API는 파드 CIDR과 루프백만 허용 (1dddbe4, #399).** 허용 소스는 파드 CIDR(차트 기본값
  `10.244.0.0/16`)과 `127.0.0.1/32`이며, NetworkPolicy가 `:9180` ingress를 APISIX ingress controller와
  `devtools`의 `narwhal-portal`로 제한함(`gitops/resources/apisix-admin-ingress-policy.yaml`). 장애 복구(break-glass)용
  파드에는 라벨 `app.kubernetes.io/name=apisix-ingress-controller`가 있어야 함(`docs/common/apisix-etcd-recovery.md`,
  `docs/common/apisix-admin-api.md`). 허용 CIDR은 차트 값 `podNetworkCidr`
  (`gitops/charts/narwhal-apps/templates/apisix.yaml`)이며, `14-gitops-bootstrap.sh`가
  `resolve_apisix_admin_cidr`(`scripts/common/lib.sh`)를 통해 채움: `APISIX_ADMIN_CIDR`, Cilium
  cluster-pool CIDR, kubeadm `podSubnet`, 차트 기본값 순서임. 의도적으로 `POD_NETWORK_CIDR`을 사용하지
  않으며, 조회 오류 시 기본값으로 대체하는 대신 fail closed로 동작하므로 커스텀 파드 CIDR에 더 이상
  수동 편집이 필요 없음. 재정의할 때만 `APISIX_ADMIN_CIDR`을 설정할 것.
- **NFS export 기본값이 `root_squash`와 모드 750 루트로 변경 (6151b86, 7e4e622).** 두 export 줄에서
  `no_root_squash`가 제거됨. `root_squash`는 디스크에 이미 있는 소유권을 바꾸지 않으므로
  `docs/common/compliance-hardening.md`의 마이그레이션 런북을 따르고 `scripts/test/verify-nfs-root-squash.sh`로
  확인할 것. 비루트 쓰기를 위해 `csi-driver-nfs`는 `feature.enableFSGroupPolicy=false`로 실행되며(불변 `File`
  정책을 가진 라이브 CSIDriver는 사전 업그레이드 가드가 삭제함) Grafana의 `initChownData`는 비활성화됨.
- **`database` 네임스페이스가 기본적으로 ingress를 차단 (3148c14).** CloudNativePG, 그 오퍼레이터, Keycloak,
  Harbor, Gitea만 허용되며 그 외 호출자는 차단됨. `database`와 `platform-system` 네임스페이스에는 여전히
  네임스페이스 전체 `PERMISSIVE` `PeerAuthentication` 예외가 있어(R243 허용 목록) mTLS가 요구되지 않음.
- **Gitea ingress가 기본 차단 (67ea222).** `gitea-http`는 APISIX, ArgoCD repo-server와 application-controller,
  포털 백엔드, Kaniko 빌드 Job만 허용하며 그 외 호출자는 차단됨.
- **노드 튜닝 Job이 격리됨 (6fd186e).** 전용 `narwhal-tuning-job` ServiceAccount(RBAC 부여 없음, 토큰 미마운트)와
  ingress·egress 전체 차단 NetworkPolicy로 실행됨. `hostNetwork` 파드에 대한 CNI 적용 여부는 확인되지 않음.
- **Kyverno `disallow-latest-tag`가 이제 집행됨 (24fedbb).** `Audit`에서 `Enforce`로 바뀌어, 이미지가 `:latest`인
  Pod는 `kube-system`, `istio-system`, `platform-system`, `devtools`, `security-system`을 제외한 모든 곳에서
  어드미션 시 거부됨. 이 네임스페이스 밖의 워크로드는 업그레이드 전에 태그를 고정할 것.
- **ArgoCD 캐시가 Valkey로 실행됨 (3a3aef9).** `13-argocd.sh`가 `argocd-redis`를
  `docker.io/valkey/valkey:8-alpine`으로 지정함(Redis 8은 RSALv2/SSPL/AGPL). 기존 클러스터는 `13-argocd.sh`를
  다시 적용하면 반영됨.
- **포털이 `narwhal-portal:1.1.0`으로 고정됨 (#343, eb993c24; 기존 2a11c10, #245의 1.0.19).**
  1.1.0은 리포지토리 키로 Cosign 서명됨. 1.0.19 및 그 이전 버전은 서명되지 않았으며, 1.0.18은 시작 시
  크래시하므로 사용하면 안 됨(`SECURITY.md`). 1.1.0은 동일한 변경에서 설치하는 TLS 전용 Valkey 리스너와
  전용 ACL 사용자를 필요로 함(`rediss://`, `VALKEY_USERNAME`, `VALKEY_PASSWORD`). 포털 1.0.19는
  `VALKEY_USERNAME`을 지원하지 않아 이에 연결하여 실행할 수 없음.
- **`up.sh`가 Phase 2가 불완전하면 exit 2로 종료 (#225)**, macOS 로컬 네트워크 권한이 VM을 막는 경우 정확한
  시스템 설정 수정 방법을 출력하고 중단함.
- **Kakao Cloud (e496631, 8f24bcf, eda06db).** 바스천이 자체 보안 그룹을 가지며, SSH는 필수값인
  `bastion_ssh_allowed_cidrs`로 제한되고 `0.0.0.0/0`은 거부됨. SSH 호스트 키는 클러스터별 `known_hosts`로
  검증되며(최초 접속 시 신뢰, 키가 바뀌면 차단), 노드나 바스천을 재구축한 후에는 `kakao_forget_host`로 이전
  항목을 지울 것. kubelet 포트 10250은 노드 서브넷에만 열림.
- **SeaweedFS S3에 클라이언트별 키 필요 (#412).** S3 서버가 이제 `enableAuth` 및 각자 전용 버킷으로 제한된 4개
  클라이언트 Secret(`loki`, `tempo`, `velero`, `cnpg`)에서 생성된 identities 파일로 실행됨.
  `storage/seaweedfs-s3-config`는 자동 생성되며 직접 편집하면 안 됨. 내장 filer S3는 꺼져 있으므로
  `seaweedfs-filer`는 더 이상 `:8333`을 게시하지 않음. Loki와 Tempo는 `envFrom`을 통해
  `monitoring/loki-s3-credentials` 및 `monitoring/tempo-s3-credentials`에서 키를 읽으며,
  `narwhal-db.yaml`은 더 이상 `cnpg-s3-credentials`를 포함하지 않음. **기존 클러스터에서는
  `docs/runbooks/seaweedfs-s3-auth.md`를 단계별로 따를 것.** `08-4-storage.sh`는 이제 읽기 전용 가드를 먼저
  실행함(#414): 기존 S3 서버에 `-config=`가 없고 Loki 또는 Tempo StatefulSet이 아직 `envFrom`으로 S3 자격 증명 Secret을 참조하지 않으면 중단하며(Secret 존재만으로는 아무것도 증명되지 않음: `07-cnpg.sh`가 먼저 생성함), 승인된 즉시
  전환은 `NARWHAL_ALLOW_S3_AUTH_CUTOVER=true`를 설정함(kubectl, RBAC, 파싱 오류는 절대 우회되지 않음). 런북
  게이트가 수정됨(#419): `backups.velero.io,schedules.velero.io`, `env | grep AWS_` 대신 WAL 아카이빙 진행
  확인과 온디맨드 CNPG 백업, S3 서버 플래그는 `command`와 `args`에서 읽음. seaweedfs Application은 이제
  `narwhal.io/s3-config-sha256` 파드 템플릿 어노테이션을 무시하므로(`ignoreDifferences`와
  `RespectIgnoreDifferences=true`) `s3_sync_server_config`가 기록한 재시작 표식이 자가 치유(self-heal) 후에도
  유지됨. 파드 템플릿에는 정적 어노테이션 `narwhal.io/s3-config-source`가 있어
  Application이 Synced 상태를 유지함(없으면 무시한 표식이 파드 템플릿의 유일한 어노테이션이라 Argo CD의 정규화된
  라이브 상태에 빈 `annotations: {}`가 남고 Application이 OutOfSync로 표시됨). 2026-10-19 kakao-narwhal에서
  검증됨: 표식이 자가 치유 후에도 유지되었고, 두 번째 `s3_sync_server_config`는 무동작이었으며 Application은
  Synced / Healthy임. 정적 어노테이션의 첫 sync는 S3 서버를 한 번 롤링함. `07-cnpg.sh`도 해당 헬퍼를 호출함(#415). `verify-backup.sh`는 Velero 파드의 `wget` 대신
  `narwhal-db` 파드 안에서 `python3`로 익명 403을 확인함(#419, 2026-10-19 kakao-narwhal에서 6/6 통과). 생성되는 키 값은 작은따옴표로 감싸므로 숫자 형태의
  키도 문자열로 유지됨(#415). `verify-backup.sh`는 Velero·CNPG 리소스를 API 그룹까지 붙여 지정함
  (`schedules.velero.io`, `backups.postgresql.cnpg.io` 등). 짧은 이름 `schedule`이 Chaos Mesh로 해석되어
  "No Velero schedules found"가 보고되던 문제를 고침(R278). `ensure_s3_identities`는 멱등적임: 두 번째 실행 시 기존 키를 유지함.
- **`08-4-storage.sh` 실행 전 SeaweedFS 데이터 노드 라벨링 필요 (#409, cd62c691).** 볼륨 서버는 노드 로컬
  hostPath `/ssd/object_store`에 데이터를 보관하며 `nodeSelector narwhal.io/seaweedfs-volume=true`로 고정됨
  (차트 및 `08-4-storage.sh`). 기존 클러스터에서는 이미 데이터를 보관 중인 노드에 먼저 수동으로 라벨을 지정할 것.
  라벨이 지정된 노드가 없으면 `08-4`는 이름순 첫 번째 워커에 라벨을 지정하며, 데이터가 없는 노드에 배치된 볼륨
  서버는 빈 상태로 시작됨(2026-10-16 NFS 마이그레이션 중 재스케줄링이 정확히 이 문제를 일으켜 Loki가
  크래시 루프에 빠짐). 둘 이상의 노드에 라벨이 있으면 `08-4`는 중단됨. SeaweedFS master와 filer는 이제
  uid/gid 1000으로 실행됨 (#410).
- **APISIX admin 키가 생성됨 (#400).** 라이브 Kakao 클러스터는 공개된 업스트림 admin 키로 실행 중이었음.
  이제 부트스트랩 시 키를 1회 생성하고(`resolve_apisix_keys`), 업스트림 기본값을 sha256으로 거부하며,
  ingress controller에 기존 Secret을 제공함. 기존 클러스터는 `scripts/cluster/rotate-apisix-admin-key.sh`를
  실행해야 함(이전 키에 대한 401 및 새 키에 대한 200을 검증함). 신규 설치에는 별도 조치가 필요 없음.
- **Kakao Cloud의 NFS export용 선택적 전용 XFS 볼륨 (#407, #408, #409, #411).** `nfs_data_volume_size`
  (기본값 0)는 master-1에 연결되는 `prevent_destroy` 볼륨을 생성함. `01-nfs-server.sh`는 볼륨이 비어 있을
  때만 XFS로 포맷하고 `prjquota`로 마운트함. 기존 export 마이그레이션은 승인 기반
  `docs/runbooks/nfs-xfs-migration.md`를 따름. OpenTofu 부분은 4단계의 `-target` 제한 단계이며
  (`csp/kakao-cloud/terraform/README.md`), 연결 후 전체 plan 실행 시 로드밸런서 멤버 4개가 교체되기 때문임. 동일 런북이 `root_squash` 전환도 함께 처리함(기본 dry-run인
  `scripts/cluster/nfs-root-squash-align.sh`로 소유권 일치 작업 수행, root 필요, CSIDriver `fsGroupPolicy`는
  불변). 두 작업 모두 2026-10-16 라이브 Kakao 클러스터에서 1회 수행되었으며
  (`docs/kakao/nfs-xfs-migration-2026-10-16.md`), 클린룸 재수행 이력은 없음.
- **nfs-quota-agent 차트/이미지 버전 불일치 거부 (#404, #406).** `05-nfs-quota-agent.sh`는 `helm` 실행 전
  차트의 플래그와 이미지를 비교하고(`scripts/common/lib-nqa.sh`), DaemonSet을 대기하며 `CrashLoopBackOff` 시
  exit 1로 종료함. 라이브 클러스터에서 `--state-dir`(v0.4.0 신규)을 전달하는 차트에 v0.3.0 이미지가 실행되어
  약 14,000회 재시작된 바 있음.
- **Velero 볼륨 백업에 두 가지 추가 권한 필요 (#421).** 생성된 `velero-server` RBAC에 `nodes`에 대한 `get`
  (클러스터 스코프)과 `storage` 네임스페이스의 `events` get/list/watch를 위한 네임스페이스 Role
  `velero-node-agent-events`가 추가됨. 노드에 대한 `list`/`watch` 및 클러스터 전체 이벤트는 계속 거부됨.
  이 권한이 없으면 `PodVolumeBackup`이 `cannot get resource "nodes"` 오류로 실패하고 `Prepared` 상태로
  남게 됨. Argo CD가 `kubectl apply`를 되돌리므로, `push-to-gitea.sh`로
  `resources/velero-server-rbac.yaml`을 push한 후 `velero-server-rbac` Application을 하드 새로고침(hard-refresh)할 것
  (스크립트는 이를 새로고침하지 않음). #412 이전에는 kopia 리포지토리가 익명 S3에 대해 초기화되지 못했고,
  kakao-narwhal에는 `PodVolumeBackup`이 한 번도 존재한 적이 없었음. 2026-10-18/19 kakao-narwhal에서 볼륨 백업 및 복원, 권한이 검증됨. 영구 RBAC는
  2026-10-19 kakao-narwhal에 적용되었음. 그날의 `velero-daily-databases` 실행(볼륨 없음)은 완료되었음.
  **export가 기본값 `root_squash`일 때 소유자나 그룹만 읽을 수 있는 파일이 있는 볼륨은 볼륨 백업이 동작하지
  않음 (#431).** Velero 데이터 이동 파드는 root로 실행되고 `root_squash`는 root를 `nobody`로 매핑하므로 kopia가
  `permission denied`를 받고 해당 볼륨을 취소함. 2026-10-19 kakao-narwhal에서 OpenBao `data` 볼륨(`vault.db`
  모드 0660, 소유자 100:1000)은 타깃 백업으로 2회, PostgreSQL `pgdata` 볼륨(모드 0700)은
  `velero-daily-full` 스케줄 실행에서 재현됨. 같은 실행에서 emptyDir 볼륨과 (누구나 읽을 수 있는) OpenBao `audit` 볼륨은
  백업되었고, 시험용 PVC는 앞서 백업·복원되었음(#418). 첫 번째 타깃 실행에서는 `Canceled` `PodVolumeBackup`이 하나 있는 백업이 20분
  넘게 `InProgress`로 남았고 다음 백업이 그 뒤에서 대기했으며, 진행 중에는 삭제 요청이 거부되었고 Velero 서버 파드를
  재시작하자 풀렸음. 두 번째 타깃 실행은 약 2분 뒤에 풀어 주었으므로 이런 백업이 얼마나 오래 `InProgress`로 남는지는
  알 수 없음.
- **Kakao Cloud 네트워킹 (#396, #265, #403).** 5개의 `kakaocloud_public_ip` 리소스 전체가 `prevent_destroy`로
  설정됨. 대상 지정 destroy 대신 `-replace`를 사용할 것. kubelet(10250) 보안 그룹 규칙은 노드 서브넷으로
  제한됨. `08-1` 설치는 이제 APISIX ingress controller에 request 256Mi, limit 512Mi를 부여함(전체 동기화 중
  256Mi에서 OOMKilled되었음). GitOps 차트의 `ingressController` 블록은 이제 일치하며(256Mi request / 512Mi limit, #424),
  kakao-narwhal의 컨트롤러는 별도 Helm 릴리즈이고 512Mi로 실행 중임.
- **기존 클러스터: CNPG 백업 스케줄, OpenBao 스냅샷 백업, Tempo·Velero 재시작 (#441, #443, #439, #440).**
  머지만으로는 실행 중인 클러스터가 바뀌지 않으며, 아래 단계는 모두 공유 클러스터를 변경하므로 먼저 승인을 받을 것.
  - *CNPG `ScheduledBackup` (#441).* `database`의 라이브 `narwhal-db-backup`은 `07-cnpg.sh`가 만들며 머지로
    갱신되지 않음. `07-cnpg.sh`의 해당 부분을 다시 실행하거나
    `kubectl patch scheduledbackup -n database narwhal-db-backup --type=merge -p '{"spec":{"schedule":"0 0 2 * * *"}}'`를
    실행할 것. 기존 5필드 스케줄은 매시간 실행되어 `Backup` 객체가 누적되었음. 일괄 삭제하지 말고 먼저 클러스터의
    `retentionPolicy`를 확인한 뒤 정리를 맡길 것.
  - *OpenBao 스냅샷 백업 (#443). 아직 어느 클러스터에도 활성화되지 않음.* 각 단계가 공유 클러스터를 변경하며
    순서는 다음과 같음(`docs/common/storage-protection-policy.md` 8절, 운영자 런북
    `docs/runbooks/openbao-snapshot-backup.md`). (1) `scripts/cluster/13-2-narwhal-portal-bindings.sh`를
    다시 실행해 정책과 Kubernetes-auth 역할 `openbao-backup`을 만듦. (2) 운영자 워크스테이션에서
    `scripts/ops/openbao-backup-keygen.sh <개인키 경로>`를 실행함. RSA 키 쌍을 로컬에서 생성하고(개인 키는 기본적으로
    패스프레이즈로 보호되며 해당 경로에만 기록됨) 인증서만 ConfigMap `storage/openbao-backup-recipient`로 클러스터에
    적용함. **개인 키는 클러스터와 Git 밖에 보관할 것. 분실하면 모든 스냅샷을 복구할 수 없음.** (3) 스크립트가 출력한
    SHA-256 지문을 배포 전에 검토된 Git 변경으로 `gitops/resources/openbao-backup.yaml`의 `RECIPIENT_FPR`에 고정할 것
    (비어 있으면 job은 WARNING만 남김). (4) `scripts/gitops/push-to-gitea.sh`로 배포함. (5) 수동 Job
    (`kubectl create job --from=cronjob/openbao-snapshot ...`)으로 첫 실행을 확인하고 다음 `daily-openbao`
    `PodVolumeBackup`이 스냅샷 볼륨을 가져가는지 확인함. ConfigMap이 생기기 전까지 CronJob은 fail closed로 동작하며
    스냅샷을 쓰지 않음.
  - *롤아웃.* Tempo 변경(#439)은 Tempo를 재시작하며 `persistence`가 꺼져 있어 아직 flush되지 않은 WAL을 잃음.
    Velero `node-agent` 변경(#440)은 `--node-agent-configmap` 인자를 추가하므로
    node-agent DaemonSet이 재시작됨.

### 추가

- **Velero용 일일 암호화 OpenBao raft 스냅샷 (#431, #443).** 04:30 UTC CronJob이 `sys/storage/raft/snapshot` 읽기로만
  제한된 Kubernetes-auth 역할로 인증하고, 운영자가 보관하는 수신자 인증서로 `openssl cms`(RSA-OAEP) 암호화하여 PVC에
  기록함(개인 키는 오프라인). 평문은 메모리 `emptyDir`에만 존재함. holder Deployment가 PVC를 읽기 전용으로 마운트해
  Velero fs-backup이 파일을 가져가며, job 파드 자체는 Velero에서 제외됨. 실패 시 fail closed로 동작하고
  `RECIPIENT_FPR`로 인증서 지문을 고정할 수 있음. 키 쌍은 `scripts/ops/openbao-backup-keygen.sh`로 운영자 워크스테이션에서 로컬 생성함.
  정적 검사와 추출한 스크립트의 로컬 실행으로만 검증함. 알려진 이슈 참고.
- **OpenBao 스냅샷 백업 운영자 런북 (#431, #449).** `docs/runbooks/openbao-snapshot-backup.md`가 지문 고정을 포함한
  활성화 순서, 첫 실행 확인, 정기 최신성 점검, 키 교체 및 노출 시 유의사항, 복원 절차 안내, 문제 해결을 다룸.
  OpenBao 스냅샷 백업은 여전히 어느 클러스터에도 활성화되지 않았고 실제 동작은 검증되지 않음. 알려진 이슈 참고.
- **오프라인 업그레이드 번들 파이프라인 (7becc87, #300, #301).** JSON 스키마, 검증기, 승인이 포함된 승격
  게이트, dry-run diff, 내용 및 의존성 검증, 컴포넌트 업그레이드 웨이브 그래프의 정적 검사(#46). cert-manager가
  첫 오케스트레이션 파일럿임 (dd9ca0e).
- **에어갭 번들 무결성 체인 (#336, #337, #338).** OCI 레이아웃 바이트 검증, 저장 시점 이미지별 다이제스트
  인벤토리, `09` 게이트의 config 플랫폼 검증. `--report FILE`로 JSON 레이아웃 리포트를 기록함.
- **오프라인 취약점 DB 번들 무결성 검사** `scripts/verify/security-db-bundle-check.sh` (#302).
- **불변 릴리스 기반 작업 (24fedbb, a12401c, b342bff).** `image-digests.tsv`가 각 태그가 해석된 다이제스트를
  기록하며 `09`는 행이 없는 이미지가 있으면 실패함. kaniko와 alpine/git은 불변 태그로 고정되고, 예약된
  워크플로가 이미지 다이제스트 드리프트를 감지함.
- **공급망 CI 게이트.** 가변 이미지 태그를 병합 전에 거부(ad7804c)하고 모든 워크플로의 가변 패키지 설치를
  거부(#316)하며, pip 요구 사항 옵션에는 해시가 필요하고(#330), `component-licenses.tsv`에 대한 라이선스 정책
  게이트(aa9c1a4), SBOM·커밋·워크플로 실행·취약점 상관 분석(6dab398, #317), 오프라인 번들용 CycloneDX
  SBOM(c6c16dd), 이미지별 라이선스 맵이 포함된 `NOTICE`(c65f3ab), 모든 릴리스 아티팩트는 목록에 있거나
  `UNKNOWN`으로 검토되어야 함(#295), kubeconform CRD 스키마를 벤더링하고 `-ignore-missing-schemas`를
  제거(7493182), 서드파티 다운로드는 고정되고 체크섬이 검증됨(fc31efb).
- **서비스 메시용 신규 정적 게이트.** R242는 `09-istio-ambient.sh`의 `AMBIENT_NAMESPACES`가 NetworkPolicy
  검사가 다루는 네임스페이스와 다르면 CI를 실패시킴(#332). R243은 `PeerAuthentication`의 `PERMISSIVE` /
  `DISABLE`을 검토된 허용 목록에서만 허용하고, 메시 전역 `STRICT`를 요구하며, 기존 예외 2건을 개수가 고정된 알려진
  이슈로 기록함(#333, #334). R244는 네임스페이스 전체 `PERMISSIVE` `PeerAuthentication`이 있는 모든 네임스페이스에
  ingress 기본 차단을 요구하며 `monitoring`과 `platform-system`을 알려진 이슈로 집계함(#339).
- **보안 태세용 정적 게이트 추가 (#283-#293, #319, #322, #325, #326).** 웹훅 `failurePolicy`와 타임아웃,
  `hostPath` 마운트(중첩된 CronJob 포함), Pod Security Admission 라벨, 워크로드 런타임 태세와 RuntimeClass 참조,
  seccomp 오버라이드, kubelet 및 API 서버 인가 기준선, 비활성화된 `NodeRestriction` 거부.
- **노드 및 etcd 검증기.** `scripts/verify/kubelet-authz-check.sh`(#227), 노드 측 static pod 매니페스트
  검증기(#291), 안전한 키 교체를 위한 `etcd-encryption-check.sh`와 `rotate-etcd-encryption-key.sh`(#233).
  kubeadm 설정은 이제 kubelet 익명 인증 끔, webhook 인증/인가, `readOnlyPort: 0`을 명시함(kubeadm 기본값과
  동일).
- **Kyverno `verify-image-signatures`(Audit, 9b19aa1)와
  `scripts/airgap/10-verify-image-signatures.sh`.** 알려진 이슈 참조: 이 정책은 현재 아무것도 집행하지 않음.
- **Kyverno `restrict-tuning-job-admission` (9742afc).** `devtools`의 privileged 및 호스트 네임스페이스 파드는
  `narwhal-tuning-job` ServiceAccount로 제한됨.
- **풀 리퀘스트 기반 테넌트 네임스페이스 (ed06e82).** `resources/tenants/<team>/<namespace>.yaml`을 `tenants`
  Application이 동기화하며, 포털의 `portal-gitops` 신원은 브랜치를 push할 수 있지만 `main`에 병합할 수 없음.
- **설계 문서와 기준선.** CAPI 대 프로바이더 ADR(#266), Kubernetes/OSS 호환성 매트릭스(#271, #296), 통합 관측성
  계약(#281), 스토리지 보호 정책(#279), 관리 플레인 HA 계약(#280), 이벤트 엔벨롭 기준선과 멀티클러스터 자격 증명
  내보내기(9af2489, 7fc3a45), T1-T7 테스트 분류(c16dc86).
- **에이전트 실행 증거 체인, 참조 구현체 (#413, ADR-0002).** `scripts/evidence/`는 권한 부여(grant) 사용에 대한
  서명 및 해시 체인 로그임: grant 사용 전의 `intent` 레코드와 사용 후의 `result` 레코드, 꼬리 잘림(tail truncation)과
  롤백을 감지하는 교차 서명 체크포인트, 분리된 엔트리 및 체크포인트 키, 체인에서 파생되는 1회 사용(spend-once)
  보장. Python 표준 라이브러리와 `openssl`로 구현되었으며, 66개의 공격 테스트가 R273으로 실행됨. 현재 **어떤
  정책 집행 지점에도 아직 연동되지 않음**; `docs/adr/0002-agent-execution-evidence-chain.md`에 잔여 위험
  (서명 오라클, advisory locking, 체크포인트 간격)이 정리됨. #417의 후속 작업이 수정됨 (DER 개인 키 거부,
  key-pair 검사, rename-swap 검사를 포함한 0600 checkpoint 파일 모드, 문서화된 torn-line 복구 절차).

### 변경

- **컴포넌트 고정 (`VERSIONS.md`).** Kubernetes 1.35.5 → 1.35.7(kubeadm/kubelet/kubectl 행은 이제 CI에서
  검사, 80c0c77), nfs-quota-agent 0.2.1 → 0.5.0(d8821a9), Harbor는 불변 태그 v2.15.1로 고정, 포털 이미지
  1.0.16 → 1.0.19(#245), 이후 1.1.0(#343).
- **포털 RBAC.** PVC 사전 점검을 위한 `StorageClass`, `ResourceQuota` 읽기 전용 접근(#315), `argocd-server`가
  더 이상 `nodes/proxy`에 접근하지 않음(#255), Velero 서버 역할이 `cluster-admin` 대신 최소 권한이 됨(#250),
  포털의 Gitea 토큰이 최소 권한이 됨(#252, #253).
- **GitOps 부트스트랩이 fail closed로 동작 (65d07b1, a6bc6bb).** ArgoCD가 소스를 바라보기 전에 Gitea API로
  소스를 검증하며, Gitea 토큰 발급은 멱등적임.
- **Keycloak 클라이언트 강화 (e67f234, 50aef34).** 와일드카드 대신 클라이언트별 정확한 `webOrigins`, 업데이트
  경로에서도 `directAccessGrantsEnabled=false`, 예상치 못한 클라이언트가 있으면 부트스트랩을 실패시키는
  `validate-keycloak-clients.sh`.
- **SBOM 상관 분석이 무관한 취약점 식별자와 매칭되지 않음 (#317).**
- **CI 및 릴리스 게이트.** CI 패키지 설치가 라이프사이클 스크립트 없이 실행됨(#346), 포털 서명 job이
  이미지 다이제스트를 전달받음(#394), 포털 `THIRD-PARTY-NOTICES.md`가 markdownlint를 통과함(#386),
  `mark-review-pass`가 비-ASCII PR 번호를 거부함(#313), research-evidence 워크플로의 `setup-python`이
  SHA로 고정됨(#314).
- **의존성 업데이트.** `ruby/setup-ruby` 1.327.0 → 1.329.0(#349), `@base-ui/react` 1.8.0 → 1.9.0(#354),
  `portal/`의 `shadcn` 4.19.0 → 4.21.4(#353). 포털 notices 생성기가 이제 법적 고지 텍스트 블록의 탭을
  공백으로 변환함.

### 수정

- **S3 키를 담은 Secret을 서버 사이드 어플라이로 적용 (#415, #442).** 필드 매니저는 `narwhal-s3-identities`이고
  값은 base64 `data:`로 기록되므로 키가 `kubectl.kubernetes.io/last-applied-configuration` 어노테이션에 남지
  않음. 대상은 Secret 5개(클라이언트 Secret 4개와 `storage/seaweedfs-s3-config`, `scripts/common/lib.sh`의
  `s3_apply_secret`)임. 기존 어노테이션은 제거되며, 이 정리나 인코딩이 실패하면 계속 진행하지 않고 중단함. **기존
  클러스터는 `ensure_s3_identities`(`07-cnpg.sh` / `08-4-storage.sh`)를 다시 실행하기 전까지 평문 키가 든 옛
  어노테이션을 그대로 가짐.** 가짜 `kubectl`로만 검증했으며, 실제 API 서버에서의 옛 client-side 소유자로부터의 소유권
  이전과 키 회전은 검증하지 않았음.
- **CNPG `ScheduledBackup`이 매시간 실행됨 (#431, #441).** 스케줄이 5필드 `0 2 * * *`였으나 CNPG는 초가 앞에 오는
  6필드를 쓰므로 매시간 2분에 실행되어 `Backup` 객체가 누적되었음. 이제 `0 0 2 * * *`(매일 02:00)임. 라이브
  객체는 업그레이드 참고 확인.
- **Tempo 메모리 ballast 비활성화 및 `GOMEMLIMIT=1280MiB` 설정 (#420, #439).** OOMKilled에 대한 가설이며
  확인된 수정이 아님. 알려진 이슈 참고.
- **Velero 데이터 이동 파드에 `app.kubernetes.io/name=velero-data-mover` 라벨 부여 (#422, #440).** 라벨은 node-agent
  ConfigMap `velero-node-agent-config`의 `podLabels`에서 오며, node-agent DaemonSet 파드가 아니라 node-agent가 볼륨
  백업을 위해 만드는 파드에 적용됨. `pods/log`와 이벤트 거부에 대한 문서를 추가함.

- **Ambient 프로브와 HBONE.** GitOps NetworkPolicy가 이제 kubelet 프로브와 포트 15008을 허용함(#240, #241).
- **네트워킹.** APISIX values 렌더링, Cilium 풀이 `POD_NETWORK_CIDR`을 따름, APISIX viewer 키가 비어 있으면
  스크립트가 중단됨(#236, 0ca929e). Prometheus 파드는 ambient 데이터플레인에서 제외됨(dee1dca).
- **재부팅 후 NFS PVC 마운트.** `rpc-statd`가 이제 부팅 시 시작됨(#249).
- **대시보드.** CSRF Secret이 `apiVersion`과 함께 렌더링되어 앱이 동기화됨(#258).
- **에어갭 이미지 다이제스트**를 업스트림 드리프트 이후 갱신(#194, #259, #310)하고, 포털 Kaniko Job이 고정한
  빌드 헬퍼 다이제스트를 번들에 포함(#262).
- **Keycloak HTTPS 준비 게이트**가 관리 포트 대신 `/realms/master`를 조사함(#231).

### 보안

- `portal-release.yml`이 `workflow_run` ref를 쓰는 두 번째 `actions/checkout`을 더 이상 사용하지 않음. OpenSSF
  Scorecard가 Dangerous-Workflow로 지적하던 패턴임 (#395, #433). 태그를 git으로 가져와 실행의 `head_sha`를 가리키는지
  증명한 뒤에만 `tagged/` worktree로 추가하며, 신뢰 모델은 그대로임. 실제 `portal/v*` 릴리스로 아직 실행되지 않았고 Scorecard 점수도
  재검사하지 않았음.
- `database` 기본 ingress 차단, Gitea ingress를 APISIX를 거치지 않는 정당한 소스로 제한, APISIX Admin API는
  파드 CIDR과 루프백에서만 접근 가능.
- 게이트웨이가 더 이상 클라이언트가 보낸 `X-WEBAUTH-USER`를 신뢰하지 않음 (492e65a).
- ArgoCD 읽기 전용 신원을 가진 비공개 GitOps 리포지토리, AppProject 경계, 클러스터 전역 `exec`나 변경 권한이 없는
  개발자 역할.
- 단기 포털 OpenBao 신원, Keycloak OIDC TLS 검증과 CA 신뢰, `root_squash` NFS export.
- Kakao 바스천 SSH 허용 목록과 검증된 호스트 키 (e496631, 8f24bcf).

### 알려진 이슈

- **이미지 서명이 집행되지 않음.** Kyverno `verify-image-signatures` 정책은 `Audit` 전용이며 `required: false`임.
  이제 리포지토리 Cosign 공개 키(`scripts/airgap/keys/cosign.pub`, #389; 드리프트 시 R74/R75c 실패)를 포함하고
  에어갭 검증을 위해 Rekor 조회를 건너뛰므로(#391), 서명되지 않은 이미지를 보고하지만 아무것도 차단하지 않음
  (#35). 포털 1.1.0은 서명되었으며 1.0.19 및 이전 버전은 서명되지 않음.
- **부트스트랩 중 메시 전역 PERMISSIVE.** `09-istio-ambient.sh`가 메시 전역 PERMISSIVE `PeerAuthentication`을
  적용하고, GitOps 차트는 같은 이름으로 `STRICT`를 선언하므로 ArgoCD가 수렴하기 전까지 트래픽이 fail-open임
  (#126).
- **`monitoring-permissive`**는 자체 주석이 워크로드별 범위를 권장함에도 `monitoring` 네임스페이스 전체를
  PERMISSIVE로 설정함. R243이 개수를 고정하여 확산되지 않으며, R244가
  `monitoring`과 `platform-system`을 ingress 기본 차단 없이 PERMISSIVE인 네임스페이스로 추적함(#126, #339).
- **`dev` 네임스페이스 라벨 충돌.** `gitops/resources/dev-namespace.yaml`은
  `istio.io/dataplane-mode: none`을 설정하지만 `09-istio-ambient.sh`는 `dev`를 `AMBIENT_NAMESPACES`에 넣고
  ambient로 라벨링함(#128). 라이브 클러스터에서 어느 쪽이 우선하는지는 확인되지 않음(unverified).
- **Ingress 격리가 부분적임.** `gitops/resources`에서 ingress를 격리하는 정책은 `database`, Gitea, APISIX Admin
  API, 노드 튜닝 Job(전체 차단)뿐이며 `monitoring`과 `platform-system`에는 없음(차트에 포함된 정책은 전수 확인하지
  않음).
- **`portal-reader` 역할은 설계상 `sync */*`를 유지함.** 포털의 서비스 신원은 모든 프로젝트를 동기화하며, 범위를
  좁히려면 별도 검토가 필요함. R246(#342)은 ArgoCD 정책을 줄 집합, 파싱된 `argocd-rbac-cm` data, 치환식 기준으로
  검사하지만 렌더링된 Helm 결과, 다른 차트, `argocd-cm`, 부트스트랩 heredoc 밖의 `kubectl` 변경은 보지 않음.
- **에어갭 번들 복사는 OCI referrer(SBOM, 서명, attestation)를 보존하지 않음.** `09`는 손실을 보고하지만
  실패시키지 않음(#124).
- **다이제스트 인벤토리는 최초 저장 시점 신뢰(trust-on-first-save)임.** `images.digests.json`에는 서명이 없으므로
  번들이 `02`가 기록한 것과 일치함은 증명하지만 `02`가 올바른 바이트를 보았음은 증명하지 않음(#124).
- **S3 인증 운영 작업: 아직 남은 것 (#414, #415).** 2026-10-19 kakao-narwhal에서 검증됨: 롤 표식이 자가 치유
  후에도 유지되고, Application이 Synced이며, 익명 검사가 통과하고, S3 서버를 롤링한 뒤에도 네 클라이언트 모두
  클라이언트별 I/O 검사를 통과함. `08-4-storage.sh` 가드는 인증된 그 클러스터에 읽기 전용으로 실행해 통과를
  확인했고, 익명 클러스터에서의 중단 경로는 가짜 `kubectl` 테스트와 독립 리뷰의 시뮬레이션으로만 확인했으며
  `08-4`를 라이브에서 다시 실행하지는 않았음. 이후 #442가 클라이언트 Secret을 서버 사이드 어플라이로 적용하고
  `kubectl.kubernetes.io/last-applied-configuration` 어노테이션에서 키를 제거함(라이브 클러스터에서 다시 검증하지
  않음). 여전히 미해결: 가드가 확인하는 `envFrom` 참조는 Loki/Tempo 파드가 재시작되어 새 키를 로드했음을 증명하지 않음.
  같은 Application은 `Secret seaweedfs-db-secret`을 비교 상태 없이 표시하며, 이는 조사하지 않았음.
- **`root_squash`에서는 소유자 전용 파일이 있는 볼륨의 Velero 볼륨 백업이 실패함 (#431).** NFS 서버에서 다른
  사용자가 읽을 수 없는 파일이 `database/`(7686개 중 7677개), `monitoring/`(최초 측정 1317개 중 138개, 이후 1296개 중 115개: 데이터가 계속 변함), `devtools/`(292개
  중 72개), `storage/`(77개 중 7개)에서 발견됨. 소유자가 데이터 이동 파드에 맞는 신원 부여, 애플리케이션 수준
  백업(OpenBao는 백업 훅으로 `bao operator raft snapshot`), NFS 서버에서의 백업 중에서 결정하기 전까지는
  OpenBao, PostgreSQL 및 소유자 전용 파일이 있는 다른 볼륨의 Velero 백업에 의존하지 말 것. CNPG 자체
  barman 백업은 S3로 별도 수행되며, 2026-10-18에 온디맨드 백업이 완료되고 WAL 아카이빙이 진행됨을 확인함.
  봉쇄 조치(1단계): 스케줄 실행에서 `PodVolumeBackup`이 Canceled된 다섯 볼륨(OpenBao `data`, CNPG `pgdata`, Gitea
  `data`, Harbor jobservice `job-logs`, SeaweedFS 마스터 `data-storage`)에 파드 어노테이션 `backup.velero.io/backup-volumes-excludes`를 부여함
  (OpenBao·Harbor·SeaweedFS(`master.podAnnotations`) 차트, CNPG `Cluster`의 `inheritedMetadata`, Gitea 차트로 설정). 스케줄 백업이 `InProgress`로
  멈춰 뒤 스케줄을 막는 대신 끝나도록 하기 위한 것임. 멈춤만 막을 뿐이며 **Velero는 여전히 이 볼륨들을 보호하지
  않음.** OpenBao는 새 애플리케이션 수준 스냅샷(#443, 아래, 아직 어느 클러스터에도 활성화되지 않음)에 의존하고, PostgreSQL은 CNPG barman에 의존하며, Gitea `data`,
  Harbor `job-logs`, SeaweedFS 마스터 `data-storage`(raft 메타데이터)는 보호되지 않으며, 마스터의
  `seaweedfs-master-log-volume`은 계속 백업됨. 이 어노테이션을 적용하면 OpenBao, CNPG, Gitea, Harbor jobservice,
  SeaweedFS 마스터 파드가 롤링됨. 라이브 클러스터에서 검증하지 않음. 근본 해결을 위해 #431은 계속 열려 있음. **OpenBao 2단계 (#443):** Velero fs-backup이 읽을 수 있는 PVC에
  일일 암호화 raft 스냅샷을 저장하는 코드가 추가됨(추가 항목 참고). **어느 클러스터에도 활성화되지 않았음.** 운영자 키,
  `13-2` 재실행, 배포가 필요하며(업그레이드 참고) 정적 검사와 로컬 OpenSSL 실행만 수행했음. 한계: **`root_squash`
  환경에서 Gitea `data`, SeaweedFS 마스터 `data-storage`, Harbor jobservice `job-logs` 볼륨은 여전히 Velero
  fs-backup으로 보호되지 않음.** `openbao-init` Secret(Shamir 언실 키와 root 토큰)은 여전히 Velero 백업 안에
  있으므로 스냅샷은 오프라인 키로 보호되지만 클러스터의 언실 시크릿은 보호되지 않음. OpenBao 스냅샷 복원은 훈련으로
  검증하지 않았고, 스냅샷 최신성 알림도 아직 없음. 라이브 미검증: 실제 스냅샷 생성, Kubernetes-auth 로그인, 클러스터
  안에서의 `openssl cms`, uid 100의 NFS 쓰기, job 파드에 대한 Velero 제외 라벨의 효과, holder `PodVolumeBackup`
  완료, 복원 드릴.
- **Velero 스케줄은 emptyDir도 백업하며 실패 원인을 진단하기 어려움 (#422).** 서버 계정에 `pods/log`와
  `events create`가 없어 임시 권한 없이는 데이터 이동 파드 로그를 읽을 수 없었음(#431은 이렇게 발견됨).
  이제 생성기가 만드는 `velero-pod-logs` Role로 `storage` 네임스페이스의 `pods/log` `get`을 부여함(라이브 클러스터에서
  검증하지 않음). `events create`는 쓰기 동사가 필요하고 거부된 네임스페이스가 확인되지 않아 여전히 없음.
  node-agent가 만드는 데이터 이동 파드에는 이제 `app.kubernetes.io/name=velero-data-mover` 라벨이 붙지만(#440,
  2026-10-20 라이브 적용, 단 Kyverno 이벤트가 실제로 줄었는지는 아직 검증하지 않음), Velero v1.18은
  repository-maintenance Job에 라벨을 붙일 수 없어 해당 Kyverno audit 이벤트는 남음. `events create` 거부는 문서화만
  되었고 수정되지 않았음.
- **Tempo가 1536Mi 제한에서 만성적으로 OOMKilled됨 (#420).** S3 변경과 무관하게 라이브 클러스터에서
  매주 수백 건의 재시작이 발생함. #439는 메모리 ballast를 0으로, `GOMEMLIMIT`를 1280MiB로 설정했으나 가설이며,
  재시작이 멈추는지는 아직 관찰되지 않았고 롤아웃 시 flush되지 않은 WAL을 잃음(`persistence` 꺼짐).
- **라이브 클러스터에 적용되지 않음.** AppProject 분리, 개발자 RBAC 변경, 비공개 Gitea 리포지토리, Valkey 교체의
  커밋 메시지는 실행 중인 narwhal 클러스터가 아니라 정적 검사 또는 임시 클러스터에서 확인했다고 명시함.
  이후 라이브 kakao-narwhal 클러스터에서 검증 완료됨: S3 인증, Velero 백업 및 복원(2026-10-18), Velero RBAC 권한
  (2026-10-19), S3 운영 수정(2026-10-19), NFS의 XFS 및 `root_squash` 전환(2026-10-16).

## [1.2.0] - 2026-08-08

Vagrant (arm64) 및 Kakao Cloud (amd64) 환경 모두에서 에어갭이 "이미지 전용"에서 인터넷 경로 없이
완전히 완료되는 설치 방식으로 전환됨.

### 추가
- **실제로 폐쇄된 폐쇄망 설치**: 번들에 Helm 차트, 바이너리(helm/cilium/hubble/yq),
  원격 매니페스트 및 OS 패키지가 포함됨. 이전에는 컨테이너 이미지만 번들링되고 나머지는 이그레스
  프록시 뒤에 숨겨진 공용 인터넷에서 가져왔음. `AIRGAP=1` 설정 시 APT를 `file:///srv/airgap/apt`로
  전환하고 기본 라우트를 삭제하여 격리가 추정이 아닌 강제로 적용됨.
- **일등 시민(First-class) 프로바이더로서의 Kakao Cloud**: OpenTofu 인프라 생성, 바스천
  프록시/레지스트리 단계, 바스천 dnsmasq에서 제공하는 `kakao.narwhal.internal` 서비스 존,
  아키텍처별 번들, 접속을 위한 `set-config-kakao.sh` / `setup-hosts-kakao.sh` 제공.
- **격리 도구**: `scripts/test/verify-isolation.sh`가 노드별 라우트, networkd 드롭인,
  직접 이그레스, 미러 도달 가능성, APT 소스를 검사함. `airgap-isolate-kakao.sh`는 이미
  프로비저닝된 노드를 격리함. `docs/common/airgap-isolation-testing.md`에 문서화됨.
- **Chaos Mesh 2.8.3 + k6 부하 테스트 수트**: Grafana 대시보드 및 Prometheus remote-write
  리시버 포함, Dr. Pym 적정 규모 산정(rightsizing) 권장 사항 포함.
- **클린 설치 회귀 테스트 수트** (`regression-check-kakao.sh`, 36개 정적 + 런타임 검사,
  각 검사는 날짜가 기재된 lessons-log 항목에 매핑됨)가 모든 푸시 시 CI에 연동됨.

### 수정
- **에어갭 미러가 단 하나의 풀(pull)도 처리하지 못했던 문제**: Ubuntu의 containerd 2.2.1이
  `config_path`를 콜론으로 구분된 쌍으로 제공했으나, containerd가 이를 허용하지 않아 해당
  문자열 자체를 디렉터리 이름으로 찾고 모든 `hosts.toml`을 무시했음. 한 노드에서 두 방식을 측정한
  결과: 콜론 형식 → `network is unreachable`, 단일 경로 → 모든 이미지를 미러에서 풀함(레지스트리
  로그 containerd 요청 0개 → 132개).
- **미러 커버리지**: 이미지 목록의 11개 레지스트리 중 5개만 `hosts.toml`을 가지고 있어
  istiod, istio-cni, gitea, argocd-redis가 업스트림에서 풀되었음. 이제 목록은 `images.txt`에서
  추출됨.
- **GitOps가 오프라인에서 동기화되지 않던 문제**: 모든 ArgoCD Application이 공개 차트
  리포지토리를 지정하고 있었음. 이제 차트는 클러스터 내 Gitea Helm 레지스트리에서 가져옴.
- **격리가 DHCP 갱신 시 유지되지 않던 문제**: `ip route del default`가 다음 임대 시 취소되었으나,
  networkd 드롭인(`UseGateway=false`)을 통해 지속성을 확보함. Kakao에서는 게이트웨이만 삭제됨(임대
  정보에 메타데이터/NTP 라우트도 포함됨).
- **Phase 2가 플랫폼 앱이 전혀 없는 상태에서도 성공을 보고할 수 있었던 문제**: Gitea 및
  GitOps 부트스트랩 단계가 경고만 출력하고 계속 진행되어, 백엔드가 없는 503 상태의 4개 서비스가
  남았음. 두 단계 모두 이제 필수(critical) 처리됨.
- NFS를 NLM 헬퍼 포트가 고정된 v3로 전환(v4.1 클라이언트/서버 교착 상태로 약 24시간 동안
  스토리지 중단 발생), kube-apiserver 힙 제한 설정, ArgoCD, Cilium operator, Tempo, Prometheus
  전반에 리소스 requests/limits 설정.

### 보안
- 하드코딩된 S3 자격 증명과 오래된 공인 IP를 리포지토리에서 제거하고, 실수로 커밋된
  6.8 GB 크기의 레지스트리 블롭 스토어를 히스토리에서 정리함 (5.79 GiB → 3.86 MiB).
- 추적(tracked)되면서 *동시에* gitignore된 파일이 존재할 경우 빌드를 실패시키는 신규 검사
  R35 추가 (`.gitignore`는 소급 적용되지 않으므로 해당 블롭 스토어가 유입되었던 원인 차단).

### 제거
- KubeMetal MLOps 연동(MLflow/SeaweedFS/Prefect) 제거. kubemetal은 이제 자체 네임스페이스에
  Helm OCI 차트를 통해 에이전트만 설치하므로 gitops 내보내기의 호출자가 없어졌음. 이에 따라
  두 번들 모두에서 mlflow 및 prefect 이미지가 함께 제거됨.

## [1.1.0] - 2026-07-13

당시 CHANGELOG 항목 없이 릴리스되었으며, 히스토리의 연속성을 위해 `v1.0.0..v1.1.0` 사이의
83개 커밋을 바탕으로 여기에 재구성함.

### 추가
- 제로클릭(zero-click) Keycloak SSO가 적용된 Kubernetes Dashboard 3.0 (차트 7.14.0, 벤더링됨 —
  업스트림 Helm 인덱스가 404 응답).
- 포털과 동기화된 듀얼 모드(라이트/다크) Keycloak 로그인 테마 및 로그아웃 자동 리다이렉트 기능.
- 모든 네임스페이스에 PSA audit/warn 라벨 적용 (KISA-POD-01); 거버넌스 페이지용 Trivy
  컴플라이언스 스캐너 추가.
- ArgoCD Application으로 관리되는 정책, 알림, RBAC 리소스.
- 최초의 `PROVIDER=kakao` 기반 작업: kube-vip 및 Vagrant 전용 경로를 건너뛰는 가드,
  MetalLB→NodePort 인그레스, 바스천 호스트, LB 공인 IP 및 고정된 연속 사설 IP.

### 수정
- argocd-redis를 앰비언트 메쉬(ambient mesh)에서 제외 — 반복적인 EOF 교착 문제의 근본 원인 해결.
- APISIX 프록시 버퍼 크기 증설; 지나치게 큰 `Set-Cookie`로 인해 포털 로그인 콜백에서
  502 에러가 발생하던 문제 해결.
- Harbor의 비결정적 차트 시크릿으로 인해 약 5분 간격의 무한 롤아웃이 발생하던 문제 해결.
- SLO 체인을 위한 로그아웃 후 리다이렉트 URI 등록.

## [1.0.0] - 2026-07-08

첫 번째 안정화 릴리스 — 연속적인 수정 없는 처음부터의 클린 설치로 검증됨
(6노드 HA 컨트롤 플레인, Ubuntu 26.04 / 커널 7.0, ARM64).

### 주요 변경
- **IDP 포털(Next.js 16 + React 19)이 고정된 공개 이미지로 제공됨**: `ghcr.io/dasomel/narwhal-portal:1.0.0` (GHCR, 멀티 아키텍처); 클러스터 내 Kaniko 빌드는 선택적 개발자 셀프서비스 도구로 변경됨.
- **Harbor 보안 강화**: 내부 공유 시크릿을 `harbor-shared-secrets` K8s Secret으로 외부에 분리(git에 평문 없음); 모든 컴포넌트 이미지를 불변 태그 `:v2.15.1`로 고정(ARM64 커널 7.0 노드에서 오래된 `:latest` amd64 레이어 CrashLoopBackOff — `exec format error` — 수정); 메트릭/exporter 비활성화.
- **포털 ↔ 클러스터 연동부 종단 간 수정**: `monitoring` PERMISSIVE mTLS 예외 처리(메트릭/로그/트레이스/알림이 메쉬 외부 포털에 도달함), 재시도 보강된 ArgoCD API 토큰 및 ServiceAccount 토큰 발급, 포털 신뢰 번들에 kube-apiserver CA 추가, 2코어 워커 노드에 맞게 trivy 스캔 작업 CPU/메모리 튜닝.
- **커널 7.0에서 Falco 비활성화** (`modern_ebpf` `scap_init` 호환성 문제, mistakes log에 문서화됨) — trivy-operator, kyverno, NetworkPolicy 및 STRICT mTLS를 통해 런타임 보안 커버리지 유지.
- CI (Lint & Validate / ShellCheck) 정상 통과; README (영문 + 국문)에 실시간 포털 스크린샷 갤러리 추가.

### 변경
- **도메인 마이그레이션 `local.narwhal.io` → `local.narwhal.internal`** (2026-06-28): 총 61개 소스
  파일 업데이트. 사유: `narwhal.io`는 실제 공개 도메인으로, 와일드카드 DNS 항목이 내부 클러스터
  도메인을 가려 로컬 dnsmasq 대신 외부 DNS 확인이 우선되는 문제가 있었음. `.internal` TLD는
  사용을 위해 ICANN에 의해 예약된 TLD로 공개적으로 해석되지 않으므로 샤도잉 문제가 해결됨.
  다음 클린 설치부터 적용되며, 기존 라이브 클러스터는 여전히 `.io`로 동작함.

### 추가
- OIDC RBAC 테스트 섹션 (`test-sso.sh` 8/8: 15개 체크)
- Keycloak `kubernetes` 클라이언트 audience mapper (K8s API 서버 `aud` 클레임 검증)
- `--oidc-ca-file` API 서버 플래그 (self-signed 인증서 JWKS 검증)
- `microprofile-jwt` 스코프 중복 groups 매퍼 자동 정리
- SSO 웹 서버 Istio ambient mesh opt-out (`istio.io/dataplane-mode: none`)
- 앱별 접근제어(인가) 강화: 4계층(cluster-admin/developer/viewer/guest) 전체 적용
  - ArgoCD: guest→`role:none` 명시적 차단 (deny 정책 추가)
  - Gitea: `narwhal` Organization + Developers/Viewers 팀 자동 생성, `--restricted-group guest` + `--group-team-map` 설정
  - Harbor: 프로젝트 `library`에 developer→Developer, viewer→Guest 그룹 멤버 자동 설정
  - OAuth2-Proxy: `allowed_groups` 필터 추가 (guest 그룹 차단)
- `test-sso.sh` 9/9 앱별 접근제어 검증 섹션 추가

### 수정
- `kcadm.sh --format csv --noquotes` 잘못된 ID 반환 → jq 기반 조회로 교체 (11곳)
- `test-sso.sh` 구 네임스페이스 참조 (keycloak→iam, argocd→devtools 등)
- `kubectl auth can-i` 경고 메시지로 인한 테스트 false negative
- Istio ambient mesh ztunnel SSO 쿠키 손상 → ArgoCD, Grafana, Harbor, Gitea 등 SSO 실패
- Gitea OAuth2 소스 이름 대소문자 불일치 (`Keycloak` → `keycloak`)

## [0.2.0] - 2026-02-24

### 추가
- 기능별 네임스페이스 통합 (`platform-system`, `iam`, `devtools`, `storage`, `dev`)
- 4사용자 체계: `admin`, `dev`, `view`, `guest`
- 4그룹 체계 (단수형): `cluster-admin`, `developer`, `viewer`, `guest`
- OIDC 그룹 기반 K8s RBAC (ClusterRoleBinding + RoleBinding)
- Keycloak HTTPRoute 조기 생성 (OIDC 검증 전 필수)
- Keycloak HBONE NetworkPolicy (`keycloak-allow-hbone`, 포트 15008)
- ArgoCD ambient mesh opt-out (`istio.io/dataplane-mode: none`)
- ArgoCD ClusterRoleBinding namespace 자동 패치 (devtools)
- CoreDNS hairpin fix (Traefik ClusterIP로 `*.local.narwhal.internal` 해석)
- README에 릴리스/라이선스 배지

### 변경
- 네임스페이스 재구조화: 개별 OSS NS → 기능별 통합 NS
  - `metallb-system`, `traefik`, `cert-manager`, `cnpg-system`, `kyverno` → `platform-system`
  - `keycloak`, `oauth2-proxy` → `iam`
  - `argocd`, `gitea`, `harbor`, `headlamp` → `devtools`
  - `seaweedfs`, `velero`, `openbao` → `storage`
- 그룹명 복수→단수: `cluster-admins`→`cluster-admin`, `developers`→`developer`, `viewers`→`viewer`
- 사용자명 변경: `k8s-admin`→`admin`, `developer`→`dev`
- PeerAuthentication STRICT → PERMISSIVE (kubelet probe + external 트래픽 허용)
- Keycloak hostname v1(`hostname-url`) → v2(`hostname.hostname` + `hostname.strict`)

### 수정
- Keycloak Operator NetworkPolicy에 HBONE 15008 누락 (mesh-to-mesh 통신 실패)
- ArgoCD ClusterRoleBinding subject namespace 불일치 (`argocd`→`devtools`)
- ArgoCD pods CrashLoopBackOff (ztunnel이 kubelet probe 차단)
- yq OIDC URL에 셸 따옴표 삽입 → API 서버 크래시
- API 서버 OIDC 플래그 추가 시점 문제 (HTTPRoute 미존재)

## [0.1.0] - 2026-02-20

### 추가
- Vagrant 기반 Kubernetes v1.35 IDP 클러스터 자동 프로비저닝
- 2-Phase 프로비저닝 구조 (Phase 1: 클러스터 인프라, Phase 2: 플랫폼 앱)
- HA Control Plane: 3 masters + kube-vip VIP (192.168.56.100)
- CNI: Cilium v1.19 (kube-proxy replacement) + Hubble 네트워크 옵저버빌리티
- Service Mesh: Istio v1.29 ambient mode (zero sidecars, ztunnel mTLS)
- Gateway API: Traefik v3.6 + cert-manager self-signed TLS
- Storage: NFS (Block) + SeaweedFS (S3) + nfs-quota-agent
- Database: CloudNative-PG v1.28 통합 `narwhal-db` (Keycloak, Gitea, Harbor 공유)
- IAM/SSO: Keycloak v26.5 OIDC (6개 앱 연동: ArgoCD, Grafana, Gitea, Harbor, Headlamp, OAuth2-Proxy)
- GitOps: ArgoCD v3.3 + Gitea v1.25 (App-of-Apps 패턴)
- Observability: Prometheus + Grafana + Loki + Tempo + Promtail
- Security: cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- Backup: Velero + CNPG barman → SeaweedFS S3
- Networking: MetalLB (LoadBalancer), dnsmasq (로컬 DNS)
- Dashboard: Headlamp + OAuth2-Proxy
- 클러스터 검증 스크립트 (`verify-cluster.sh`, `test-sso.sh`)
- SSO CA cert 자동 배포 (Headlamp, Grafana, ArgoCD)
- DNS HA: 모든 master 노드에 dnsmasq, CoreDNS forward 설정

### 변경
- 3 CNPG 클러스터 → 통합 `narwhal-db` (HA failover, ExternalName 서비스)
- 개별 스크립트 → kube-ready-box 기반 공통 스크립트 분리
- 토폴로지: 단일 노드 → 3m+3w (Master NoSchedule, Worker에서 플랫폼 앱 실행)
- NFS StorageClass: 계층적 subDir + 최적화된 mountOptions

[Unreleased]: https://github.com/dasomel/narwhal/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/dasomel/narwhal/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/dasomel/narwhal/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/dasomel/narwhal/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dasomel/narwhal/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/dasomel/narwhal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
