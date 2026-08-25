# Failure Injection / Chaos 시나리오 카탈로그 (T5)

> narwhal#50의 AC "failure injection scenario catalog 정의"에 대한 답.
> [`test-strategy.md`](test-strategy.md)의 T5 절에서 참조한다.
>
> **중요한 정정**: narwhal#50의 2026-08-23 트리아지 댓글은 "failure-injection/chaos
> 카탈로그 없음"이라고 적었지만, 이는 부정확하다. `tests/chaos/`에 Chaos Mesh 기반
> 실험 6종(+smoke 1종)이 **이미 구현되어 있고, 2026-08-10 클러스터 destroy 이전인
> 2026-07-17에 전부 PASS로 실행된 기록이 `tests/chaos/RUNBOOK.md`에 있다.** 이
> 문서는 그 기존 자산을 먼저 정확히 반영한 뒤, 이슈가 요구하는 나머지 하위 시나리오
> 중 실제로 비어 있는 부분만 "설계됨, 미실행"으로 신규 추가한다.
>
> **이 패스에서 새로 실행한 chaos 실험은 없다** — 클러스터가 destroy된 상태라
> 실행 자체가 불가능하다. 아래 표의 "PASS"는 전부 2026-07-17 실행분이며, 이후
> 재검증되지 않았다.

## 1. 기존 구현 — Chaos Mesh 실험 (`tests/chaos/`)

실행 방법: `./tests/chaos/run-chaos.sh <experiment>` (experiment 정의는
`tests/chaos/experiments/*.yaml`). 판정은 `run-chaos.sh`가 라벨 셀렉터 기반으로
자동 수행한다(openbao-kill이 처음엔 auto-unseal Job 파드를 오집계해 FAIL로
잘못 판정한 적이 있어 — 셀렉터 기반으로 수정 후 재실행 PASS, RUNBOOK 63행).

| 실험 | 타겟 | 가설 | 마지막 실행 | 결과 | 이슈 T5 하위 항목 매핑 |
|------|------|------|--------------|------|--------------------------|
| `headlamp-kill` | devtools/headlamp (단일 replica) | 컨트롤러가 킬·복구 가능한지 확인하는 smoke 전용 | 미기록(스모크는 결과표 없음) | — | pod failure(전제조건 확인용) |
| `istiod-kill` | istio-system/istiod | control-plane 파드 킬에도 data-plane(ztunnel) 통신 유지 | 2026-07-17 | PASS(2 replica라 SPOF 미발생) | component failure |
| `openbao-kill` | storage/openbao-0 | 킬 후 auto-unseal Job이 자동 복구 | 2026-07-17 | PASS(재실 ~66초) | component failure |
| `coredns-latency` | kube-system/coredns(mode:one) | 500ms 지연 주입해도 나머지 replica가 흡수 | 2026-07-17 | PASS | network/DNS outage(부분 — 지연만, 완전 단절은 아님) |
| `keycloak-kill` | iam/keycloak-0 | 기존 JWT 세션은 유지, 신규 로그인만 일시 지연 | 2026-07-17 | PASS | component failure |
| `worker-cpu-stress` | chaos-testing/chaos-stress-target(단일 워커) | 스트레스 타겟만 영향받고 시스템 파드는 probe-kill 안 됨 | 2026-07-17 | PASS(2026-07-14 사건이 단일 노드가 아니라 호스트 전체 과다구독이었음을 역으로 입증) | node resource exhaustion(부분 — 실제 노드 다운은 아님) |
| `cnpg-primary-kill` | database/narwhal-db(primary) | CNPG가 8초 내 자동 failover | 2026-07-17 | PASS | storage/database failure |

Keycloak T2 파일럿(`scripts/test/t2-component.sh keycloak --mode render`)은 이 실험을
실행하지 않는다. 대신 실제 rendered Keycloak CR의 `namespace: iam` 및 이 파일의
`keycloak-kill` selector `namespaces: [iam]`, `app: keycloak`을 함께 확인해, T2 desired-state
대상과 T5 catalog 대상이 drift하지 않게 한다. Chaos Mesh 실행/복구 검증은 여전히 라이브
클러스터가 있어야 한다. 이 연결은 machine-consumed contract다: adapter는 위 Markdown 표에서
정확히 `` `keycloak-kill` | iam/keycloak-0 `` 행 하나를 파싱해 namespace와 component identity를
도출하고, malformed/duplicate/absent row는 실패한다. 표의 첫 두 data column 형식을 바꾸면
adapter도 함께 바꿔야 한다.

## 2. 이슈 "핵심 검증 시나리오" 5개와의 대응

이슈 본문의 `핵심 검증 시나리오`는 실행 가능한 테스트 스크립트가 아니라 **설계
입력(illustrative target scenarios)**이라고 작업 지시에 명시돼 있다. 그대로
실행하려 하지 않고, 각각이 현재 어떤 자산에 대응되는지만 정리한다.

| # | 시나리오 | 대응 자산 | 상태 |
|---|----------|-----------|------|
| 1 | 인터넷 차단 → bundle 반입 → 신규 설치 → validation | `docs/common/airgap-isolation-testing.md` + `verify-isolation.sh` + `scripts/up.sh` | 절차 문서화됨, end-to-end 자동 실행 스크립트는 없음(수동 조합) |
| 2 | vulnerability DB 반입 → image scan → 결과 재현 | 없음 | **갭** — trivy-db가 `ghcr.io`에서 런타임 pull(`test-strategy.md` §3.4) |
| 3 | workload 유지 → component upgrade → smoke/regression → rollback | 없음 | **갭** — T6, 아래 §3 신규 시나리오 |
| 4 | 장애 주입 → messenger 통지 → LLM 1차 RCA → 조치 → 승인된 Runbook → 복구 검증 | chaos 실험(장애 주입 절반)만 존재 | **갭** — 통지·LLM RCA·승인 게이트 전부 없음(T5 notification + T7) |
| 5 | multi-cluster staged upgrade 중 1개 실패 → rollout 중단·복구 | 없음(단일 클러스터 프로젝트) | **갭** — narwhal은 현재 단일 클러스터 구조, multi-cluster 자체가 아키텍처 확장 필요 |

## 3. 신규 시나리오 카탈로그 — 설계됨, 미구현

아래는 이슈의 T5 정의(node/pod/control-plane/component failure, network/DNS/LB/
registry outage, storage failure, backup/restore failure, upgrade 실패/rollback,
alert storm/notification failure)에서 §1의 기존 7개 실험이 **덮지 못하는** 부분만
추린 것이다. 전부 "설계됨, 미실행"이며, 실행에는 라이브 클러스터가 필요하다.

### 3.1 node-down (노드 전체 장애)

- **가설**: 워커 노드 하나가 완전히 다운(kubelet 응답 없음)되면, 컨트롤 플레인이
  `NotReady`로 표지하고 표준 grace period(기본 5분) 후 해당 노드의 파드가 다른
  노드로 재스케줄된다.
- **왜 기존 것과 다른가**: `worker-cpu-stress`는 노드를 살려둔 채 특정 파드만
  CPU를 태운다. 이건 노드 자체(kubelet/컨테이너 런타임)를 죽이는 것 — Chaos Mesh의
  `PodChaos`가 아니라 `NodeChaos`(kubelet 프로세스 킬 또는 VM 자체 중단)가 필요.
- **예상 탐지 신호**: `kubectl get nodes` NotReady 전환 시각, 재스케줄된 파드의
  새 노드 배정 시각, 그 사이 서비스 가용성(portal `/login` 200 유지 여부).
- **구현 스케치**: Kakao는 VM 기반이므로 `NodeChaos`보다 OpenTofu로 해당 인스턴스를
  stop하는 쪽이 더 현실적인 장애 모사다. Chaos Mesh 대신 스크립트
  (`scripts/test/` 하위에 `node-outage-inject.sh` 같은 이름)로 별도 구현 필요 —
  이번 패스에서 만들지 않았다.
- **상태**: 미구현.

### 3.2 registry outage (내부 레지스트리/Harbor 단절)

- **가설**: 이미지 레지스트리(에어갭 미러 또는 Harbor)가 응답하지 않아도, 이미
  풀된 이미지로 뜬 파드는 영향받지 않아야 하고, 신규 파드 스케줄만 `ImagePullBackOff`로
  실패해야 한다(연쇄 장애로 번지면 안 됨).
- **관련**: `docs/common/apisix-etcd-recovery.md`(다른 종류의 etcd 장애)와
  `docs/common/airgap-isolation-testing.md`의 "미러가 실제로 쓰이는가" 절이
  인접 관심사이지만, 레지스트리 자체를 인위적으로 끊는 실험은 없다.
- **구현 스케치**: `NetworkChaos`(레지스트리 파드로 가는 트래픽 partition)로
  구현 가능 — 이미 `coredns-latency`가 같은 메커니즘(NetworkChaos)을 쓰고 있어
  패턴 재사용이 쉽다.
- **상태**: 미구현.

### 3.3 storage failure (NFS / SeaweedFS)

- **가설**: NFS 서버(csi-driver-nfs가 의존)가 일시 응답 불가일 때, 이미 마운트된
  PV를 쓰는 파드는 I/O 대기로 느려지되 죽지 않아야 하고, 서버 복구 후 자동으로
  정상화돼야 한다.
- **왜 중요한가**: `docs/common/lessons-log.md`의 2026-07-26 AppArmor/NFS 연쇄
  장애(containerd 1.7 pin 사건, R01이 지금 이걸 정적으로만 막고 있음)가 정확히
  이 카테고리다 — 정적 체크는 "그 버그가 재발하지 않는다"만 증명하지, "NFS가
  실제로 느려질 때 견디는가"는 증명하지 못한다.
- **상태**: 미구현. project memory에도 "chaos & k6 suite: … 잔여: NFS 스토리지"로
  이미 알려진 갭으로 기록돼 있음 — 이번 트리아지가 처음 발견한 게 아니라, 계속
  이월되고 있는 항목.

### 3.4 backup/restore failure (Velero)

- **가설**: `verify-backup.sh`(2단계 스모크)는 백업이 성공했는지만 본다. 이 시나리오는
  한 걸음 더 나가 — 백업 대상 리소스를 실제로 지운 뒤 **복구가 실제로 원상태를
  되돌리는지**, 그리고 백업 자체가 실패하는 경로(예: 스토리지 꽉 참, 자격증명
  만료)에서 알림이 오는지를 검증한다.
- **구현 스케치**: `run-chaos.sh`에 새 실험 추가(`velero-restore-drill`) — 네임스페이스
  하나를 백업 → 삭제 → 복구 → 리소스 diff.
- **상태**: 미구현.

### 3.5 upgrade 실패 / rollback (T5 ∩ T6)

- **가설**: 컴포넌트 업그레이드(예: ArgoCD 마이너 버전) 도중 실패하면, GitOps
  selfHeal이 이전 버전으로 자동 되돌리거나, 최소한 명확한 실패 신호와 수동 rollback
  절차가 있어야 한다.
- **왜 없는가**: 이 저장소는 지금까지 "clean install"만 반복 검증해왔고(3회,
  lessons-log의 대부분), 기존 클러스터를 "업그레이드"한 시나리오의 회귀 하네스가
  없다. `version-check.yml`도 버전 *일치*만 보지 업그레이드 *절차*는 다루지 않는다.
- **상태**: 미구현 — T6 신규 작업과 겹친다(`test-strategy.md` §9의 다음 단계 3번).

### 3.6 alert storm / notification failure

- **가설**: 장애 폭주 시 Alertmanager가 중복 알림을 억제(inhibit/group)하면서도
  최초 알림은 지정된 채널(Slack/Discord/Email)로 확실히 도달해야 한다.
- **확인된 사실**: `gitops/resources/alertmanager-config.yaml`을 직접 읽었다 —
  `receiver: default`/`critical`/`warning`이 라우팅 규칙에는 있지만, 실제
  webhook 설정(Slack/Discord)은:

  ```yaml
  # 기본: 알림 저장만 (webhook 미설정)
  # TODO: 프로덕션 환경에서 Slack/Discord/Email webhook 설정
  # webhookConfigs:
  #   - url: 'https://hooks.slack.com/services/T.../B.../...'
  ```

  주석 처리된 TODO 상태다. **알림 채널이 아예 배선돼 있지 않으므로, 이 시나리오는
  "미실행"이 아니라 "테스트할 대상 자체가 없다"**가 정확한 상태 — 이슈 시나리오 4번의
  "즉시 messenger notification" 전제가 이 저장소에서 아직 성립하지 않는다.
- **상태**: 미구현, 그리고 선행 조건(webhook 설정)부터 없음.

## 4. 요약

| 항목 | 존재 | 마지막 검증 | 비고 |
|------|:---:|-------------|------|
| pod/component kill (5종) | ✓ | 2026-07-17 | 재검증 필요(클러스터 재기동 후) |
| network latency (DNS) | ✓(부분) | 2026-07-17 | 완전 단절이 아니라 지연만 |
| database failover | ✓ | 2026-07-17 | |
| host CPU 과부하 | ✓(부분) | 2026-07-17 | 단일 노드만, 호스트 전체 과다구독 재현 아님 |
| node 전체 다운 | ✗ | — | 설계만, §3.1 |
| registry outage | ✗ | — | 설계만, §3.2 |
| storage(NFS/SeaweedFS) 장애 | ✗ | — | 설계만, §3.3. 기존에 알려진 이월 갭 |
| backup/restore 실패 주입 | ✗ | — | 설계만, §3.4 |
| upgrade 실패/rollback | ✗ | — | 설계만, §3.5 |
| alert storm/notification | ✗ | — | 선행조건(webhook)부터 없음, §3.6 |

7개 존재 자산 중 실제로 이슈의 요구를 완전히 만족하는 것은 `cnpg-primary-kill`
정도이고, 나머지는 부분적(지연만, 단일 노드만)이다. 6개 신규 시나리오는 전부
미구현이며, 이번 패스는 그중 어느 것도 구현하지 않았다 — 라이브 클러스터가 없어
Chaos Mesh 실험을 새로 작성해도 검증할 방법이 없고, 검증 안 된 chaos 실험 YAML을
"추가했다"고 보고하는 것은 이 프로젝트의 "no fake completion" 원칙에 어긋난다.
