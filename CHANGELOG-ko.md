# Changelog

이 프로젝트의 주요 변경 사항은 이 파일에 기록됩니다.

이 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르며,
이 프로젝트는 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 준수합니다.

[English](CHANGELOG.md) | 한국어

## [Unreleased]

- **포털이 `narwhal-portal:1.1.13`으로 고정됨 (기존 1.1.9).** 1.1.10은 경보 페이지와 알림 채널 설정을
  추가하고(#610, #614), Trivy 데이터를 다시 가져온 뒤에도 부분 결과 안내를 표시한다(#505, #615).
  1.1.11은 Figma 분할 레이아웃 로그인(#619, #620)을 적용해 새 Keycloak 로그인 테마(#621)와 맞춘다.
  1.1.12는 Figma 앱 셸(그룹형 사이드바), 대시보드, 빈 상태 디자인을 적용한다(#624, #634).
  1.1.13은 아키텍처 페이지와 온보딩 플랫폼 아키텍처 그림을 다시 디자인하고(#640) 사용자 목록의 권한·세션 시각 표시를
  고친다(#641).
  이미지는 저장소 키로 Cosign 서명되어 있다(index digest `sha256:148ea713…`). 클러스터 bastion에서 이
  고정을 적용하기 전에 digest로 미러링해야 한다(`scripts/airgap/README.md`의 "Mirror a pin bump").
  로컬 Vagrant 클러스터는 ghcr.io에서 직접 pull한다.

## [1.4.0] - 2026-11-09

1.3.0 하드닝 이후의 안정화 릴리스. 노드 수준 OOM 종료, 호스트 디스크 증가, etcd/NFS I/O 사고 이후 로컬
클러스터를 다시 손봤다(8GB 워커 VM, 전용 NFS 데이터 디스크, 크게 줄어든 Trivy 스캔 쓰기량, 백업 경로와
모니터링 스택의 QoS와 분산 배치). Keycloak, Velero UI, 게이트웨이 관리자 UI는 이제 `cluster-admin` 그룹에
묶이고, 그동안 눈에 띄지 않던 장애(CNPG WAL 아카이빙, 메시 인증서, 오래된 Trivy DB 미러, Velero 백업, VM
디스크 증가)에 대한 경보가 추가되었다. 기존 클러스터에서 운영자 조치가 필요한 변경이 여러 개 있으므로
먼저 **업그레이드 참고**를 읽을 것.

### 업그레이드 참고

- **워커 VM 메모리가 8GB (#597).** `WORKER_MEMORY`가 6144에서 8192로 오르고(마스터는 6GB 유지) VM 합계가
  약 42GB라 호스트 RAM 48GB 이상이 필요하다(README). 실행 중인 VM은 예전 `memsize`를 유지하므로 기존
  클러스터는 워커를 한 대씩 롤링 재시작해야 한다: cordon과 drain, `vagrant halt <worker> && vagrant up
  <worker> --no-provision`, uncordon, 할당 가능 메모리가 약 7.3GiB인지 확인한 뒤 다음 워커. `narwhal-db`가
  이제 anti-affinity를 필수로 요구하므로(#590) drain 전에 CNPG primary 위치를 확인하고 그 워커에 있으면
  먼저 switchover하며, 워커 두 대를 동시에 내리지 않는다. 전체 절차와 롤백:
  `docs/vagrant/operations.md` 13절.
- **NFS export가 master-1의 전용 디스크로 이동 (#605, #606). 기존 클러스터는 소유자가 직접 수행하는 점검
  작업이 필요하다.** 디스크를 붙이려면 master-1을 꺼야 하고 그동안 NFS를 쓰는 모든 워크로드가 멈춘다.
  100GB 미만이면 30~60분으로 추정하며 측정하지 않았다. 먼저 Velero 백업, CNPG 논리 덤프, etcd 스냅샷을
  받고 `docs/vagrant/operations.md` 15절을 따른다(`docs/runbooks/nfs-xfs-migration.md`의 3~7단계를 재사용).
  **디스크가 이미 있는 머신에서 `NARWHAL_NFS_DISK_GB=0`을 쓰지 말 것:** 디스크 없이 부팅하면 루트
  디스크에 남은 옛 복사본을 export하므로 Vagrantfile이 중단한다. 새 클러스터는 디스크(기본 200GB)를
  자동으로 받는다.
- **Keycloak: 영구 master realm 관리자로 이전 (#598).** 새로 설치하면 Phase 2에서 자동으로 한다. 기존
  클러스터는 master-1에서 `scripts/cluster/11-1-keycloak-admin.sh`를 한 번 실행한다. `platform-admin`을
  만들고 자격증명을 `iam/keycloak-admin`에 저장하며, 새 계정이 관리 API를 호출할 수 있는지 확인한 뒤에만
  `temp-admin`을 삭제한다. 다시 실행해도 결과가 같다. 이전하기 전까지 스크립트는
  `iam/keycloak-initial-admin`으로 폴백한다. 절차와 복구: `docs/runbooks/keycloak-permanent-admin.md`.
- **168h 메시 인증서 TTL을 받으려면 ztunnel과 waypoint를 재시작 (#601).** 새 TTL은 클라이언트가 요청하므로
  동기화 전에 발급된 인증서는 `kubectl -n istio-system rollout restart daemonset/ztunnel`과 각 `waypoint`
  Deployment의 rollout restart 전까지 예전 24h 수명을 유지한다. `istio_agent_cert_expiry_seconds`가 604800에
  가까운지 확인한다. 절차: `docs/vagrant/operations.md` 14절.
- **`gitops/resources/narwhal-db.yaml`을 수동으로 적용 (#590, #602, #605).** CNPG `Cluster`는 어떤 Argo CD
  Application도 소유하지 않고 `14-gitops-bootstrap.sh`가 적용하므로, Gitea에 push하고 동기화해도 라이브
  객체는 바뀌지 않는다. `kubectl apply -f gitops/resources/narwhal-db.yaml`을 실행하면 호스트명 기준 필수
  anti-affinity, 더 긴 liveness와 isolation 점검, 명시적 메모리 requests/limits, PodMonitor가 적용된다.
  CNPG 인스턴스는 롤링 재시작된다. 먼저 두 인스턴스가 서로 다른 워커에 있는지 확인하지 않으면 하나가
  Pending으로 남는다.
- **새 차트를 Argo CD가 동기화하기 전에 `trivy-server` StatefulSet을 삭제 (#584).** 캐시가 `emptyDir`에서
  5Gi `nfs-csi` PVC로 바뀌었고 `volumeClaimTemplates`는 변경할 수 없다:
  `kubectl -n security-system delete sts trivy-server`(걸려 있는 동기화 재시도도 비운다). 이전 캐시에는
  잃어도 되는 데이터만 있었다.
- **Prometheus, Tempo, Loki를 한 번 재스케줄 (#586).** 새 preferred anti-affinity는 파드가 스케줄될 때만
  적용된다. `tempo-0`, `loki-0`, Prometheus 파드를 한 번에 하나씩 삭제하고 `kubectl -n monitoring get pod
  -o wide`로 배치를 확인한다. Tempo와 Loki는 영속 저장소 없이 돌므로 flush되지 않은 데이터는 OOM 재시작과
  마찬가지로 사라진다.
- **기존 클러스터에서 `08-5-registry.sh`, `11-2-keycloak-config.sh`, `11-3-keycloak-clients.sh`를 다시
  실행.** `08-5`는 Trivy DB 갱신이
  쓰는 Harbor robot 계정과 보존·GC 설정을 만든다(#603). `11-3`은 Velero UI JWT 서명 키를 생성하고(#555)
  `cluster-admin` 전용 로그인 흐름을 `velero-ui` 클라이언트에 연결한다(#599). `11-2`는 `narwhal`과 `master`
  realm의 `loginTheme`·`adminTheme`을 `narwhal`로 설정하며, 관리 콘솔 브랜딩에 필요하다(#572).
- **예전 스크립트로 미러링된 Gitea 디렉터리를 다시 push (#578).** 이 릴리스 이전의
  `push-to-gitea.sh <dir>`은 이미 있는 디렉터리를 중첩시키고(`<dir>/<basename>/...`) 삭제를
  전파하지 않았다. 해당 경로에 대해 다시 실행하면 이제 Gitea 사본이 정확히 교체된다.

### 추가

- **`csp/kakao-cloud/terraform/destroy-all.sh`로 Kakao Cloud 배포 전체를 삭제 (D-KAKAO-TEARDOWN).**
  `prevent_destroy` 가드 6개(공인 IP와 NFS 데이터 볼륨)를 풀고 `tofu destroy`를 실행한 뒤, 잡을 수 있는
  모든 종료(성공, 취소, 실패, Ctrl-C, TERM, HUP, QUIT; SIGKILL만 예외)에서 가드를 되돌린다. R292가 stub `tofu`로 동작을 검증한다. terraform README의
  "정리" 절에 전체 삭제로 사라지는 것을 데이터까지 정리했다. NFS 볼륨과 함께 NFS로 제공되는 모든 PVC가,
  SeaweedFS 객체 저장소가 있는 워커 디스크와 함께 클러스터 안 Velero·CNPG 백업이 사라진다. 거부된 VPC
  삭제(`ACTIVE`)와 오래 걸리는 삭제(`PENDING_DELETE`)를 구분하고, 수동 방법 B에는 볼륨 자체의 가드가
  전체 삭제를 막을 때 NFS 볼륨을 남기는 방법을 적었다.
- **master-1의 전용 NFS 데이터 디스크 (D-NFS-VMDK, #605, #606).** etcd와 NFS PV(사고 보고 기준 17개)가 VMDK
  하나를 같이 써서, NFS 쓰기 폭주 때 etcd WAL fsync가 8초를 넘겼고(2026-11-08) API 타임아웃, CNPG liveness
  재시작, failover가 이어졌다. Vagrantfile이 이제 sparse 200GB 디스크(`NARWHAL_NFS_DISK_GB`)를 붙이고
  `01-nfs-server.sh`가 이를 XFS(`prjquota`)로 포맷해 UUID로 마운트한다. `NFS_DATA_DEVICE=auto`는 파티션·마운트·
  파일시스템이 없는 디스크가 정확히 하나일 때만 고르고 아니면 실패한다. vmware_desktop에서는 `nvme0:1` vmx
  키와 `vmware-vdiskmanager`를 실행하는 `before up/reload` 트리거로 붙인다. `config.vm.disk`가
  vagrant-vmware-desktop 3.0.5에서 `vagrant up master-1`을 깨뜨리기 때문이다(#606). VirtualBox는 `vm.disk`를
  쓴다. 디스크가 이미 있는데 `NARWHAL_NFS_DISK_GB=0`으로 부팅하는 것은 거부된다(R327). 이전 런북은
  `docs/vagrant/operations.md` 15절이며 업그레이드 참고를 볼 것. 확인하지 못한 것: 새 `vagrant up`/`reload`,
  새 클러스터의 auto 디스크 선택, VirtualBox, etcd fsync가 실제로 개선되는지(두 VMDK가 같은 호스트 SSD에
  있다).
- **VM 디스크 증가 경보와 호스트 점검 (D-VM-DISK-GUARD, #582).** 게스트가 43G를 쓰는데 worker-3의 vmdk가
  호스트에서 298GiB에 이르렀다. 성장형 linked clone vmdk는 해제된 블록을 돌려주지 않고 NVMe는 discard를
  받지 않기 때문이다. `NodeDiskWriteRateHigh`는 워커의 6시간 평균 쓰기율이 30분 동안 20GB/일을 넘으면
  울린다. `scripts/ops/check-vm-disk.sh`는 VM별 호스트 vmdk 디렉터리와 게스트 `df`를 비교해 차이가 30G,
  vmdk가 120G 이상, 호스트 여유가 40G 미만이면 경고한다(`--strict`는 exit 1). `scripts/up.sh`는 경고만 하는
  모드로 실행한다. 회수 런북은 `docs/vagrant/operations.md` 12절(R309). 확인하지 못한 것: 실제 폭주에서
  경보가 울리는지.
- **영구 Keycloak master realm 관리자 (D-KC-ADMIN-573, #598).** 새 critical Phase 2 단계
  `11-1-keycloak-admin.sh`가 realm role `admin`만 가진 `platform-admin`(`temp-admin`과 같은 권한)을 만들고
  `iam/keycloak-admin`에 출력 없이 저장하며, `get realms/master`로 로그인을 확인한 뒤에만 `temp-admin`을
  삭제한다(두 이름이 같으면 거부). `11-2`, `11-3`, `13-2`, `test-sso.sh`, `verify-cluster.sh`,
  `show-credentials.sh`는 `keycloak-admin`을 먼저 읽고 `keycloak-initial-admin`으로 폴백한다. 포털과
  operator는 쓰지 않는다. 런북: `docs/runbooks/keycloak-permanent-admin.md`(R321). 확인하지 못한 것: `11-1`의
  실제 실행, Keycloak 파드 재시작 뒤 `temp-admin`이 다시 생기지 않는지.
- **Trivy DB 미러가 스스로 갱신되고 Harbor 저장 공간에 상한이 있음 (D-SECDB-AUTO, #603).** CronJob
  `trivy-db-refresh`가 6시간마다 돌며 `trivy-db:2`와 `trivy-java-db:1`만 digest를 고정한 skopeo 1.22.2로
  Harbor `trivy-db` 프로젝트에 복사하고, 이미 최신이면 건너뛰며, 승인자를 `auto-refresh`로 기록한다. 이
  프로젝트로 권한이 한정된 Harbor robot 계정을 쓴다(`08-5`가 생성). `08-5`는 저장소마다 최신 2개(untagged
  포함)만 남기는 보존 규칙과 GC 일정도 설정한다. egress가 없는 클러스터(Kakao)는 수동 fetch, promote, push
  게이트를 그대로 쓴다. 새 경보: `TrivyDbRefreshFailed`, `TrivyDbRefreshStale`(R322). 수동 경로도
  고쳤다(#550): `push-security-db.sh`가 이제 Harbor 관리자 자격증명으로 HTTPS 로그인하고, `08-5`는
  `trivy-db`에 저장소가 3개 미만이면 정확한 명령과 함께 경고한다(보안 대시보드와 컴플라이언스 인프라 화면이
  빈 상태였다). 확인하지 못한 것: 실제 CronJob 실행과 Harbor API 호출.
- **`trivy-server`가 오래된 미러 DB를 계속 다시 받으면 경보 (D-SECDB-STALE, #588).**
  `TrivyServerDbRedownloading`은 3시간 동안 파일시스템 쓰기량이 6e9바이트를 넘게 늘어날 때, 즉 다운로드가
  2회 이상일 때 울린다. 라이브 클러스터가 `trivy_*` 지표를 내지 않기 때문이다(R316). 갱신 절차는
  `scripts/airgap/README.md`에 있다.
- **Velero 지표와 백업 경보 (#511).** Prometheus에 `velero_*` 시계열이 없었다. Velero 차트의 ServiceMonitor를
  켜고, `velero-metrics` PeerAuthentication이 서버 파드의 8085 포트만 PERMISSIVE로 두며, NetworkPolicy는
  `monitoring`의 Prometheus 파드만 그 포트에 들인다. 실패, 부분 실패, 오래된 백업에 경보가 울린다.
- **CNPG 모니터링과 경보 (D-CNPG-MON, #602, #604).** `monitoring.enablePodMonitor`가 꺼져 있어 WAL 아카이빙이
  약 20시간 실패해도 아무도 몰랐다. `narwhal-db`가 이제 PodMonitor를 켜고 Prometheus는 모든 네임스페이스의
  PodMonitor를 선택한다. 새 경보: WAL 아카이브 적체(`cnpg_collector_pg_wal_archive_status{value="ready"}`가
  30분 동안 양수, critical), `CNPGMetricsUnavailable`(15분), `CNPGInstanceNotReady`,
  `IstioWaypointCertExpiringSoon`(인증서 잔여 24시간 미만, #601). 첫 WAL 경보는 라이브에 없는
  `cnpg_pg_stat_archiver_*` 시계열을 써서 울릴 수 없었고, #604가 라이브에서 확인한 지표로 바꿨다(R324,
  R326). `absent()` 때문에 항상 울리던 `CNPGClusterNotHealthy`도 고쳤다.
- **포털의 OSS 업데이트와 Trivy DB 상태 표시.** 설정에 OSS 업데이트 탭이 생겼다: 선언된 버전(`VERSIONS.md`의
  커밋된 스냅샷, `version-check.yml`이 검사), 실행 중인 버전, 업스트림 버전, 7일 cooling 규칙, 업그레이드
  이슈 본문을 만들어 주는 요청 흐름이 있으며 포털은 클러스터를 바꾸지 않는다(#552). 보안 대시보드에는
  cluster-admin 전용 Trivy DB 카드가 생겨 태그, 나이, 7일 SLO 기준 fresh/stale/missing을 보여 주고(#554),
  미러된 모든 소스를 나열하며 trivy-checks를 고정으로 표시한다(#570). Kyverno 정책 표는 Enforce와 Audit을
  설명하고, 설명·분류·심각도·규칙별 범위를 보여 주는 상세 패널을 연다(#547).
- **로컬 클러스터의 라이브 편집 포털 (#529).** dev Deployment를 차트에서 생성하고 field manager로 유지하므로
  Argo CD가 30초 안에 dev 이미지를 되돌리지 않는다. 낡은 수동 복사본 `skaffold dev` 매니페스트를 대체한다.
- **에어갭 흐름이 고정 버전을 라이브로 올리기 전에 digest 미러링을 요구 (#468, #515).** Kakao 노드는 업스트림
  egress가 없어서 bastion 미러에 이미지가 없는 채로 핀을 올리면 다음 재스케줄에서 `ImagePullBackOff`가
  났다. `narwhal-version-upgrade` 스킬, `scripts/airgap/README.md`("Mirror a pin bump"), 에이전트 규칙이 이제
  `skopeo copy --all --preserve-digests`와 노드 쪽 manifest HEAD를 요구하고,
  `scripts/test/check-mirror-pins.py`가 미러를 읽기 전용으로 점검한다.
- **`check-bundle.sh`가 정확한 차트 버전을 요구 (D-BUNDLE-PIN-VERSION, #528).** 예전 `falco`나 `headlamp`
  차트만 있는 번들이 통과해 로컬 `up.sh`가 약 40분 뒤 `14-gitops-bootstrap`에서 실패했다. 이제 `vagrant up`
  이전에 멈추고 빠진 `<chart>-<version>.tgz`를 하나씩 알려 준다.
- **Keycloak 로그인 테마를 포털과 통일, 관리 콘솔 브랜딩 (#572).** 로그인 테마는 이제 `keycloak.v2` 위의
  스킨으로 포털 토큰을 그대로 가져오고 Pretendard(OFL 라이선스)를 포함하며, 관리 콘솔에도 같은 브랜딩이
  적용된다. 테마 소스는 `narwhal-platform` 차트(`gitops/charts/narwhal-platform/keycloak-theme`)로 옮겨져
  ConfigMap 두 개와 체크섬 어노테이션으로 렌더링되므로 테마가 바뀌면 Keycloak이 롤링된다.
  `gitops/resources/keycloak-theme`은 제거했다. R306은 ConfigMap 키가 Keycloak CR이 마운트하는 키와
  일치하는지 검사한다.
- **`dev` 네임스페이스가 GitOps 관리 아래로 (D-DEV-OWNER, #534).** 새 Application `dev-namespace`가
  `dev-namespace.yaml`(메시 제외, `dev-quota`, `dev-limits`, RBAC)을 적용한다. 라이브의 `dev`에는 라벨, 쿼터,
  limits가 없었다. R301이 소유권과 sync-wave 순서를 검사한다.

### 변경

- **Falco가 모든 프로바이더에서 다시 활성화되고 0.39.2(차트 4.16.0)에서 0.45.1(차트 9.2.1)로 올라감
  (D-FALCO-045).** 0.39.2의 `modern_ebpf` 프로브가 커널 7.0에서 `scap_init`에 실패해 2026-07-08에
  비활성화되었다. 0.45.1은 업스트림 커널 7.0 수정(falcosecurity/libs#2997, falcosecurity/libs#3021,
  falcosecurity/libs#3086, falcosecurity/libs#3119)을 포함하며, 로컬 커널 7.0.14(linuxkit) 실행에서
  프로브를 열었고 0.39.2는 같은 환경에서 7월과 같은 증상으로 여전히 실패한다. 마이그레이션은
  breaking이다: 차트는 `falco.grpc`를 거부하고, 컨테이너 메타데이터는 `container` 플러그인에서 오며,
  falcoctl이 꺼져 룰(falco-rules 5.2.0)과 플러그인은 이미지에 내장된 것을 쓰고 런타임에 아무것도
  내려받지 않는다. falcosidekick은 서브차트 기본 2.32.0 대신 2.35.0을 쓴다(D-FALCO-SIDEKICK): 2.32.0은
  falcosecurity/falcosidekick#1366(취약한 Go 모듈, 2.34.0에서 수정)과 falcosecurity/falcosidekick#1390
  (다중 출력 레이스, 2.35.0에서 수정)을 고치기 전 버전이고, trivy CRITICAL/HIGH가 9/80에서 3/19로
  준다. Loki에는 JSON 라인(`format: json`)을 쓰고 포털이 이를 파싱하며, 포털의 priority 필터는
  falcosidekick이 쓰는 대문자 시작 라벨과 일치한다(기존 소문자 셀렉터는 아무것도 매칭하지 못했다).
  Alertmanager 출력은 `/api/v2/alerts`로 보낸다: 차트 기본값 `/api/v1/alerts`는 Alertmanager 0.27
  이상에서 410 Gone을 돌려줘(클러스터는 0.33.0 고정) Critical 알림이 모두 버려졌고, 실제
  Alertmanager v0.33.0에서 Critical 이벤트가 `GET /api/v2/alerts`에 `source=falco`로 보인다. 보안
  화면은 Ubuntu 26.04에서 런타임 이벤트 탭을 더 이상 숨기지 않고, `14-gitops-bootstrap.sh`도 그곳에서
  Falco를 제거하지 않는다. 클러스터가 아닌 로컬 Docker 랩(실제 Falco·falcosidekick·Alertmanager)에서
  확인했으며, 차트 타르볼·이미지 2개·GitOps push는 별도로 클러스터에 반영해야 하고, Ubuntu 26.04 /
  커널 7.0 Vagrant 박스와 Kubernetes CRI 경로에서의 Falco는 아직 관측되지 않았다.
- **포털이 `narwhal-portal:1.1.9`로 고정됨 (기존 1.1.0, 중간에 #467·#477·#481·#502·#520·#561·#580·#596이 1.1.1~1.1.8로
  고정, 1.1.9는 #608).** 1.1.9는 `lucide-react`를 1.66.0으로 올리고(#539) OSS 버전 목록에 `skopeo` 이미지를
  추가한다(#603). 사용자에게 보이는 기능 변경은 없다. 1.1.8 이하의 수정을 모두 포함한다. 이미지는 저장소 키로
  Cosign 서명되어 있다(index digest `sha256:72a1f4f4…`). 클러스터 bastion에서 이 고정을 적용하기 전에 digest로
  미러링해야 한다(`scripts/airgap/README.md`의 "Mirror a pin bump"). 로컬 Vagrant 클러스터는 ghcr.io에서 직접 pull한다.
- **워커 VM이 메모리 8GB, vCPU 4개, 베이스 box 0.3.1 (D-WORKER-MEMORY, #597, #531).** 워커 할당 가능 메모리가
  약 5.3GiB였고 2026-11-05~06에 노드가 커널 OOM killer에 반복해서 당했다. `WORKER_MEMORY`가 8192가 되었고
  64GiB 호스트에서 VM 합계는 36GB에서 42GB가 된다(R323). box `dasomel/ubuntu-26.04-xfs`는 0.1.0에서
  0.3.1(Ubuntu 26.04.1, 커널 7.0.0-42)로 올라가며, Vagrant Cloud가 2026-12-31에 box 호스팅을 끝내므로
  sha256이 있는 kube-ready-box GitHub Pages 카탈로그에서 받는다(D-BOX-CATALOG). 0.3.x는 arm64
  `vmware_desktop`만 제공한다. 업그레이드 참고를 볼 것.
- **Trivy 스캔이 노드 디스크에 쓰는 양이 크게 줄었음 (#574, #575, #584).** 매일 리포트가 만료되어
  (`scannerReportTTL` 24h) 약 114개를 다시 스캔했고 스캔 Job마다 약 1GB의 Trivy DB를 받아서 worker-3이 3일
  동안 517GiB를 썼다. 이제 `scannerReportTTL`은 168h, operator는 `builtInTrivyServer: true`(스캔 Job이 DB를
  받지 않고 서버에 질의)로 돌며, operator에 실제 리소스(requests 100m/384Mi, limit 1Gi)를 준다. 차트
  0.27.0은 예전 `operator.resources` 키를 무시했고 operator는 22시간에 33번 OOMKilled되었다.
  `operator.scanJobsRetryDelay`는 6h라서 docker.io 익명 쿼터에 걸린 스캔이 30초마다 재시도하지 않고,
  Velero 임시 워크로드(kopia maintain Job, 데이터 경로 파드)는 라벨로 스캔에서 제외되며, 서버 캐시는
  Velero 백업에서 제외된 5Gi `nfs-csi` PVC다. 라이브에서 변경 66분 뒤 스캔 Job의 쓰기는 0이었고
  `VulnerabilityReport`는 94개에서 114개로 늘었다. 24시간 측정은 하지 않았다. 이미 vmdk에 쓰인 공간은 제자리에서
  회수할 수 없다.
- **백업 경로가 노드 메모리 압박에서 살아남음 (D-sw-qos, D-datapath-qos, #583).** 2026-11-06 worker-3이 Velero
  백업 중 전역 OOM에 빠져 BestEffort `weed` filer가 죽었고 S3가 500을 돌려주어 PodVolumeBackup이 실패했다.
  SeaweedFS master, volume, filer, s3에 cpu·메모리 requests와 메모리 limits를 주고,
  `velero-node-agent-config`는 `loadConcurrency.globalConfig: 1`과 `podResources`를 설정한다. 스케줄, 볼륨
  정책, 백업 대상 볼륨은 그대로다(R310). 실제 백업 실행에 배포해 보지는 않았다.
- **Prometheus, Tempo, Loki를 워커에 분산하고 Loki를 Burstable로 (D-MON-QOS-LOKI, D-MON-SPREAD, #586).**
  셋이 모두 worker-3에 있는 동안 Prometheus가 약 3시간에 16번 OOMKilled되었고 Loki에는 requests와 limits가
  없었다. Loki는 requests 100m/256Mi, limit 768Mi(규칙 사이드카 10m/64Mi, limit 192Mi)를 받고, 셋 모두
  preferred(weight 100, required 아님) 호스트명 파드 anti-affinity를 받는다. 보존, 저장 크기, 스크레이프
  설정은 그대로다(R311). 업그레이드 참고를 볼 것. 확인하지 못한 것: 재스케줄 뒤 실제 배치.
- **메시 워크로드 인증서 수명이 168시간 (D-MESH-CERT-TTL, #601).** 호스트가 슬립하면 VM과 갱신 타이머가
  멈추고, 2026-11-07에 ztunnel 6개와 waypoint 3개가 만료된 인증서로 돌아 CNPG가 Ready가 아니게 되었으며
  WAL 아카이빙이 약 20시간 실패했다. TTL은 클라이언트가 정하므로 ztunnel의 `SECRET_TTL`을 168h로,
  waypoint에는 `meshConfig.defaultConfig.proxyMetadata`로 같은 값을, istiod의 `MAX_WORKLOAD_CERT_TTL`을
  720h로 두었고 GitOps와 `09-istio-ambient.sh`가 일치한다. 대가는 탈취된 워크로드 키의 유효 기간이 1일에서
  7일이 되는 것이다. `up.sh`는 호스트 슬립이 켜져 있으면 경고한다. 문서: `docs/vagrant/operations.md` 14절,
  `docs/common/troubleshooting.md` 10.2. ztunnel 인증서 만료는 경보가 없다(지표가 없고 Loki ruler에
  `alertmanager_url`이 없다).
- **Narwhal의 CNPG 클러스터가 anti-affinity를 필수로 요구하고 probe 지연을 견딤 (D-CNPG-AA-REQUIRED,
  D-CNPG-RESTART-AMP, D-CNPG-RESOURCES, D-CNPG-APPDB, #590, #605).** `narwhal-db`의 두 인스턴스가 한때 모두
  worker-1에 있어 워커 drain이 WAL 아카이빙을 막았다. `podAntiAffinityType`이 이제 `kubernetes.io/hostname`
  기준 `required`다. 대가: 워커 하나를 drain하면 쫓겨난 인스턴스가 갈 곳은 세 번째 워커뿐이고, 그 워커에
  requests 여유가 없거나 워커 두 대가 내려가 있으면 Pending으로 남는다(`docs/common/database.md`). liveness는
  약 120초를 기다리고 isolation 점검은 5초이며 메모리 requests와 limits를 명시했고(CPU limit 없음),
  `bootstrap.initdb`는 라이브 DB와 같은 `narwhal`/`narwhal`이다. 저장소에는 존재하지 않는 `authentik`이
  남아 있어 CNPG 기본 모니터링 쿼리가 `database "authentik" does not exist`로 모두 실패했다. `narwhal-db.yaml`과
  `07-cnpg.sh`를 함께 바꾼다(R313). 라이브 클러스터의 기본 쿼리 오류가 남아 있는지는 다시 확인하지 않았다.
- **`kyverno-policies`가 Kyverno가 채우는 필드만 무시 (D-KYV-IGNORE-NARROW, #589).** 예전
  `ignoreDifferences`는 Kyverno가 autogen 규칙을 spec에 주입한다고 가정해 규칙 본문,
  `validationFailureAction`, annotations, labels를 숨겼다. Kyverno 1.18은 이를 `.status.autogen`에 쓰고 Argo
  CD는 status를 비교하지 않는다. 라이브 정책 10개는 git과 Argo tracking annotation만 달랐다. 이제
  `admission`, `emitWarning`, `skipBackgroundRequests`, `allowExistingViolations`, verifyImages의 `useCache`와
  `signatureAlgorithm`만 무시하고, `add-default-securitycontext`는 `Audit`을 명시한다(라이브도 이미 Audit).
  `headlamp-policy`와 `narwhal-platform` 템플릿에는 같은 광범위한 목록이 남아 있다. ClusterPolicy 8개에 표준
  title, description, category, severity annotation을 추가했다(#569).
- **Harbor가 스캔 폭주를 견딤 (#567).** Trivy 스캔이 도는 동안 Harbor 구성 요소가 재시작되거나 굶지 않도록
  probe와 리소스 requests를 추가했다.
- **포털의 인증서 갱신 RBAC이 네임스페이스로 한정됨 (D-CERT-RENEW-NS, #592).** `certificates/status`의
  `update`를 `narwhal-portal` ClusterRole에서 빼고 Certificate가 있는 `devtools`, `platform-system`, `storage`
  세 네임스페이스의 Role과 RoleBinding으로 옮겼다(R318). 다른 네임스페이스의 Certificate는 갱신할 때 403이
  나며 포털은 이를 권한 없음으로 보여 준다. 그 네임스페이스에 Role을 추가하면 된다.
- **Gitea 브라우저 로그인이 Gitea 자체 Keycloak OAuth2 소스를 사용 (D-GITEA-OAUTH, #496, #498).** 이제 로그인할
  때마다 그룹이 매핑되고(`cluster-admin`은 사이트 관리자, `developer`는 `narwhal/Developers`, `viewer`는
  `narwhal/Viewers`, `guest`는 제한), 제거된 멤버십은 빠지며, 리버스 프록시 인증은 꺼졌다.
  `REVERSE_PROXY_TRUSTED_PROXIES`는 클라이언트 IP 신뢰용으로만 유지한다(D-GITEA-CLIENT-IP). 예전
  `gitea admin auth delete --name`은 그런 플래그가 없어 실행되지 않았고 재실행이 조용히 실패했다. 이제 헬퍼가
  소스를 제자리에서 갱신한다.
- **Headlamp를 v0.42.0에서 v0.46.0으로 올리고 내장 OIDC 자동 로그인 사용 (#497).** #478의 게이트웨이 스크립트
  주입을 대체한다.
- **게이트웨이 관리자 UI와 Velero UI가 `cluster-admin` 그룹을 따름.** Hubble, Prometheus, Alertmanager, NFS
  quota agent는 `cluster-admin` 밖의 모든 Keycloak 사용자에게 403을 돌려준다(예전에는 viewer를 포함한 모든
  realm 사용자가 Alertmanager silence를 만들 수 있었다, #535). Velero UI 로그인 자체는 `velero-ui/login`
  client role과 전용 `velero-ui-browser` 인증 흐름으로 `cluster-admin`에 한정된다(D-VELERO-UI-LOGIN-563,
  #599). 복사한 browser 흐름 안의 게이트는 SSO 쿠키 세션이 건너뛰므로 이 흐름은 해당 클라이언트에만
  연결한다. OpenBao와 Velero UI 루트에 직접 들어가면 쿼리 문자열이 없을 때 `/sso`로 리다이렉트한다(#472).
  관리자 최상위 권한: `cluster-admin`은 Grafana `GrafanaAdmin`과 Keycloak `realm-admin`을 받고, 권한
  매트릭스는 developer가 네임스페이스나 노드를 조회할 수 없다고 정정했다(#548).
- **Kakao 외부 ingress가 master 로드밸런서를 경유 (D-INGRESS, #473).** 클러스터는 그대로인데 worker LB 공인
  IP가 인터넷에서 응답하지 않게 되었다. master LB는 같은 APISIX NodePort로 80/443을 전달하고 14개 도메인이
  모두 응답했다. master ingress 타깃 그룹에는 health monitor가 없어 master가 죽으면 간헐적 실패가 날 수
  있다. `INGRESS_LB=worker`로 예전 경로로 돌아간다. `setup-hosts-kakao.sh --apply`는 먼저 443 포트를
  점검한다(`SKIP_PROBE=1`로 우회).
- **kube-apiserver OIDC가 모든 컨트롤 플레인 노드에 적용되고 kubeadm 업그레이드에도 유지됨 (#500, #521, #523,
  #495).** master-1에만 `--oidc-*` 플래그 7개가 있어서 Service가 master-2나 master-3으로 가면 Dashboard,
  Headlamp, kubectl 토큰이 401로 실패했다. `11-4b`가 노드마다 플래그를 쓰고(원자적 이동, 제한된 `readyz`
  대기), `11-4c`가 `kubeadm-config`의 `ClusterConfiguration`에 병합해 `kubeadm upgrade`가 유지하며,
  `distribute-join-kakao.sh`는 컨트롤 플레인이 합류하기 전에 CA 해시를 비교하고,
  `11-3-keycloak-clients.sh`는 critical Phase 2 단계다.
- **1.1.1 이후 포털 변경 (#475, #479, #501, #514, #546, #547, #549, #556, #559, #566, #581).** 아키텍처와 클러스터
  페이지가 projected SA 토큰으로 인증하고, 권한 표가 역할 기본 도구를 보여 주며, 보안 페이지가
  `VulnerabilityReport`를 페이지 단위로 가져오고(한 응답이 10.7MB로 8MiB 한도를 넘었다) 429에서 재시도하며
  부분 결과를 표시하고, 매트릭스의 sticky 열이 불투명해지고, DORA 리드타임이 분 단위 정밀도를 유지하며
  Argo CD가 최신 작업에만 phase를 기록하므로 오래된 이력 행은 대시로 표시되고, 리드타임은 새
  `gitea-portal-api` 게이트웨이 규칙(토큰 인증된 GET과 POST, 세 경로만, R305)으로 Gitea를 읽고 Helm 소스 앱은
  N/A로 표시하며, 비용 서비스 분석이 모든 파드를 귀속시킨다(kube-state-metrics가 이제 파드 라벨을 내보낸다).
  이 기간에 1.1.6~1.1.9가 릴리스되었고 1.1.9는 lucide-react 업데이트, OSS 목록의 `skopeo` 항목, 8GB 문구를
  담고 있다(#607).
- **`push-to-gitea.sh <dir>`이 디렉터리를 정확히 미러링하고 삭제를 전파 (D-GITOPS-PUSH-DIRS, #578).**
  2026-11-05에 이 스크립트가 이미 있는 대상을 중첩시켜 Keycloak이 12시간 동안 테마를 마운트하지 못했다.
  이제 clone 안의 대상을 지운 뒤 복사하며 `..`와 절대 경로는 거부한다. 경로 없는 mirror-all 모드는 Gitea
  저장소에만 있는 리소스가 있으므로 의도적으로 추가 전용으로 남겼다(R308). 라이브 Gitea에서는 실행해 보지
  않았다.
- **운영 문서를 1.3.0 동작에 맞춤 (#451, #452, #541).** 재해 복구 절차가 더 이상 "데이터 손실 없이"를
  약속하지 않고, `vagrant destroy` 전에 클러스터 밖에 받아 둘 사본과 복원되지 않는 것(Velero에서 제외된 5개
  볼륨, 클러스터 내부 SeaweedFS는 외부 백업이 아님)을 데이터별로 적는다. 릴리스 안내와 색인,
  `operations.md`의 Velero 명령, OS와 containerd 서술을 바로잡았다. Falco arm64 TOCTOU 경고(arm64에는
  `creat`와 `open`이 없어 `libpman`이 붙이지 못함)는 `openat`, `openat2`, `connect` 프로그램이 붙으므로
  무해하다고 문서화했다.
- **포털 브라우저 E2E 잡이 15분 뒤 종료되고 `harden-runner`가 v2.22.3으로 고정 (D-job-timeout, #465, #508).**
  차단된 egress 때문에 checkout이 두 번 약 25분 멈췄다. egress 정책과 허용 목록은 그대로이고 타임아웃은
  여전히 게이트를 실패시킨다.

### 수정

- **Falco가 플랫폼 자체 클라이언트의 API 서버 연결로 Loki를 뒤덮지 않음 (D-FALCO-NOISE).** 0.45.1 롤아웃
  20시간 뒤 라이브 Loki에 이벤트 47,180건이 쌓였고 그중 47,178건이 번들 NOTICE 룰 "Contact K8S API Server
  From Container"였으며, 모두 API 호출이 본업인 컴포넌트에서 나왔다. 이 룰에 `customRules`
  (`/etc/falco/rules.d`에 마운트)로 정확한 (네임스페이스, 이미지) 쌍 8개(velero-ui, narwhal-portal,
  keycloak-operator, kyverno, headlamp, Kubernetes dashboard auth·web, cloudnative-pg)에 대한 예외를 하나
  추가했다. 그 밖의 파드는 그대로 탐지하고, priority 바닥은 바꾸지 않았으며, kube-apiserver 감사 로그에는
  이 클라이언트들의 쓰기와 secrets·configmaps·serviceaccounts·RBAC 객체 읽기가 계속 기록된다(그 밖의 읽기는
  감사되지 않는다). 실제 falco:0.45.1 이미지의 `falco -V`와 로컬 랩(docker.io가 아닌 5쌍은 조용하고 다른
  네임스페이스·이미지는 여전히 탐지, docker.io 3쌍은 랩의 Docker 엔진이 CRI와 달리 `docker.io/` 접두사를
  떼어 문법만 확인)에서 검증했고 R291 A11이 예외를 고정한다. 정적 변경만 포함하며
  DaemonSet은 다음 동기화에서 롤링되고, Kakao 클러스터에서의 동작은 아직 관측되지 않았다.
- **istio-cni가 ambient 등록을 재시도하고 verify 스크립트가 ztunnel에 거부된 kubelet probe를 표시
  (D-576-DETECTION-RETRY, D-576-PROBE-DENY, #576, #577).** 노드 재시작 뒤 ztunnel이
  `storage/seaweedfs-volume-0`과 `storage/openbao-agent-injector`의 kubelet probe를 거부했다(정책
  `istio_converted_static_strict`). istio-cni 에이전트가 노드 부팅 중 시작한 파드를 놓칠 수 있기 때문이다.
  volume 파드의 headless DNS 레코드가 빠져 kopia가 `unexpected EOF`로 실패했고 Velero 백업이 위험해졌다.
  Application과 `09-istio-ambient.sh`에 `enableAmbientDetectionRetry: true`를 설정했고,
  `verify-cluster.sh`가 거부를 보고하며, `troubleshooting.md`에 복구 단계가 있다(R307). 실제 노드 재부팅에서는
  실행해 보지 않았다.
- **플랫폼 `*Down` 경보가 울릴 수 있게 됨 (D-DOWN-WORKLOAD, #525, #526).** KeycloakDown, ArgoCDDown,
  HarborDown, GiteaDown, TraefikDown은 존재하지 않는 `kube_pod_labels` 시계열(kube-state-metrics가 파드
  라벨을 내보내지 않았다)과 조인해서 어느 것도 울릴 수 없었고, TraefikDown은 APISIX로 대체된 컴포넌트를
  보고 있었다. 이제 워크로드 가용성을 확인한다.
- **`loki-canary` DaemonSet 비활성화 (D-LOKI-CANARY-DISABLE, #587, #591).** `lokiCanary`는 차트의 최상위
  키인데 중첩된 `monitoring.lokiCanary.enabled: false`가 무시되어 모든 노드에서 canary가 돌았다(R315).
- **OpenBao OIDC가 저장되지 않으면 Phase 2가 멈춤 (#544).** 최초 프로비저닝에서 `auth/oidc/config`가 issuer
  불일치(`...:9443/realms/narwhal`)로 거부되었고 `&& echo ok || echo WARN`이 실패를 삼켜 OpenBao SSO가
  `could not load configuration`으로 실패했다. 이제 블록이 `set -e`로 돌며 10초 간격 6회 재시도 뒤 exit
  1하고, `test-sso.sh`는 OpenBao에 `auth_url`을 요청해 Keycloak realm URL일 때만 통과한다. `:9443` issuer의
  원인인 bootstrap Keycloak CR(스킴 없는 hostname, `strict: false`)은 고치지 않았다.
- **Kakao의 매끄러운 SSO와 SSO 점검 (#478, #494, #548, #533, #593).** bastion dnsmasq가 실패하면 다시
  시작하고 네트워크를 기다린다(노드가 apiserver용 Keycloak issuer를 해석하지 못했다). 비어 있는 Keycloak
  클라이언트 시크릿 읽기는 이제 빈 Secret을 쓰지 않고 스크립트를 실패시킨다(`grafana-oidc-secret`과
  `gitea-oidc-secret`이 비었던 원인). Argo CD `policy.default`를 비워 guest가 거부되고(예전에는 애플리케이션
  46개를 볼 수 있었다), Harbor가 OIDC 인증서를 검증하며, Harbor `developer`와 `viewer` 그룹은 OIDC가 켜진 뒤
  매핑된다(예전에는 `db_auth`에서 실행되어 조용히 거부되었고 그 사용자는 프로젝트 접근이 없었다).
  `test-sso.sh`는 HTTP 200 안에 담긴 Dashboard API 403을 거부로 판정하고, `08-6-tls-routes.sh`는 CA가
  실제로 바뀐 때만 `harbor-core`를 재시작한다. 새 `docs/common/sso-accounts.md`가 시드 계정과 문제 해결을
  설명한다.
- **포털 인증서 갱신이 동작 (D-CERT-RENEW-ISSUING, #549).** 설정, 인증서, 갱신이 항상 실패했다. 포털이
  cert-manager 웹훅이 5분 미만을 거부하는 `spec.renewBefore: "1s"`를 패치했고 인증서를 `get`만 할 수 있었다.
  이제 `cmctl renew`처럼 `PUT /status`로 reason `ManuallyTriggered`의 `Issuing=True`를 설정하고,
  ClusterRole에는 `certificates/status`의 `update`만 추가했다(#592가 네임스페이스 세 곳으로 좁힘).
- **Velero 스케줄이 `emptyDir` 볼륨을 건너뜀 (#422, #455).** kakao-narwhal에서 PodVolumeBackup 115개 중
  107개가 `emptyDir` 볼륨이었고 백업 창에서 약 9,770초를 썼다. 이제 `velero-volume-policy` ConfigMap이
  이를 건너뛴다.
- **Kakao에서 Cost 페이지가 가격을 계산 (#493).** 포털 1.1.1 이상은 `COST_*` 키 8개가 없으면 503을 돌려주는데
  `13-2-narwhal-portal-bindings.sh`는 3개만 설정했다. 이제 프로바이더별로 8개를 모두 설정한다.
  `node-collector`는 에어갭 인벤토리에서 추적한다.
- **라이브 테스트 스위트가 거짓 실패를 보고하지 않음 (#510, #518, #524, #530, #536).** oauth2-proxy와 Traefik
  ForwardAuth의 죽은 점검을 제거하고, 와일드카드 TLS 점검이 올바르게 확장되며, ambient 네임스페이스
  목록이 `09`가 등록하는 것과 일치하고(`dev`는 의도적으로 ambient가 아님), 파싱할 수 없는 Keycloak 로그인
  폼은 통과가 아니라 실패한다. `test-sso.sh --section=acl`에 게이트웨이 관리자 UI 4개에 대한 라이브 점검
  16개를 추가했다.

### 보안

- **Velero UI가 공개된 키로 세션에 서명하거나 모두에게 manage-all을 주지 않음 (#555, #594, #599).** 앱이
  `AUTH_SECRET_PASSPHRASE`가 설정되지 않아 업스트림의 하드코딩된 JWT passphrase로 폴백했으므로 누구나 토큰을
  만들 수 있었다. 이제 `11-3`이 `openssl rand -base64 32`로 키를 만들어 `velero-ui-oauth`에 넣는다(예전 키로
  위조한 토큰은 401). `POLICY_FILE_PATH`는 있는데 정책 파일이 없어서 guest를 포함한 로그인 사용자 누구나
  백업을 만들고 삭제할 수 있었다. 정책은 이제 `g,cluster-admin,manage,all`이고 파일이 없으면 파드가
  시작하지 않는다. 게이트웨이는 `POST /api/backups/download`(업스트림에 정책 점검이 없고 guest가 실제 다운로드
  URL을 받았다. 일괄 다운로드는 사라짐)와 `DELETE /api/settings/velero/plugins/:name`(UI가 호출하지 않는
  업스트림 stub)에 403을 돌려준다. 앱이 자체 OAuth를 쓰므로 실제 경계는 `cluster-admin` 전용 로그인
  흐름(#599)이다. `/api/stats/*`, `/api/form/*`, `/api/settings/velero/*`, `/api/config`는 로그인할 수 있는
  사람이면 누구나 호출할 수 있고, 업스트림 CORS는 여전히 모든 origin을 반사하며,
  `docs/common/app-sso-permission-matrix.md`에 엔드포인트 노출을 기록했다.
- **게이트웨이 관리자 UI가 `cluster-admin` 밖의 사용자를 거부 (#535, #536, #545).** 공용 `gatewayAdminOnly`
  헬퍼가 `openid-connect` 앞에서 클라이언트가 보낸 `X-Userinfo`를 지우고 뒤에서 그룹을 확인한다. R302는 이제
  모든 차트, raw 매니페스트, plugin config, global rule을 렌더링해 다른 차트에서 제한이 없는 라우트를
  잡는다.
- **포털 의존성을 공개된 권고 이후 버전으로 올림.** Keycloak 로그인 경로의 Auth.js(`next-auth`
  5.0.0-beta.32, `@auth/core` 0.41.3)에서 설정 오류 시 인증 점검이 fail open되는 것을 포함한 critical
  권고 두 건(#461), `zod` 4.6.7과 `undici` 7.30.1(#471), `fast-uri` 4.2.3(#492)을 반영했고 쓰이지 않는 `hono`
  override를 제거했다(#543).
- **Keycloak이 임시 bootstrap 관리자를 더 이상 유지하지 않고 (#598), Harbor가 OIDC 인증서를 검증한다
  (#548).** 추가와 수정 항목을 볼 것.

### 알려진 이슈

- **이미 커진 호스트 디스크는 제자리에서 회수할 수 없음 (#574, #582).** 한 번 쓰인 vmdk는 할당된 채로 남고
  linked clone은 줄일 수 없다. 워커 VM을 다시 만들어야 공간이 돌아온다(`docs/vagrant/operations.md` 12절).
  worker-2처럼 Trivy 스캔 쓰기로 계속 커질 수 있다(#574·#575 이후, #584 이전에 컨테이너 쓰기 24시간 21GB 중
  18GB가 측정되었다).
- **`docker.io/*` 이미지의 Trivy 스캔 Job이 UNAUTHORIZED로 실패**한다(익명 pull 쿼터; velero, busybox,
  dashboard 이미지, #575). 스캔 Job마다 Java DB도 여전히 쓴다.
- **라이브 클러스터에서 확인하지 못한 것:** Trivy 갱신 CronJob과 Harbor 보존(#603), Keycloak `11-1` 이전(#598),
  새 `vagrant up`에서의 NFS 디스크 연결(#606), 노드 재부팅에서의 ambient 감지 재시도(#577), Harbor CA
  재시작(#593), Dashboard의 200 안 forbidden 점검(#593), 재스케줄 뒤 모니터링 배치(#586), 메모리 압박에서의
  백업 경로(#583).
- **ztunnel 인증서 만료에는 경보가 없고** (#601), 168h TTL은 탈취된 워크로드 키의 수명을 1일에서 7일로
  늘린다.
- **워커를 drain하면 CNPG 인스턴스가 Pending으로 남을 수 있다.** 남은 여유 워커에 자리가 없거나 워커 두 대가
  내려가 있는 경우다(#590).
- **Headlamp와 Kubernetes Dashboard는 `platform-admin` 바인딩을 유지한다.** 내장 `cluster-admin`으로 넓히는
  것은 소유자 결정을 기다린다(#548).

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
  객체는 업그레이드 참고 확인. 한 클러스터에서 관찰함: 2026-10-21 02:00:00 UTC에 수정된 스케줄이 `Backup`
  (`narwhal-db-backup-20261021020000`, completed) 정확히 1건을 만들었고 매시간 실행은 멈춤(`Backup` 객체 1311개에서
  1312개); 기존 `Backup` 객체는 삭제하지 않았음.
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
- **S3 인증 운영 작업: 남은 공백 (#412 후속, #414와 #415는 종료됨).** 2026-10-19 kakao-narwhal에서 검증됨: 롤 표식이 자가 치유
  후에도 유지되고, Application이 Synced이며, 익명 검사가 통과하고, S3 서버를 롤링한 뒤에도 네 클라이언트 모두
  클라이언트별 I/O 검사를 통과함. `08-4-storage.sh` 가드는 인증된 그 클러스터에 읽기 전용으로 실행해 통과를
  확인했고, 익명 클러스터에서의 중단 경로는 가짜 `kubectl` 테스트와 독립 리뷰의 시뮬레이션으로만 확인했으며
  `08-4`를 라이브에서 다시 실행하지는 않았음. 이후 #442가 클라이언트 Secret을 서버 사이드 어플라이로 적용하고
  `kubectl.kubernetes.io/last-applied-configuration` 어노테이션에서 키를 제거함(라이브 클러스터에서 다시 검증하지
  않음). 남은 공백: 가드가 확인하는 `envFrom` 참조는 Loki/Tempo 파드가 재시작되어 새 키를 로드했음을 증명하지 않음.
  같은 Application은 `Secret seaweedfs-db-secret`을 비교 상태 없이 표시하며, 이는 조사하지 않았음.
- **`root_squash`에서는 소유자 전용 파일이 있는 볼륨의 Velero 볼륨 백업이 실패함 (#431).** NFS 서버에서 다른
  사용자가 읽을 수 없는 파일이 `database/`(7686개 중 7677개), `monitoring/`(최초 측정 1317개 중 138개, 이후 1296개 중 115개: 데이터가 계속 변함), `devtools/`(292개
  중 72개), `storage/`(77개 중 7개)에서 발견됨. 소유자 전용 파일이 있는 볼륨의 Velero 백업에는 의존하지 말 것:
  OpenBao는 대신 CronJob의 애플리케이션 수준 암호화 스냅샷을 쓰고(#443, 아직 어느 클러스터에도 활성화되지 않음, Velero
  백업 훅 아님), PostgreSQL은 CNPG barman에 의존하며, 나머지 볼륨은 Velero로 보호되지 않음. CNPG 자체
  barman 백업은 S3로 별도 수행되며, 2026-10-18에 온디맨드 백업이 완료되고 WAL 아카이빙이 진행됨을 확인함.
  봉쇄 조치(1단계): 스케줄 실행에서 `PodVolumeBackup`이 Canceled된 다섯 볼륨(OpenBao `data`, CNPG `pgdata`, Gitea
  `data`, Harbor jobservice `job-logs`, SeaweedFS 마스터 `data-storage`)에 파드 어노테이션 `backup.velero.io/backup-volumes-excludes`를 부여함
  (OpenBao·Harbor·SeaweedFS(`master.podAnnotations`) 차트, CNPG `Cluster`의 `inheritedMetadata`, Gitea 차트로 설정). 스케줄 백업이 `InProgress`로
  멈춰 뒤 스케줄을 막는 대신 끝나도록 하기 위한 것임. 멈춤만 막을 뿐이며 **Velero는 여전히 이 볼륨들을 보호하지
  않음.** OpenBao는 새 애플리케이션 수준 스냅샷(#443, 아래, 아직 어느 클러스터에도 활성화되지 않음)에 의존하고, PostgreSQL은 CNPG barman에 의존하며, Gitea `data`,
  Harbor `job-logs`, SeaweedFS 마스터 `data-storage`(raft 메타데이터)는 보호되지 않으며, 마스터의
  `seaweedfs-master-log-volume`은 계속 백업됨. 이 어노테이션을 적용하면 OpenBao, CNPG, Gitea, Harbor jobservice,
  SeaweedFS 마스터 파드가 롤링됨. 한 클러스터(kakao-narwhal)에서 관찰함: 1단계(#434, #436)는
  2026-10-19에 적용되었고, 2026-10-21(UTC) 읽기 전용 확인에서 2026-10-20과 2026-10-21의 예약 Velero 백업(databases,
  full, gitea, harbor, openbao)이 모두 Completed였으며, Canceled된 `PodVolumeBackup` 5건은 모두 2026-10-19 실행분이고
  이후 실행분에는 없음(Completed 총 345건). 관찰일 뿐 보장이 아니며, 이 볼륨들의 전체 복원은 여전히 검증되지 않음. 근본 해결을 위해 #431은 계속 열려 있음. **OpenBao 2단계 (#443):** Velero fs-backup이 읽을 수 있는 PVC에
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

[Unreleased]: https://github.com/dasomel/narwhal/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/dasomel/narwhal/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/dasomel/narwhal/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/dasomel/narwhal/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/dasomel/narwhal/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dasomel/narwhal/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/dasomel/narwhal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
