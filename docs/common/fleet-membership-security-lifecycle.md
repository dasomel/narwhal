# Fleet Cluster Membership 보안 Lifecycle (제안)

- 상태: 제안 (현재 구현과 분리)
- 범위: Narwhal fleet에 연결되는 member cluster의 식별, bootstrap trust, 자격 증명, 격리 및 폐기
- 근거 구현: `scripts/cluster/13-3-export-narwhal-portal-cluster-credentials.sh`의 Portal용 credential export와 `docs/common/multicluster-control-plane.md`의 등록 절차
- 경계: 이 문서는 설계 계약이다. 아래 `PROPOSED` 상태/필드/동작은 현재 API 또는 controller가 제공한다는 뜻이 아니다.

## 현재 저장소에서 확인되는 것

`13-3-export-narwhal-portal-cluster-credentials.sh`는 호출자가 `--cluster-id`, 표시 이름, 환경, provider, 출력 디렉터리를 지정하게 한다. ID는 RFC 1123 label로 검증하며 active `kubectl` context에서 API server와 CA를 읽는다. 스크립트는 `devtools/narwhal-portal` ServiceAccount와 해당 계정을 `narwhal-portal` ClusterRole에 연결한 `narwhal-portal` ClusterRoleBinding을 확인한 뒤 `kubectl create token ... --duration=24h`로 토큰을 요청한다. 등록 JSON에는 credential 값이 아닌 `credentialRef.apiServerEnvVar`와 `credentialRef.tokenEnvVar`가 기록된다. `credentials.env`와 생성 kubeconfig는 `0600`, 출력 디렉터리는 `0700`이다.

`docs/common/multicluster-control-plane.md`는 등록 body를 `POST /api/settings/clusters` 형식으로 설명하고, API/token 값을 Portal secret store에 별도로 전달하도록 한다. 같은 문서는 Portal credential 참조에 CA 변수 필드가 없고 자동 갱신 경로도 없다고 명시한다. 따라서 현재 근거는 operator가 수행하는 export 및 수동 등록 준비이지, one-time bootstrap, cluster identity attestation, mutual authentication, heartbeat, credential overlap/rotation, revoke 또는 decommission 자동화가 아니다. 기존 `narwhal-portal` 권한도 이 exporter 경로 자체만으로 fleet-wide per-cluster/tenant RBAC를 증명하지 않는다.

## 제안 결정

### D1 — 식별자와 재설치 의미

**PROPOSED:** member는 `cluster_id`라는 변경 불가 machine identity로 식별한다. `display_name`, provider account/resource ID, API endpoint, kubeconfig context, credential은 별도 속성이다. registry는 `cluster_id`의 uniqueness 제약을 둔다. 동일 ID의 두 번째 enrollment는 `duplicate_identity`로 거부하고, 이름·endpoint 변경만으로 identity를 바꾸지 않는다.

신규 설치/rebuild는 새 `cluster_id`를 받아야 한다. 기존 ID의 재사용은 이전 멤버가 revoke된 뒤 별도의 승인된 re-enrollment 절차에서만 허용한다. 복제된 cluster가 ID와 bootstrap material을 함께 복사한 경우에는 duplicate enrollment 및 동시 lease 충돌로 quarantine한다. **ASSUMPTION:** 현재 저장소에는 cluster 내부의 immutable identity object 또는 clone 증명 수단이 확인되지 않았다. 발급기관이 서명한 identity proof를 두는 방법은 **PROPOSED**이며 구현 전까지 clone 판별을 보장하지 않는다.

### D2 — 상태 기계와 배치 자격

**PROPOSED:** 허용 상태 및 전이는 다음과 같다. 상태 변경은 actor, 시각, 이유, correlation/evidence ID와 함께 append-only audit evidence로 남긴다. 자격 증명 상태와 placement/traffic 적격성은 분리한다.

| 상태 | 의미 및 허용 전이 |
|---|---|
| `pending` | enrollment 요청 접수. 아직 운영 권한 없음. `verified` 또는 실패 정리로 이동 |
| `verified` | 독립된 operator attestation 및 identity 충돌 확인 완료. bootstrap 발급 가능 |
| `active` | 양방향 인증, 권한 검사, 초기 health/lease 확인 완료. 명시적으로 허용된 작업만 수행 |
| `degraded` | 일시적 health/lease 실패. 읽기·진단은 정책상 허용 가능하나 placement/traffic 신규 할당 금지 |
| `quarantined` | compromise, duplicate identity, 불일치 증거 또는 operator 조치. 즉시 신규 자격 거부 및 placement 제외 |
| `draining` | 폐기 승인 후 workload 이동/할당 중단. 기존 자격은 제한된 정리 작업에만 허용 |
| `revoked` | 모든 fleet 자격 폐기 확인. terminal 상태; 재가입은 새 승인 절차 |

`pending → verified → active`만 정상 가입 경로다. `active → degraded → active`는 lease와 인증 검사가 회복되었을 때 가능하다. 어떤 비활성 상태에서도 compromise/중복 발견은 `quarantined`로 갈 수 있다. `quarantined → active`는 회전된 자격 검증과 별도 operator 승인 후에만 허용한다. `active|degraded|quarantined → draining → revoked`는 폐기 경로다. 실패한 enrollment는 pending 상태를 정리하고 발급된 모든 임시 자격을 revoke하며, active로 전이하지 않는다.

**PROPOSED 배치 기준:** `active`이고 현재 lease가 신선하며 health gate가 통과된 cluster만 placement/traffic 후보가 된다. stale 임계시간과 health probe는 fleet 정책으로 명시하며 임의의 기본 초를 두지 않는다. `degraded`, `quarantined`, `draining`, `revoked`는 신규 배치 대상이 아니다. 이미 실행 중인 workload의 이동은 별도 workload 안전 정책을 적용한다.

### D3 — Attestation 및 bootstrap trust

**PROPOSED:** 요청자는 enrollment 요청에 `cluster_id`, 표시 이름, endpoint, provider/resource 식별 참조, 요청 목적을 제출한다. endpoint나 표시 이름만으로 소유권을 증명할 수 없다. Fleet operator는 cluster 생성/소유 인벤토리와 요청 ID를 대조해 사람으로 attestation하고, cluster operator는 자신이 선택한 대상 API에서 생성된 요청임을 확인한다. 두 증거가 일치해야 `verified`가 된다. 자동 provider 증명 연동은 존재가 확인되지 않아 후속 확장이다.

Verified 요청에만 예측 불가능하고 1회 사용 가능한 bootstrap material을 발급한다. **PROPOSED 프로파일:** 최대 유효기간 15분, 특정 `cluster_id` 및 enrollment attempt에 바인딩, 성공/만료/취소 후 재사용 불가. 이 material은 단기 client identity를 발급받는 데만 쓰며, fleet 운영 API나 Kubernetes admin 권한을 갖지 않는다. bootstrap 서명 검증 키/issuer pin은 승인된 관리 경로로 member에 전달한다. Bootstrap token은 로그·Git·일반 상태 저장소에 남기지 않는다.

Enrollment는 challenge-response로 member proof를 확인하고 server certificate/CA를 검증한 뒤에만 완료한다. 양방향 인증 및 mTLS는 **PROPOSED**이고 현재 exporter의 bearer-token 방식과 동일시하지 않는다. Offline 환경에서는 동일한 서명된, cluster-bound bundle과 만료/one-time 규칙을 쓰고, 사용 결과·발급자 서명·시각을 재연결 시 audit에 동기화한다. 신뢰된 시각이나 서명 검증 키가 없으면 offline enrollment를 성공 처리하지 않는다.

### D4 — 자격 발급, 회전, 만료, 철회

**PROPOSED 목표 credential:** cluster별 workload identity와 좁은 scope의 작업 권한을 갖는 client credential. 사람의 admin kubeconfig는 fleet membership credential로 사용하지 않는다. 기존 exporter의 `devtools/narwhal-portal` 24시간 ServiceAccount token은 현재 Portal 읽기 경로의 사실이며, 이 제안의 target identity protocol로 간주하지 않는다.

| 자격 | 제안 수명/전환 |
|---|---|
| bootstrap material | 최대 15분, 1회 enrollment 전용. 성공/만료 즉시 무효 |
| member credential | **PROPOSED:** 최대 24시간. 자동 발급 지원 여부와 관계없이 만료 전 rotate |
| 회전 overlap | **PROPOSED:** 새 credential 검증 후 최대 1시간 old/new 동시 유효. 전환 확인 즉시 old 폐기; 1시간 초과 시 자동 overlap 금지 |

정상 회전은 `new credential issued → member proves new credential → control plane accepts new → old revoked → evidence closed` 순서다. 새 자격 검증 전 old를 제거하지 않아 무중단 전환을 지원하되, overlap 중 각 키의 ID와 사용 기록은 구분한다. 만료 전 교체하지 못하면 credential은 만료로 거부되고 member는 `degraded`로 내려가며, 새 bootstrap/recovery 승인 없이 무기한 연장하지 않는다.

**PROPOSED 즉시 철회:** 의심 credential을 우선 denylist/revoke하고 해당 멤버를 `quarantined`로 전이한다. 정상 rotation과 달리 overlap을 허용하지 않는다. 신뢰된 경로로 새 identity를 발급하고 증거를 검토한 뒤 operator 승인으로 재활성화한다. 철회 전파 완료를 확인할 수 없으면 완료로 간주하지 않고 해당 credential을 사용하는 작업을 차단한다. Revocation은 fleet API authorization 및 member credential verifier 양쪽에 적용되어야 한다.

### D5 — Lease, 실패 규칙, decommission evidence

**PROPOSED:** active member는 인증된 heartbeat/lease를 갱신한다. lease 레코드는 `cluster_id`, credential key ID, monotonic sequence, 발급 시각, 만료 시각, health summary, correlation ID를 포함하며 비밀값은 포함하지 않는다. sequence 역행, ID 불일치, 만료, 서명 불일치, API/CA 검증 실패는 성공 응답으로 취급하지 않는다. 설정 가능한 freshness window 초과 시 `degraded`, 장기 미복구 시 quarantine은 operator policy로 결정한다. Stale 상태에서는 placement/traffic 신규 사용을 차단한다.

Decommission은 `draining → revoked` 순서로 수행한다: (1) 승인자와 대상 `cluster_id`를 재확인하고 backup/evidence 보존 결정을 기록, (2) placement/traffic 후보에서 제외, (3) 허용된 drain 수행, (4) member 및 control plane credential 폐기, (5) 저장소의 secret reference 제거, (6) revoke 확인과 최종 상태를 audit에 기록한다. Registry row/evidence는 삭제하지 않는다. 정리 도중 실패하면 `draining` 또는 `quarantined`를 유지하고 재시도 가능한 단계와 마지막 오류를 기록한다. 자격 revoke 확인 전 `revoked`로 표시하지 않는다.

| 실패 | 필수 처리 |
|---|---|
| SA, binding 또는 token 발급 실패 | 현재 exporter 기준 오류로 중단. 부분 산출물 제거 확인. fleet enrollment 성공으로 간주하지 않음 |
| bootstrap 만료/재사용/다른 ID 사용 | 거부, attempt 기록, 기존 bootstrap 무효화. 새 attempt는 새 attestation부터 시작 |
| endpoint/CA 또는 identity 증거 불일치 | `quarantined`; trust 자동 승인 금지 |
| duplicate ID 또는 동시 lease | 두 번째 활성화 차단, 관련 member 격리 및 operator 판정 |
| 회전 중 새 자격 검증 실패 | 기존 자격은 만료 전까지만 유지, 신규 발급 반복 제한; compromise 의심이면 즉시 revoke/quarantine |
| control plane이 revoke 결과를 확인하지 못함 | 접근 거부 정책 유지, 상태 `draining`/`quarantined`, 확인 전 완료 보고 금지 |
| offline에서 revoke 전달 불가 | 중앙 revoke list를 서명·시각 포함해 전달할 때까지 해당 member를 신뢰 가능한 active로 표시하지 않음 |

## Evidence 및 권한 경계

**PROPOSED evidence record**는 `membership_id` (= `cluster_id`), 이전/새 상태, 행위자와 attestation 종류, credential key ID 및 발급/만료/철회 시각, bootstrap attempt ID, lease/health 판정, 결과/error code, correlation ID를 연결한다. Secret/token, kubeconfig, CA private key는 기록하지 않는다. Issue가 언급한 #42/#108의 실제 correlation 형식 및 #111 privileged-operation 연동은 이 문서에서 구현 사실로 주장하지 않는다. 적용 전 기존 audit contract를 확인해 ID 필드를 매핑해야 한다.

**PROPOSED authorization:** fleet API는 tenant/operator가 허가받은 `cluster_id` 집합을 매 요청에서 검사한다. 멤버 자격은 해당 cluster의 선언된 작업만 허용하고 다른 cluster 조회·변경, tenant 간 접근, 임의 Kubernetes admin 권한을 부여하지 않는다. 현재 exporter의 ClusterRoleBinding이 ClusterRole을 참조한다는 사실만으로 위 per-tenant 경계가 이미 구현됐다고 볼 수 없다.

## 수용 기준과 검증 증거

다음은 구현 완료를 판정할 acceptance 기준이다. 본 문서는 검증 결과를 주장하지 않는다.

1. 동일 표시 이름·다른 `cluster_id`는 독립 가능하고, 동일 `cluster_id`의 두 번째 활성화는 거부된다.
2. bootstrap 성공 후 재사용 및 만료 material은 거부되며 active credential은 bootstrap 용도로 동작하지 않는다.
3. 회전은 새 credential 확인 후 overlap 내 cutover하며, 만료/철회된 credential은 fleet operation을 수행할 수 없다.
4. heartbeat stale, sequence replay, identity mismatch는 placement/traffic 후보 제외를 만든다.
5. compromised credential은 quarantine과 즉시 revoke 경로를 거치며 old credential 재사용은 실패한다.
6. decommission 완료 evidence에는 placement withdrawal, credential revoke, secret reference 제거, 보존된 감사 참조가 모두 연결된다.
7. enrollment 중간 실패는 활성 trust artifact를 남기지 않으며 offline 절차도 동일 evidence 필드를 재현한다.
8. tenant/operator 경계 검증은 허용된 `cluster_id`와 거부된 교차 tenant 요청을 모두 확인한다.

## 범위 밖 및 미결정

Portal 저장소의 API/schema 구현, 실제 cluster-side identity issuer, mTLS/PKI 구현(#107 연계), provider attestation, lease controller, secret manager, placement/traffic 연동, offline bundle format 및 실클러스터 검증은 범위 밖이다. 현 `docs/common/multicluster-control-plane.md`의 수동 24시간 token 절차와 본 문서의 제안 credential protocol 사이에는 구현 간극이 남는다. 이 계약을 구현하기 전에는 해당 제안 상태를 기존 export 흐름에 있다고 문서화하지 않는다.
