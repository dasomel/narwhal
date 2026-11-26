# Changelog

이 프로젝트의 주요 변경 사항은 이 파일에 기록됩니다.

이 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르며,
이 프로젝트는 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 준수합니다.

[English](CHANGELOG.md) | 한국어

## [Unreleased]

## [1.1.32] - 2026-11-26

### 변경

- **Velero node-agent Config Audit 수용 범위 축소:** `storage/node-agent`의 KSV003/KSV106 수용을 제거했다. 이제 모든 capability를 제거한다(#831). 수용 사유에 실측한 백업·복원 결과를 기록했다.
- **OSS 버전 목록에 kubelet-csr-approver 1.2.16 추가** (#825).
- **CSI NFS Config Audit 수용 범위 축소 (R417):** controller KSV012/KSV020은 실측한 root 소유 0755 공유 CSI 소켓을 근거로 수용하고, sidecar 수정 후 KSV001은 privileged SYS_ADMIN nfs 컨테이너만 남으므로 두 워크로드에서 수용한다. KSV021과 KSV125는 조치 대상으로 남긴다.

## [1.1.31] - 2026-11-26

### 변경

- **Batch C 노드 에이전트 Config Audit 수용 범위 축소:** Falco KSV004/KSV030/KSV104, Cilium KSV003/KSV004/KSV025, Envoy KSV025, Envoy·MetalLB speaker·kube-vip·CSI NFS 노드 KSV021은 보고서에 나오면 조치 대상으로 표시된다. Istio CNI·ztunnel·Cilium Envoy의 KSV001 사유에는 SYS_ADMIN 실측과 차트 동작을 기록했다. kube-vip Pod와 MetalLB speaker DaemonSet의 KSV105는 실측에 근거해 해당 리소스에만 수용한다. Falco의 privileged eBPF 수용 사유에는 미검증 상태인 leastPrivileged 후속 경로를 명시했다. 다음 포털 릴리스부터 적용된다.

## [1.1.30] - 2026-11-26

### 변경

- 컴플라이언스에서 `narwhal-master-1`의 API 서버(CIS 1.2.1, 1.2.12)와 `narwhal-db-1` 파드 및 Deployment가 소유한 `narwhal-db-pooler-rw` ReplicaSet의 낮은 UID/GID 검사에 한해 위험 수용을 기록합니다.

## [1.1.29] - 2026-11-25

### 변경

- Trivy 점검 카탈로그(`scripts/gen-trivy-check-catalogue.mjs`, `src/data/trivy-check-catalogue.generated.sha256.json`)를 trivy-operator 0.36.1(내장 trivy-checks `v1.12.2-0.20261012053351-ac85645895ed`) 기준으로 다시 생성했습니다. 생성기는 새 `id` + `aliases` 메타데이터 형식을 읽습니다(#807).
- `ReplicaSet/trivy-operator` 수용 사유가 `trivy-operator:0.36.1`을 가리키도록 바꿨습니다. 운영자는 여전히 UID/GID 10000으로 실행됩니다(라이브 파드 실측) (#807).

### 보안

- 패키지 override로 OSV 권고를 해소했습니다: `minimatch` `^11.0.1`, `glob` `^14.0.2`, `seroval` `~1.6.11` (#803).

## [1.1.28] - 2026-11-25

### 변경

- **Compliance 리소스별 리포트 판정 원천 전환 (`D-COMPLIANCE-VERDICT-SOURCE`, `src/lib/compliance.ts`, `src/lib/trivy-check-catalogue.ts`, `src/types/compliance.ts`, `src/components/compliance/failure-counters.tsx`, `src/components/compliance/frameworks-grid.tsx`, `src/app/(dashboard)/compliance/frameworks/[id]/page.tsx`):**
  - 프레임워크 통제 판정 원천을 `ClusterComplianceReport.status.detailReport.results[].checks`에서 기존 `ClusterRole/narwhal-portal` 권한으로 조회 가능한 리소스별 Trivy 리포트 6종(`configauditreports`, `clusterconfigauditreports`, `rbacassessmentreports`, `clusterrbacassessmentreports`, `infraassessmentreports`, `clusterinfraassessmentreports`)의 페이지네이션 조회(`limit=200`, `metadata.continue`, `410 Gone` 1회 재시작)로 전환하고 `ClusterComplianceReport.spec.compliance.controls[].checks[].id`와 `resolveCanonicalCheckId`(`AVD-KSV-0001`/`KSV001`/`KSV-0001`/`KCV0033` 정규화, `KSV0012`·`KSV012` 비충돌 유지)로 조인하도록 변경했습니다(RBAC 확장 없음).
  - 매핑된 점검 중 하나라도 `success: false`가 있으면 `failed`(기존 정확 수용 규칙 및 `kube-bench` 실패 우선 병합·`sourceDisagreement` 유지), 실패 없이 통제 범위(노드 종류·네임스페이스·리소스 종류) 안의 리소스에 명시적 `success: true` 기록이 1건 이상 있으면 `tested-passed`(`testedByReport: true`, 선택 필드 `reportTestedPassCount`·중복 제거 `testedPassCount` 집계), 그 외에는 기존 정직 상태(`실패 기록 없음` / `미평가`)를 유지하며 기록 부재로 통과를 추론하지 않습니다. trivy-checks 카탈로그에 없는 점검이 매핑된 통제는 미평가로 남습니다(`D-VERDICT-UNKNOWN-CHECK`). `status.detailReport` 체크와 부풀려진 `status.summary.failCount`를 판정 원천에서 제외해 `trivy-operator` `0.36.1`(`reportRecordFailedChecksOnly: "false"`)의 `FromDetailReport` 버그로 통과 항목이 실패로 오판되는 문제를 차단했으며, `0.25.0` 라이브 클러스터 출력은 5개 프레임워크 전 카운트 필드에서 기존 `1.1.27` 실측과 동일함을 포털 ServiceAccount 읽기 전용 조회로 확인했습니다(2026-11-25).

## [1.1.27] - 2026-11-25

### 변경

- 라이브 클러스터에서 사유가 검증된 `narwhal-master-1` 노드의 CIS 벤치마크 1.1.12(kubeadm static-pod root etcd), 1.3.7, 1.4.2(노드 외부 보안 metrics) 점검 결과와 매칭되는 Trivy 인프라 KCV0039(`kube-controller-manager`), KCV0041(`kube-scheduler`) 점검 결과에 실측 노드별 정확 수용 규칙을 추가했습니다(라이브 적용이나 새로운 테스트 통과를 주장하지 않음, `src/data/compliance-acceptance-rules.json`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/lib/i18n.ts`). 매칭은 벤치마크 항목의 경우 `(benchmarkId, controlId, node)`, 인프라 항목의 경우 `(kind, namespace, name, check)`에 대해 정확히 일치합니다. 포털은 관측된 테스트 기록과 기준선 대조 결과만 보고하며 통제를 증명하거나 보장한다고 표현하지 않습니다.
- CIS 1.3.6 / KCV0038은 수용 규칙 없이 조치 필요로 유지합니다. 관측된 kubelet은 `serverTLSBootstrap`·CSR이 없고 자체 서명 서빙 인증서를 사용하며, `kube-bench` `PASS`는 controller-manager 게이트만 검사합니다. 1.2.1, 1.2.6, 1.2.10, 1.2.12, 1.3.6, 4.2.6 통제는 담당자 결정에 따라 조치 필요로 유지됩니다(CIS 1.3.1을 위한 controller-manager 플래그는 클러스터 변경 사항이며 포털 변경이 아닙니다).

## [1.1.26] - 2026-11-24

### 변경

- **Compliance > CIS `kube-bench` 테스트 기록 연동 (`src/lib/kube-bench.ts`, `src/lib/compliance.ts`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/data/compliance-acceptance-rules.json`, `src/types/compliance.ts`, `src/lib/cache-keys.ts`, `src/lib/i18n.ts`, `src/components/compliance/failure-counters.tsx`, `src/components/compliance/frameworks-grid.tsx`, `src/app/(dashboard)/compliance/frameworks/[id]/page.tsx`):**
  - 기존 포털 RBAC 변경 없이 기존 `configmaps` list/get 권한으로 `security-system` 네임스페이스의 노드별 `kube-bench` ConfigMap(#792의 정기 CIS 벤치마크가 기록, `narwhal.io/compliance-tool=kube-bench`, `record.json`)을 조회해 `k8s-cis-1.23` 평가의 테스트 근거로 연동했습니다. `schemaVersion=1`, `tool=kube-bench`, `benchmarkId=cis-1.23`, `status=complete`, 중복 없는 비어 있지 않은 `controlId`, `totals` 일치, 미래 시각이 아닌 `finishedAt`, 26시간 상한(`BENCHMARK_MAX_AGE_HOURS = 26`), `/api/v1/nodes` 실존 노드 확인, 512 KiB JSON 크기 상한, 잘림/403 비캐시 등 fail-closed 검증과 섹션별 노드 커버리지(1.x/2.x/3.x 전체 컨트롤플레인 노드, 4.x 전체 노드, 5.x 클러스터 1회)를 적용했습니다. 이 규칙은 벤치마크 근거 자체에 대한 것이며 최종 통제 상태가 아닙니다. 26시간 초과·오류 상태·형식 오류·중복 또는 누락된 기록은 벤치마크 판정을 내지 않고(사유 표시), `WARN`/`type: "manual"`(`manual`)과 `INFO`(`benchmark_info`) 결과도 판정을 내지 않습니다. 최종 상태는 Trivy와의 실패 우선 병합을 따릅니다. Trivy가 실패를 기록한 통제는 실패로 유지되고, Trivy가 실패 없이 보고한 통제는 "실패 기록 없음"으로 유지되며, 두 소스 어디에도 근거가 없는 통제만 미평가로 표시됩니다.
  - Trivy와 도구 간 실패 우선(failure-first) 병합을 적용했습니다: Trivy 실패 또는 노드 `kube-bench` `FAIL` 중 하나라도 있으면 컨트롤을 `failed`로 유지하고(판정 불일치 시 `sourceDisagreement`로 양쪽 판정 병기, 예: CIS `1.3.6`), 검증·커버리지 결함은 전용 한영 `benchmark_*` 사유로 표시합니다. 2026-11-24 포털 ServiceAccount 권한 실측에서 cis-1.23(116개 통제)은 테스트 통과(벤치마크) 70, 실패 기록 없음 4, 실패 25(조치 필요 24, 수용 1), 미평가 17(이전: 0 / 4 / 23 / 89)로 집계되었습니다. 포털은 관측된 테스트 기록과 기준선 대조 결과만 보고하며 통제를 증명하거나 보장한다고 표현하지 않습니다.
  - 벤치마크 점검 결과는 정확한 규칙으로만 수용 가능하도록 `compliance-acceptance-rules.json`에 빈 `benchmark` 배열과 정확 일치 로더/매처(`matchBenchmarkException`: `benchmarkId`, `controlId`, 선택적 `node`)를 확장했습니다(이번 릴리스에서 추가된 수용 규칙 없음). 스캐너 원본 통과 수 렌더링을 제거하여(기존 CIS 95/116, 전체 122/178로 표시되던 원본 수치 대신 근거 기반 74/116 및 86/178 표시) 요약 카드·그리드·상세 UI가 `테스트 통과(벤치마크)`를 `실패 기록 없음`과 분리 집계하고 도구·버전·노드 수·타임스탬프 및 FAIL `actualValue`/`expectedResult` 발췌를 표시하도록 개선했습니다. 프레임워크 목록·상세·요약 캐시 키를 `v4`로 올렸으며 회귀 검증 `R402`(23개 변이)를 통과합니다.

## [1.1.25] - 2026-11-24

### 변경

- **Compliance > 닫힌 세계(closed-world) 기준선 기반 KISA 실시간 통제 점검 (`src/lib/kisa.ts`, `src/lib/kisa-checks.ts`, `src/lib/kisa-checks-ops.ts`, `src/lib/kisa-controls.ts`, `src/lib/kisa-policy-canonical.mjs`, `src/data/kisa-policy-baseline.json`, `scripts/gen-kisa-policy-baseline.mjs`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/data/compliance-acceptance-rules.json`, `src/lib/oss-updates/version.ts`, `src/lib/k8s-client.ts`, `src/lib/trivy.ts`, `src/types/security.ts`, `src/types/kisa.ts`, `src/lib/cache-keys.ts`, `src/lib/i18n.ts`, `src/components/compliance/kisa-checklist.tsx`):**
  - 남아 있던 고정 상태 6개(SEC-01, ETCD-02, IMG-01, TLS-01, OBS-01, IMG-02)를 실시간 평가로 바꾸고(IMG-01은 노드 파일을 API로 볼 수 없어 사유가 붙은 `manual`), KISA 16개 통제 전체를 닫힌 세계 원칙(`D-KISA-CLOSED-WORLD`, `R397`, `R399`)에 따른 Kubernetes API 평가로 전환했습니다. 2026-11-24 관리자 권한 실측 분포는 pass 3 / fail 4 / warn 8 / manual 1입니다(1.1.24는 pass 6 / fail 6 / warn 4였고, 그중 10개는 실시간·6개는 고정 상태).
  - 이 모델에서 `pass`는 관측된 상태가 사전에 열거·검토된 기준선 형상(`src/data/kisa-policy-baseline.json` schemaVersion 2 내 Kyverno 정책 10건, NetworkPolicy/CiliumNetworkPolicy 매니페스트 33건, 클러스터 전역 정책 1건)과 일치함을 의미합니다. 포털은 통제를 증명하거나 보장한다고 표현하지 않으며, 이미 cluster-admin 권한을 획득하고 검토 형상과 바이트 단위로 동일한 객체를 위조하는 공격자에 대한 변조 탐지를 주장하지 않습니다. `KISA-SEC-01`, `KISA-ETCD-02`, `KISA-IMG-01` 세 개 통제는 설계상 pass 경로가 없습니다.
  - 포털 RBAC(`ClusterRole/narwhal-portal`)에 회귀 검증(`R378`, `R397`)으로 고정된 최소 읽기 전용 권한을 추가했습니다: `security.istio.io/peerauthentications` [list], `batch/cronjobs` [get, list], `wgpolicyk8s.io/policyreports` [list], `cilium.io/{ciliumnetworkpolicies,ciliumclusterwidenetworkpolicies}` [list]. Secret 읽기 권한은 부여되지 않았습니다.
  - `KISA-IMG-02`는 임의 Kyverno 규칙/CEL 해석을 제거하고 `kisa-policy-baseline.json` 대비 정규화 스펙 SHA-256 대조(값 검증 후 문서화된 Kyverno 1.18 기본값 9개 제거), `Ready=True`, 오버라이드 거부(`specHasOverrides`), `enforceEligible` 검증으로 전환했습니다. `KISA-ADM-01`은 `admissionRelevant: true` 전체 정책을 기준선과 대조하고 미검토 정책을 노출합니다.
  - `KISA-NET-01`(`checkNet01`)과 `KISA-ETCD-02`(`checkEtcd02`)는 `CiliumClusterwideNetworkPolicy`와 기준선 `clusterwidePolicies`의 일치 여부를 검증합니다(CCNP 미검토/드리프트 시 `warn`, `KISA-NET-01`에서는 CCNP 미검토/드리프트 시 충족 네임스페이스를 `0/5`로 집계, 403/API 실패 시 `manual`). `KISA-ETCD-02`는 `platform-system` 내 모든 `NetworkPolicy`·`CiliumNetworkPolicy`의 기준선 다이제스트 일치를 요구하고(네임스페이스 내 미검토 정책 시 `warn`), 실행 중인 `platform-system/apisix-etcd` 파드 템플릿 라벨에 대한 `matchesLabelSelector`(`null`/`undefined`는 미선택, `{}`는 전체 선택; 불일치 시 `warn`, 롤아웃 필드 누락 시 `manual`)를 검증하며, pass 경로 없이 관측 사실만 보고합니다(`--config-file` 존재 시 `flags may be overridden by the config file; not evaluated`; pass 경로는 검토된 etcd TLS 워크로드 스펙 다이제스트와 함께 추가 예정).
  - `KISA-OBS-01`(`checkObs01`)은 `AlertmanagerConfig` `v1alpha1` CRD 15종 통합 타입별 필수 목적지 테이블(`ALERTMANAGER_INTEGRATION_VALIDATORS`)을 적용해 빈 `webhookConfigs: [{}]`나 `apiURL` 누락 `slackConfigs` 및 미등록 타입을 `warn`으로 감지하며, `Ready=True`인 라벨 매칭 Falco/falcosidekick 파드를 요구합니다. `KISA-CP-01` 및 `KISA-ETCD-01`은 `Ready=True` 파드 조건이 필수인 `kube-apiserver`로 검사를 엄격히 격리했습니다.
  - `KISA-RBAC-01` 및 `KISA-RBAC-02`는 이름 접두사 예외 없이 `D-KISA-CLOSED-WORLD` 원칙으로 RBAC 바인딩을 평가합니다: `portal/src/data/compliance-acceptance-rules.json`(`rbacBindings`, `R395`)은 업스트림 Kubernetes release-1.35 기본값과 차이 0건으로 전수 일치하는 44개 부트스트랩 `ClusterRoleBinding`을 명시적으로 열거하며, 부트스트랩 메타데이터, 정확한 `roleRef`, 비어 있지 않은 `checks`, 정규화된 `subjects` 집합 일치를 요구하고, 네임스페이스 `Role/dev/cluster-admin`은 `KISA-RBAC-02` 대상임을 명시했습니다.
  - `KISA-TLS-01`(`checkTls01`)은 이름 우선순위와 생성 시각 기준 우선순위 재구현을 제거했습니다: `istio-system` 내 selector 없는 `PeerAuthentication`이 정확히 1건이고 `mtls.mode === "STRICT"`이며 `portLevelMtls`가 없을 때만 유효하며(0건→`warn`, 2건 이상→`warn`, 비-STRICT→`fail`), selector 없는 정책에 `UNSET (inherits)`를 보고하고, 단일 `STRICT` 루트 아래 비루트 네임스페이스의 단독 selector-less `UNSET` 정책에 대해서만 `portLevelMtls`가 없을 때 `STRICT` 상속을 확인합니다.
  - 16개 전 통제에 `ko`/`en` `detailLocalized`와 `reasonKey`를 완비하여(`정확 규칙 미수용` / `not accepted by an exact rule`), PSA `audit`/`warn`/`privileged`, `notAfter` 누락 인증서, 실패·잘린 Trivy 요약, `rootPa` `portLevelMtls`, 통제 0건에 따른 거짓 통과를 차단했습니다.
- **의존성 업데이트**: `next` 및 `eslint-config-next` 16.4.6(#708), `@opentelemetry/auto-instrumentations-node` 0.83.1, `@opentelemetry/exporter-trace-otlp-http` 및 `@opentelemetry/sdk-node` 0.301.2, `@opentelemetry/resources` 3.1.1(#709), `@playwright/test` 1.65.1, `@vitejs/plugin-react` 6.3.0, `vite` 8.3.8(#710), `zustand` 5.1.0(#711), `@grpc/grpc-js` 1.14.7(#712)로 판올림했습니다.
- **설정 > OSS 업데이트(배포 측 변경, #778)**: 포털 코드 변경은 없습니다. GitOps 차트가 포털에 CRD `gateways.gateway.networking.k8s.io` 하나에 대한 `get` 권한을 부여해 Gateway API CRDs 행이 "알 수 없음: 권한 없음" 대신 버전을 표시하고, `OSS_UPDATES_UPSTREAM=true`(기본·vagrant 프로바이더, 에어갭 kakao 프로바이더는 `false`)를 설정해 업스트림 최신 릴리스 조회를 켭니다.

## [1.1.24] - 2026-11-23

### 수정

- **OSS 업데이트 실행 Pod 실측·접미사 허용목록·실행 이미지 CVE 귀속·사유 명시 (`src/lib/oss-updates/catalog.ts`, `src/lib/oss-updates/status.ts`, `src/lib/oss-updates/version.ts`, `src/lib/k8s-client.ts`, `src/lib/trivy.ts`, `src/types/oss-updates.ts`, `src/components/settings/oss-updates-table.tsx`, `src/lib/i18n.ts`):**
  - 기존 포털 RBAC 범위 내 직접 Kubernetes 조회(`getOssClusterInventory()`: `/version`, bounded `deployments`·`daemonsets`·`statefulsets`·`replicasets`, 실제 실행 중인 `status.containerStatuses[].image`·`state.running`·`imageID` digest·`ownerReferences`·`deletionTimestamp`·정적 Pod `kubernetes.io/config.mirror`+`nodeName` 조회용 `/api/v1/pods`, `gateways.gateway.networking.k8s.io` CRD 403 확인)로 전환했습니다. 라벨만 겹치는 Job Pod 등은 소유권(`ownerReferences`) 검증으로 제외하고, 목록 페이지 상한 초과(`truncated`) 시 `목록이 잘려 전체 Pod을 확인하지 못함`(`unknown`)으로 처리하며, Terminating·Pending·Succeeded·Failed·비실행 컨테이너는 실행 중으로 세지 않습니다. 동일 태그라도 `imageID` digest가 다르면 축약 digest와 함께 `롤아웃 중 (혼재)` 드리프트로 표시하며, 워크로드·컨테이너 부재나 K8s 조회 실패 시 차트 버전·Trivy 태그만으로 `일치`를 내지 않고 `Helm`은 `해당 없음 (CLI)`로 구분합니다.
  - 부분 문자열 `imageMatch` 대신 실행 중인 이미지의 정확한 `repo+tag`(또는 `digest`)만 매칭(`summarizeCvesForArtifacts`)해 이전 태그 리포트가 현재 실행 이미지 CVE로 합산되지 않도록 했으며(미일치 시 `스캔 없음 (이 실행 이미지의 리포트 없음)`), 동반·초기화·사이드카 이미지도 개별 CVE 수치와 조회 실패·digest-only(`태그 없음 (digest 고정)` 및 축약 digest) 사유를 생략 없이 표시합니다.
  - 컴포넌트별 허용 접미사 목록(`allowedTagSuffixes`: Gitea `-rootless`, APISIX `-ubuntu`, Istio `-distroless`) 외의 접미사(`-rc1`, `-preview1`, `-custom`)와 비-SemVer 태그는 원문 그대로 `태그 해석 불가`로 두고 `일치` 처리하지 않습니다(`R394`).

### 변경

- **Compliance 정확한 예외 수용 (`src/data/compliance-acceptance-rules.json`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/components/compliance/failure-counters.tsx`, `src/lib/i18n.ts`):** namespace·기본 역할·이름 부분문자열 일괄 수용을 범위·namespace·kind·정확한 이름·검사 ID별 검토 규칙으로 대체했습니다(Config Audit 규칙 73->45, RBAC 4->47 [31개 Kubernetes 부트스트랩 역할 포함]). 근거 미확인 예외를 제거하고 ReplicaSet Deployment 소유 확인, 캐시 버전 갱신, summary API·한영 UI의 미사용 규칙 목록/개수를 추가했습니다(`R395`).

- **Compliance 정직한 3-상태 판정 및 조회 실패 구분 표시 (`src/lib/compliance.ts`, `src/lib/kisa.ts`, `src/types/compliance.ts`, `src/components/compliance/*`, `src/app/api/compliance/*`, `src/app/(dashboard)/compliance/*`, `src/lib/i18n.ts`, `src/data/trivy-check-catalogue.generated.json`):**
  - 생성·고정된 158개 점검 카탈로그(`src/data/trivy-check-catalogue.generated.json`) 기반으로 실패 전용 점검을 평가하고, Trivy의 `{ checkID: "", success: true }` 플레이스홀더를 통과로 간주하던 가정을 세 가지 상태(`passed`, `failed`, `not_evaluated`)의 컨트롤 평가로 대체해 `passed`는 해당 종류·범위의 리포트에 그 컨트롤의 카탈로그 점검 ID로 기록된 실패가 없다는 뜻으로만 쓰도록 변경했습니다(스캐너는 실패만 기록하며, 인프라 점검은 `passed`로 표시하지 않음)(`no_evidence_in_report`, `coverage_unknown`, `absent_from_report`, `excluded_namespace`, `unsupported_kind`, 스펙/수집기 `nodeType` 기반 `missing_node_type`, `unlinked_check_id`, `manual`, `scanner_disabled`, `summary_only`, `empty_checks`) (`R387`, `R389`, `R391`, `R392`, `R393`).
  - 요약 프레임워크 통과율을 분자/분모 라벨 포함 풀링 비율로 계산하고 모든 `formatPassRatePct` 호출(원본 통과율 포함)에 `hasNotEvaluated`를 전달해 `100%` 누수를 차단했으며, KISA `CFG-01`이 조치가능 실패 시 `fail`, 미평가 잔존 시 `warn`, 전수 평가·조치가능 0건일 때만 `pass`가 되도록 변경했습니다.
  - Config Audit에서 미수용 MEDIUM+(`totalConfigAuditFailures`)와 별도로 미수용 LOW(`unacceptedLowConfigAuditFailures`) 및 수용 위생 LOW(`lowSeverityConfigAuditFailures`)를 분리 표시하고, 제외된 구 ReplicaSet 수(`droppedConfigAuditReports`·`droppedConfigAuditFailures`·`droppedFailCount`·`droppedCheckCount`)를 대조할 수 있게 했습니다.
  - Kubernetes API 목록 조회 오류를 `0`이나 빈 배열로 숨기거나 실패·부분 응답을 캐시하지 않고 API `503 { unavailable: true }`(전 소스 실패 시 `/api/compliance/summary` 포함), 부분 조회 알림 배너(`partial`/`partialSources`), 소스별 타임스탬프(`status.updateTimestamp` 우선)·6시간 재집계 주기 안내를 표시합니다.
- **`KCV0056` 인프라 수용 규칙 제거 (`src/lib/compliance-accepted.ts`, `src/lib/i18n.ts`):**
  - `gitops/charts/narwhal-apps/templates/trivy-operator.yaml`에서 호스트 `/etc/cni/net.d/`를 컨테이너 `/etc/cni`로 마운트해(`D-NODE-COLLECTOR-CNI-MOUNT`) `node-collector` `CMD-0009`(`stat -c %a /*/cni/*`)가 `700` 디렉터리가 아닌 내부 CNI 파일을 검사하도록 수정됨에 따라 `ACCEPTED_INFRA_RULES`에서 `KCV0056` 수용 규칙을 제거하고 `compliance.acceptedReason.infraCniDirGlob` ko/en i18n keys를 삭제했습니다. fail-closed 인프라/프레임워크 수용·조치가능 분리 구조는 유지하므로 실패한 `KCV0056`은 다시 조치가능으로 표시되며 향후 데이터 전용 규칙 추가도 계속 지원합니다.

## [1.1.23] - 2026-11-20

### 변경

- **`KCV0056` 인프라 감사 및 프레임워크 수용/조치가능 분리 (`src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/types/compliance.ts`, `src/components/compliance/infra-audit-list.tsx`, `src/components/compliance/failure-counters.tsx`, `src/app/(dashboard)/compliance/infra-audit/[node]/page.tsx`, `src/lib/i18n.ts`):**
  - `normalizeCheckId`가 `KCV` ID(`AVD-KCV-0056 -> KCV0056`)를 정규화하도록 확장하고 `Node` 점검용 fail-closed `ACCEPTED_INFRA_RULES` / `matchInfraException`을 추가해 `KCV0056`(`upstream`: `node-collector:0.3.1` / `trivy-checks` `CMD-0009`가 `stat -c %a /*/cni/*`로 내부 파일 `600`이 아닌 `/etc/cni/net.d` 디렉터리 `700`을 검사하며 `trivy-operator` `0.25`에 명령 오버라이드 없음; 디스크 파일 권한은 `harden-node-files.sh`·`verify-cluster.sh`로 보장)만 수용 처리했습니다. 미등록 `KCV` ID(`trivy-operator.yaml`의 `/etc/passwd`·`/etc/group` 읽기 전용 마운트로 해결하는 `KCV0059` 포함)와 `checks[]`가 누락된 리포트는 조치가능으로 유지합니다.
  - `InfraAuditRow`에 `actionableSummary`·`acceptedSummary`·`accepted`·`acceptedScope`, `ComplianceSummary`에 `acceptedInfraFailures`를 추가하고, 프레임워크 `Node` 타깃 판정에도 `infraAuditMatcher`를 재사용하며, 인프라 탭·상세 페이지·요약 카운터에서 원본 심각도 건수를 보존한 채 수용 배지와 사유를 표시합니다.

## [1.1.22] - 2026-11-19

### 변경

- **잔여 저위험 컴플라이언스 프레임워크 항목 수용 목록 반영 (`src/lib/compliance-accepted.ts`, `src/lib/i18n.ts`, `docs/common/compliance-hardening.md`):**
  - `database/Pod/narwhal-db-1`(`KSV036`에 `KSV020`, `KSV021` 추가: `postgresql:18` `/etc/passwd` UID 26 및 `0700` `pgdata` PVC 소유권; 파드 이름 정확 일치이므로 다른 CNPG 인스턴스 파드는 등록 전까지 조치 가능으로 남음), `database/ReplicaSet/narwhal-db-pooler-rw`(`upstream`, `KSV020`, `KSV021`: CloudNativePG v1.29.1이 Pooler 파드 securityContext를 UID 998/GID 996으로 덮어쓰며 지원 설정 없음), `storage/CronJob/openbao-snapshot`·`storage/ReplicaSet/openbao-snapshot-holder`(`KSV020`, `KSV021`: PVC의 기존 백업 파일이 UID 100 소유라 변경에 소유권 마이그레이션 필요), `nfs-quota-agent/DaemonSet/nfs-quota-agent`(`KSV003`, `KSV020`, `KSV021` 추가: privileged XFS/ext4 프로젝트 쿼터 실행에 필수)을 수용 목록에 추가하고 `compliance-accepted.test.ts` 및 `compliance.test.ts`(`ccr-live-trimmed.json`)를 갱신했습니다. `openbao-auto-unseal`은 예외에 넣지 않고 GitOps 매니페스트에서 `runAsUser`/`runAsGroup` `10100`으로 수정했습니다.
- **컴플라이언스 프레임워크에서 수용된 예외와 조치 가능 실패 분리 (`src/lib/compliance.ts`, `src/components/compliance/frameworks-grid.tsx`, `src/components/compliance/failure-counters.tsx`, `src/app/(dashboard)/compliance/frameworks/[id]/page.tsx`, `docs/common/compliance-hardening.md`):**
  - `ClusterComplianceReport.status.detailReport.results[].checks[]`의 모든 실패 점검에 대해 `parseComplianceTarget`(`<namespace>/<kind>-<name>`)과 `normalizeCheckId`(`AVD-KSV-00NN -> KSVNNN`)를 적용하고 기존 Config Audit(`configAuditMatcher`, `fetchReplicaSetMap()` 구 리비전/Deployment 소유권 필터), RBAC(`rbacAuditMatcher`), 인프라(조치 가능) 판정 함수를 그대로 재사용했습니다. 가능할 때는 `configauditreports` / `(cluster)rbacassessmentreports`에서 해시된 리포트 이름을 해석하고, 파싱 불가·ID 누락·미등록 항목은 fail-closed로 조치 가능에 유지합니다.
  - 원본 `failCount`와 `passRate`를 보존한 채 `actionableFailCount`, `acceptedFailCount`, `effectivePassRate`를 계산하고(`detailReport.results` 부재 시 fail-closed), 프레임워크 카드 목록·확장 패널·상세 페이지·요약 카운터에서 조치 가능 건수를 헤드라인으로 표시하고 수용 배지·접힌 예외 그룹·`docs/common/compliance-hardening.md#risk-accepted-exceptions` 링크(`ko`/`en` i18n)를 제공합니다.

## [1.1.21] - 2026-11-19

패치 릴리스: 교체된 옛 Deployment 리비전이 남긴 Trivy 리포트를 Compliance 집계에서 제외합니다(narwhal#727).

### 운영자 참고 / 동작 변경

- **플랫폼 변경이 함께 필요합니다:** 포털 ClusterRole에 `apps/replicasets` 읽기 전용 `get`/`list`가 있어야 합니다
  (같은 변경 묶음에서 `narwhal-portal-k8s.yaml`에 추가). 권한이 없으면 ReplicaSet 조회가 실패하고 모든 리포트를
  유지하며, 이는 이전 동작과 같습니다.
- 새 리비전이 있는 Deployment의, 파드가 모두 사라진 옛 리비전 리포트만 제외합니다. 0으로 축소한 Deployment,
  단독 ReplicaSet, 종료 중인 파드가 남은 리비전은 계속 집계합니다.

### 수정

- **교체된 구 Deployment 리비전 ReplicaSet의 ConfigAuditReport 제외 및 Deployment 소유권 검증 (`src/lib/compliance.ts`, `src/lib/compliance-accepted.ts`, narwhal#727):**
  - 컴플라이언스 Config Audit 목록·상세 조회 시 `configauditreports`와 같은 요청 경로에서 별도 캐시 없이 `complianceK8sFetch`(`K8S_POLICY`)로 `/apis/apps/v1/replicasets`를 조회하고, (a) `Deployment` `ownerReferences`가 있고 (b) `spec.replicas == 0` 및 `status.replicas == 0`이며 (c) 동일 네임스페이스의 같은 `Deployment`가 더 높은 `deployment.kubernetes.io/revision`을 가진 다른 `ReplicaSet`을 소유한 구 리비전의 보고서만 목록·상세·요약에서 제외하도록 수정했습니다(단독 ReplicaSet·0으로 축소한 현행 Deployment·종료 중인 리비전·메타데이터 미상은 모두 유지).
  - 라이브 결함이 가려지지 않도록 fail-safe로 동작합니다. `ReplicaSet` 목록 조회 실패(403/타임아웃 등) 또는 목록 내 대상 `ReplicaSet` 누락 시 해당 보고서를 모두 유지하고 경고 1건만 기록합니다.
  - 유지되는 `ReplicaSet`의 `metadata.ownerReferences` Deployment 이름을 `normalizeWorkloadName` / `matchConfigAuditException`에 전달해 `ownerReferences`가 동일 이름의 `Deployment`를 가리킬 때만 `SafeEncodeString` 해시 접미사를 접도록 수정했습니다(소유자가 없는 `harbor-core-<hash-like>` 단독 ReplicaSet은 수용하지 않으며, 소유권 미확인 시에는 범위를 넓히지 않고 기존 알파벳 규칙을 유지합니다).

## [1.1.20] - 2026-11-19

권한 축소가 다음 로그인을 기다리지 않고 포털에 반영되고, 열린 탭이 이전 범위로 가져온 데이터를 계속 보여 주지 않도록 하는
보안 패치 릴리스입니다(narwhal#734, narwhal#688).

### 운영자 참고 / 동작 변경

- Keycloak 그룹 변경은 다음 액세스 토큰 갱신 시 반영됩니다. 갱신이 실패하면(Keycloak 접속 불가, 세션 회수) 사용자는
  guest로 처리되고 다시 로그인하도록 안내됩니다. 토큰 수명보다 긴 Keycloak 장애 중에는 관리자를 포함한 로그인 사용자가
  Keycloak이 복구될 때까지 접근할 수 없습니다.
- 세션의 서버 측 접근 범위가 바뀌면 열린 탭이 새로고침됩니다(최근 30초 안에 이미 새로고침했다면 "접근 범위가 변경되었습니다"
  화면). 실시간 이벤트·서비스 흐름 스트림은 토큰 수명마다 최소 한 번 다시 연결됩니다.
- 별도 설정은 필요하지 않습니다.

### 수정

- **Keycloak 토큰 갱신 시 `groups`·`teams` 클레임 재도출, 전 서버 게이트 fail-closed 차단 및 SSE 수명 제한 (`src/lib/auth.ts`, `src/lib/scope.ts`, `src/app/api/events/stream/route.ts`, `src/components/providers.tsx`, narwhal#734):**
  - `config.callbacks.jwt`에서 `refreshKeycloakToken` 성공 시 새 `id_token`(없으면 `access_token`)을 `decodeJwtClaims`로 디코딩하고(`D-734-REFRESH-JWT-TRUST`, 별도 JWKS 의존성 없이 `KEYCLOAK_ISSUER` 서버 TLS 호출 신뢰 유지), `validateIssuerAudience`, `isUnexpiredExp`, `alg !== "none"` 검증 후 로그인 경로와 동일한 `applyGroupClaims`(`sanitizeGroups`, `classifyGroupClaim`, `sanitizeTeamGroups`)로 `groups`·`teams`·역할을 재계산해 최대 access token 수명 내에 Keycloak 그룹 변경이 반영되도록 수정했습니다.
  - 디코딩·`iss`/`aud`/`exp` 검증 실패, `Array.isArray(claims.groups)` 부재뿐 아니라 토큰 엔드포인트 요청 실패(HTTP 400 `invalid_grant`·네트워크 오류 등) 시에도 `failClosedRefresh`로 이전 그룹·팀을 남기지 않고 `groups: ["guest"]`, `teams: []`, `groupClaimStatus: "no_groups"`, `token.error = "RefreshTokenError"`로 fail-closed 처리했으며, `src/lib/scope.ts`의 공통 접근 게이트(`resolveEffectiveAccess`/`hasSessionAccess`/`requireEffectiveScope`/`getEffectiveScope`)가 `session.error`를 거부하고 `src/components/providers.tsx`의 `SessionErrorHandler`가 ref 가드로 `signIn`을 1회만 호출하도록 했습니다.
  - `auth.ts`에서 `session.expiresAt`을 전달하고 `/api/events/stream`이 access token 만료 시각(미지정 시 5분)에 스트림을 닫아 `EventSource` 재연결 시 최신 권한으로 재인가되도록 했으며, `src/lib/auth.test.ts`, `src/lib/scope.access.test.ts`, `src/app/api/events/stream/route.test.ts`, `src/components/providers.test.tsx`에 회귀 테스트를 추가했습니다(다중 탭 동기화는 후속 과제로 범위에서 제외).

- **서버 스코프 변경·권한 회수 시 브라우저 캐시 초기화 및 전체 문서 내비게이션 전환 (`src/lib/scope.ts`, `src/lib/scope-guard.ts`, `src/components/providers.tsx`, `src/app/api/**`, narwhal#688):**
  - 테넌트 스코프 API 라우트와 `/api/events/stream` 응답의 `X-Narwhal-Scope` 헤더에 무염 SHA-256 대신 서버 `AUTH_SECRET`으로 서명한 32자리 16진수 HMAC(`signScopeFingerprint`)을 반환해 팀·네임스페이스 이름 사전 대입을 차단했습니다.
  - React Query `queryFn`을 `Providers`의 공유 `QueryClient`에 바인딩된 `apiFetch` / `apiFetchJson`으로 통합해, `X-Narwhal-Scope` 헤더 변경 또는 `401`·`403 ACCESS_NOT_GRANTED` 응답 시 즉시 캐시를 비우고 모듈 수준 `navigating` 플래그(`ScopeChangeError`)로 동시·지연 응답의 캐시 저장을 차단한 뒤 전체 페이지 내비게이션(지문 변경 → `window.location.reload()`, `403 ACCESS_NOT_GRANTED` → `/access-denied`, `401` → `/login`)을 수행하며, replica 불일치로 인한 무한 새로고침을 막기 위해 `sessionStorage` 기반 30초 1회 제한을 뒀습니다(제한 초과 또는 `sessionStorage`를 쓸 수 없으면 새로고침 대신 `/access-denied?reason=scope-changed`로 이동: 권한이 남아 있어도 `/`로 되돌리지 않고 수동 `다시 불러오기` 링크만 보여 반복되지 않음)(생존 중인 React Query 캐시를 인메모리로 부분 제거·취소·재조회하는 이전 방식은 동시 응답 경합을 구조적으로 막지 못해 폐기했습니다).
  - 실제 `QueryClient`·`QueryObserver` 기반 회귀 테스트(`src/lib/scope-guard.test.ts`)와 `scope.access.test.ts`, `events/route.test.ts`, `events/stream/route.test.ts`, `alerts/route.test.ts`의 `viewer` 세션·HMAC 헤더 검증을 추가했습니다.
  - `auth.ts` 토큰 갱신 시 claims 재동기화, SSE 스트림(`/api/events/stream`) 연결 중 스코프 재검증, 다중 탭 간 동기화는 `narwhal#734`로 분리해 추후 처리합니다.

## [1.1.19] - 2026-11-18

구체적 근거가 있는 검토 완료 컴플라이언스 예외를 추가하고 RBAC 예외 매칭의 fail-open 경로를 닫는 패치 릴리스입니다
(narwhal#717, narwhal#722).

### 운영자 참고 / 동작 변경

- 조치할 수 없는 항목이 사유와 함께 수용됨으로 더 표시됩니다(CloudNativePG 인스턴스 파드의 토큰 마운트, privileged NFS
  쿼터 에이전트, 기본 Keycloak 이미지). 조치할 수 있는 항목은 조치 대상으로 유지됩니다.
- 직접 작성한 RBAC 예외는 해당 네임스페이스의 `Role` 또는 `ClusterRole`에만 일치하며, kind를 알 수 없는 리포트는
  수용되지 않습니다. 별도 설정은 필요하지 않습니다.

### 추가

- **Config Audit 검토 완료 예외 추가 (`src/lib/compliance-accepted.ts`, `src/lib/i18n.ts`, `docs/common/compliance-hardening.md`):**
  - `ACCEPTED_CONFIG_AUDIT_RULES`를 58개 워크로드 70건으로 갱신했습니다(`Pod` 리소스 유형 추가로 `database/Pod/narwhal-db-1` `KSV036` 정확 매칭, `nfs-quota-agent/DaemonSet/nfs-quota-agent` `KSV001`/`KSV012`/`KSV017`/`KSV023`/`KSV121` 추가 및 `KSV014`/`KSV104` 조치대상 유지, `D-COMPLIANCE-R2`에 따른 `iam/StatefulSet/keycloak` `KSV014` `upstream` 추가; 플랫폼에서 수정 가능한 `keycloak-operator`, `velero`, `node-agent`의 `KSV014`는 조치 대상으로 유지).

### 수정

- **`matchRbacException`의 리소스 kind fail-closed 검증 (`src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, narwhal#717):**
  - `ACCEPTED_RBAC_RULES` 인덱스와 `matchRbacException` 매칭에 `kind`를 포함해 `Role`(비어 있지 않은 namespace)과 `ClusterRole`(빈 namespace)만 일치시키고 `devtools/Unknown/narwhal-portal-jobs` 등 `Unknown`·누락·불일치 kind는 수용하지 않도록 수정했습니다.

## [1.1.18] - 2026-11-18

Compliance 화면에서 검토를 마친 조치 불가 항목과 조치 대상 항목을 구분하고, 인증서 갱신 요청의 결과를 표시하는 패치
릴리스입니다 (narwhal#713, narwhal#716).

### 운영자 참고 / 동작 변경

- Compliance 카운터는 조치 대상 항목을 표시합니다. 검토를 마친 예외는 목록에 그대로 남고 사유와 함께 수용됨으로
  표시됩니다. 예외 목록은 코드에 고정되어 있으며 `docs/common/compliance-hardening.md`에 문서화되어 있습니다. 목록에
  없는 항목은 조치 대상으로 유지됩니다. 별도 설정은 필요하지 않습니다.

### 추가

- **Config Audit 및 RBAC 검토 완료 예외(Risk-accepted / Upstream) 분류 (`src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/components/compliance/*`, `docs/common/compliance-hardening.md`):**
  - ReplicaSet `SafeEncodeString` pod-template-hash 정규화(`^[bcdfghjklmnpqrstvwxz2456789]{5,10}$`)와 `AVD-KSV-*`/`KSV*` ID 정규화를 적용한 워크로드·점검ID 단위 `ACCEPTED_CONFIG_AUDIT_RULES`(56개 워크로드 67건)와 역할·점검ID 정확 매칭 기반 `ACCEPTED_RBAC_RULES`(`platform-admin`, `developer-workload-admin`, `narwhal-portal-jobs`, `narwhal-portal-notification-channels` 4건)를 도입하고, `chaos-mesh`·`kubernetes-dashboard` 차트의 실제 역할 13건은 부분일치 대신 `UPSTREAM_EXACT_RBAC_ROLES` 정확 일치로 제한했습니다.
  - 미등록 점검ID·타 워크로드·`summary` 대 `checks[]` 불일치(예: `platform-admin`의 `KSV042`, summary Critical 1/High 1인데 `checks[]`에 수용된 High 1건만 있는 경우)는 fail-closed로 조치가능(`actionableSummary`)에 남기며, 비시스템 `LOW` 점검은 기존 위생(`lowSeverityConfigAuditFailures`)에만 집계해 이중 집계를 방지하고 모든 실패 항목을 API 응답에 유지합니다.
  - 컴플라이언스 요약 카운터, Config Audit/RBAC 표, 확장 패널, 상세 페이지에 수용 건수와 항목별 수용 배지 및 `ko`/`en` 다국어 사유(`compliance.acceptedReason.*`)를 표시하고, `docs/common/compliance-hardening.md` 표와의 일치 검증 테스트(`src/lib/compliance-accepted.test.ts`)를 추가했습니다.

### 수정

- **TLS 인증서 갱신 결과 피드백 및 발급 시각/리비전 표시 (`src/components/settings/certs-table.tsx`, `src/lib/k8s-client.ts`):**
  - `Certificate` 투영과 `GET /api/settings/certs` 응답에 `revision`(`status.revision`)과 `issuing`(`Issuing=True` 조건) 필드를 추가했습니다(기존 필드와 `cluster-admin` 권한 경계 유지).
  - `CertsTable`에 로케일 기준 `notBefore`와 `rev <N>`을 표시하는 `발급일` 열을 추가하고, `POST /api/settings/certs/renew` 요청에 15초 `AbortSignal` 타임아웃(언마운트 시에도 중단)을 두었으며, 갱신 POST 성공 후 행 단위 상태(리렌더에도 유지)로 쿼리를 무효화·재조회하며 30초까지 2초 간격(`발급 중...`), 이후 최대 5분까지 10초 간격(`아직 대기 중 — 잠시 후 다시 확인하세요`, 이후 수동/재포커스 재조회에도 기준선 유지)으로 일시적인 GET 실패에도 중단 없이 폴링해 `{time} 갱신됨` 또는 `갱신 실패`를 구분 표시하고 언마운트 시 중단합니다.

## [1.1.17] - 2026-11-17

팀 매핑이나 라벨 네임스페이스가 없는 계정의 포털 접근을 차단하고 사용자별 네임스페이스로 데이터를 제한하며,
클러스터 전체 신호는 클러스터 관리자로 한정하는 패치 릴리스입니다 (narwhal#659 후속, #686, #678, #687).

### 운영자 참고 / 동작 변경

- 팀 매핑도 없고 `narwhal.io/team` 라벨이 지정된 네임스페이스도 없는 계정은 게스트를 포함해 더 이상 포털에 접근할 수
  없습니다. `/access-denied`로 이동하며 데이터 API는 `403 ACCESS_NOT_GRANTED`를 반환합니다 (narwhal#659 후속, #686).
  업그레이드 전에 팀 매핑을 추가하거나 네임스페이스에 라벨을 지정하세요.
- developer와 viewer는 더 이상 클러스터 전체 데이터를 볼 수 없습니다 (narwhal#678, #687). 경보, 인시던트, 트레이스,
  서비스 그래프, 클러스터 개요는 각자의 네임스페이스로 제한됩니다. 네임스페이스가 없는(노드/클러스터) 경보,
  워크로드 분포, 실시간 플로우 스트림은 클러스터 관리자만 볼 수 있으며, 네임스페이스 리소스 속성이 없는 트레이스는
  비관리자에게 숨겨집니다.
- 새 설정은 필요하지 않으며 클러스터 관리자 동작은 변경되지 않습니다.

### 변경

- 유효 가시성 범위가 빈 비관리자 사용자(일치하는 `teamMappings` 항목과 `narwhal.io/team` 라벨 네임스페이스가 모두
  없는 경우)는 로그인 후 내비게이션이나 테넌트 데이터 조회 없이 전용 `/access-denied` 안내 화면(관리자 문의 및
  로그아웃 제공)으로 이동하며, 포털 데이터·작업 API는 HTTP `403`(`code: "ACCESS_NOT_GRANTED"`)을 반환합니다.
  세션·로그아웃·헬스 엔드포인트는 계속 접근할 수 있습니다(`D-PORTAL-UNMAPPED-DENY`, narwhal#659 후속).
- **포털 읽기/스트림 API 테넌트 스코프 및 관리자 전용 경계 강화 (narwhal#678):**
  - `GET /api/alerts`, `GET /api/hero`, `GET /api/status`, `GET /api/events`(및 `/api/my-apps`)가 공통
    `alertVisible` 규칙(`D-PORTAL-TENANT-SCOPE-678`)을 공유해 developer·viewer에게는 유효 네임스페이스에 속한
    경보만 표시하고, 네임스페이스 없는(노드/클러스터 수준) 경보는 `cluster-admin`에게만 노출합니다.
  - `GET /api/hero`는 ArgoCD 앱·경보·노드 압박 인시던트(관리자 전용)를 스코프로 걸러 건수·마스코트 상태를 재계산하며,
    `GET /api/status`는 플랫폼 핵심 구성요소 상태는 유지하되 Alertmanager `incidents`만 스코프 필터링합니다.
  - `GET /api/traces`는 `service.name` 대신 각 span 자체의 OpenTelemetry 네임스페이스 속성(`k8s.namespace.name` /
    `service.namespace`)으로 쿼리·사후 필터링하고, 비관리자 응답 행은 호출자의 유효 네임스페이스에 속한 span들만으로
    투영합니다(`serviceName`·`operationName`·`startTime`·`duration`·범위 내 `spanCount`를 가장 이른 범위 내 span에서
    산출하고 타 테넌트 루트 span 메타데이터는 노출하지 않으며 참여 트레이스의 `traceID`만 유지). 또한 캐시 읽기마다
    호출자의 현재 스코프로 재필터링·재투영하고, 네임스페이스가 없는 트레이스는 비관리자에게 차단하며, `?service=`
    조회는 소유권 스냅샷이 불완전(`snapshot.complete: false` 또는 조회 실패)하면 `403`으로 fail-closed 차단합니다.
  - 스코프 캐시 지문(`scopeFingerprint`)이 `all` 플래그, 정렬된 해석 네임스페이스, 정렬된 `argocdProjects`, 역할
    클래스(`admin`/`non-admin`)까지 포함하도록 확장했으며, 앱/프로젝트로 필터링하는 캐시 라우트(`/api/events`,
    `/api/my-apps`, `/api/governance/scorecard`)도 캐시 읽기 시 현재 스코프로 재검증·재필터링합니다.
  - `GET /api/cluster`는 비관리자에게 전체 집계(`summary`)와 유효 범위 내 `namespaces` 목록만 반환하고 노드별
    목록(`nodes: []`)·컨트롤플레인 파드 목록(`controlPlane: []`)은 비웁니다(`ClusterInfraView`에서도 해당 패널을
    숨깁니다).
  - `GET /api/service-graph` 및 `GET /api/service-graph/[svc]`는 Istio 오류율·p95 지연 집계 및 L7/L4 중복 제거를
    양쪽 `(namespace, workload)` 쌍으로 분리하고, ArgoCD 앱 매핑과 상태 fallback을 네임스페이스로 한정해 타 테넌트
    앱 이름/상태 혼입을 차단하며, 캐시 읽기 시 현재 스코프로 재필터링하고, 범위 외·모호한 이름·불완전 소유권 `[svc]`
    조회는 `403`을 반환합니다.
  - `GET /api/catalog/[name]`과 `GET /api/governance/scorecard`의 연관 경보 필터에도 공통 `alertVisible` 규칙을 적용해
    `labels.app`만 같은 타 네임스페이스 경보와 네임스페이스 없는 노드 경보를 비관리자에게 차단했습니다.
  - **네임스페이스 부재로 비관리자에게 차단되는 신호:** `k8s.namespace.name`·`service.namespace` 속성이 없는 OTel
    트레이스, 네임스페이스 라벨이 없는 Hubble 기반 서비스 그래프 엣지, 네임스페이스 없는 Alertmanager 경보는
    `cluster-admin`에게만 노출되며 `developer`·`viewer`에게는 fail-closed로 차단됩니다.
  - `GET /api/governance/distribution` 및 `GET /api/service-graph/stream`(SSE)을 `cluster-admin` 전용으로
    제한했습니다(developer·viewer는 `403`). `DistributionView`는 관리자 권한 안내를 표시하고 `ServiceMapView`는
    비관리자에게 실시간 스트림 토글을 숨깁니다.

## [1.1.16] - 2026-11-16

팀 매핑이 없는 비관리자 접근 범위를 제한하고, 상위 보안 수정이 포함된 의존성 업데이트를 반영한 패치 릴리스입니다
(narwhal#659, narwhal#673).

### Fixed

- `config/role-filter.json`의 `teamMappings`에 일치하는 팀이 없는 비관리자(`developer`, `viewer`)가
  `roleDefaults`(`["*"]`)로 폴백되어 전체 네임스페이스가 노출되고 `narwhal.io/team` 라벨 단독 격리가
  우회되던 문제를 수정했습니다. 이제 매핑된 팀이나 `narwhal.io/team` 라벨이 없으면 빈 가시성 범위(`[]`,
  fail-closed)를 반환하며, 라벨 단독 소유권 해석은 그대로 유지합니다 (`narwhal#659`).

- **운영자 참고:** 업그레이드 후 팀에 `teamMappings` 항목도 없고 `narwhal.io/team` 라벨이 지정된 네임스페이스도 없는
  developer와 viewer는 포털이 비어 보입니다. 먼저 팀 매핑을 추가하거나 네임스페이스에 라벨을 지정하세요.

### 보안

- 상위 보안 수정이 포함된 `next`와 `eslint-config-next`를 16.4.3에서 16.4.5로 업데이트했습니다. 또한
  `@opentelemetry/sdk-node`, `@opentelemetry/exporter-trace-otlp-http`를 0.301.0으로,
  `@opentelemetry/auto-instrumentations-node`를 0.83.0으로, `@opentelemetry/resources`를 3.1.0으로,
  `@opentelemetry/semantic-conventions`를 1.45.0으로, `@tanstack/react-query`를 5.107.1로,
  `lucide-react`를 1.70.0으로 업데이트했습니다. 개발 도구: `@playwright/test` 1.65.0, `tailwindcss` 4.3.5,
  `vite` 8.3.6, `@vitejs/plugin-react` 6.2.1 (narwhal#673).

## [1.1.15] - 2026-11-14

같은 이름의 Job과 실행 중인 서비스를 구분할 수 없는 경우 네임스페이스 없는 서비스 telemetry를 보존하는 패치 릴리스입니다
(narwhal#653).

### Fixed

- 다른 네임스페이스의 같은 이름 Job 때문에 실행 중인 서비스의 네임스페이스 없는 telemetry가 서비스 그래프에서 숨겨질 수
  있었습니다. 워크로드 정보가 불완전하거나 같은 이름의 실행 중인 컨트롤러 또는 파드가 있으면 이제 telemetry를 유지합니다
  (narwhal#653).

## [1.1.14] - 2026-11-11

아키텍처 페이지의 잘못된 "알 수 없음" 상태와 일회성 Job 노이즈를 고치고, 설정 사용자 목록에 실제 최근 로그인을
표시하는 패치 릴리스입니다.

### Fixed

- 아키텍처 페이지와 온보딩 플랫폼 그림에서 IDP Portal, Keycloak, ArgoCD를 포함한 대부분의 서비스가 "알 수 없음"으로
  표시되었습니다. 상태를 Deployment/StatefulSet/DaemonSet 준비 상태(다음으로 실행 중인 파드 준비 상태)에서 가져오고,
  ArgoCD 애플리케이션 상태는 폴백으로 사용합니다 (narwhal#646, narwhal#649).
- 서비스 탭이 일회성 워크로드(백업·유지보수 Job 파드)를 서비스로 나열했습니다. 이제 숨기고, 숨긴 개수를 탭에
  표시합니다 (narwhal#646, narwhal#649).
- 설정 사용자 목록에서 로그아웃하면 최근 로그인 값이 사라졌습니다. 이제 가장 최근의 Keycloak LOGIN 이벤트를
  표시하고("최근 로그인" 컬럼), 이벤트가 없으면 가장 최근 활성 세션 시작 시각으로 대체하며 세션 출처임을 표시합니다.
  realm 사용자 이벤트가 필요하며, 프로비저닝 스크립트가 30일 보존으로 활성화합니다 (narwhal#647, narwhal#648).

## [1.1.13] - 2026-11-11

아키텍처 페이지와 온보딩 플랫폼 아키텍처 그림에 대시보드 디자인을 적용하고, 설정 사용자 목록에서 권한과 마지막 로그인이
비어 보이던 문제를 고친 패치 릴리스.

### 변경

- 아키텍처 페이지: 인프라·서비스 뷰에 대시보드 카드, 미터, 토큰을 적용. 서비스는 관측된 네임스페이스별로 묶고 연결
  그래프, 필터, 실시간 오버레이, 선택 상세는 유지 (narwhal#640, narwhal#642).
- 온보딩: 플랫폼 아키텍처 그림을 아이콘·역할·상태 카드로 구성한 5개 레이어로 다시 그리고, 좁은 화면에서는 레이어
  목록으로 표시. 상태 데이터가 없으면 "알 수 없음"으로 표시 (narwhal#640, narwhal#642).

### 수정

- 설정 사용자 목록에서 모든 사용자의 권한이 `—`, 마지막 로그인이 "없음"으로 표시되던 문제. 권한은 Keycloak 그룹
  멤버십에서 가져오고, 시각 열은 가장 최근 활성 세션의 시작 시각을 표시한다(열 이름을 "최근 세션 시작"으로 변경. 렐름에
  로그인 이력이 없기 때문). 보강된 목록은 60초 동안 캐시 (narwhal#641, narwhal#643).

## [1.1.12] - 2026-11-10

Figma 앱 셸, 대시보드, 빈 상태 디자인을 적용하고 OSS 버전 스냅샷에 1.1.11 Portal pin을 기록한 패치 릴리스.

### 변경

- 상단 내비게이션을 그룹형 왼쪽 사이드바(개요, 딜리버리, 거버넌스, 시작하기)로 바꾸고 마스코트 워드마크, 활성 강조 막대,
  사용자 카드를 추가. 56px 상단 바에 경로, ⌘K 검색, 상태 표시, 언어, 테마를 배치. 1024px 미만에서는 사이드바가 드로어로
  열린다. 역할별 메뉴 노출은 그대로 (narwhal#624, narwhal#634).
- 대시보드에 아이콘·미터가 있는 KPI 카드, 구간 선택, 영역 차트, ArgoCD 상태 배지와 동기화 막대, 심각도 표시가 있는
  경보 목록을 적용. 데이터가 없는 카드는 0 기준 문구 대신 "데이터 없음"을 표시 (narwhal#624, narwhal#634).
- 공용 빈 상태·오류 상태에 마스코트, 점선 프레임, 설명, 다시 시도 버튼을 적용 (narwhal#624, narwhal#634).
- 1.1.11 GitOps pin에 맞춰 OSS 버전 스냅샷의 Portal 버전을 1.1.11로 갱신 (narwhal#623).

## [1.1.11] - 2026-11-10

Figma 로그인 디자인을 적용하고, OSS 버전 스냅샷에 1.1.10 Portal pin을 기록한 패치 릴리스.

### 변경

- `/login`을 Figma 분할 레이아웃으로 개편: 브랜드 패널(마스코트, 헤드라인, 태그라인, 물결 장식)과 리다이렉트
  스피너 또는 mock 역할 선택을 담는 왼쪽 정렬 400px 열. 900px 이하에서는 작은 워드마크만 표시 (narwhal#619, narwhal#620).
- 1.1.10 GitOps pin에 맞춰 OSS 버전 스냅샷의 Portal 버전을 1.1.10으로 갱신 (narwhal#618).

## [1.1.10] - 2026-11-10

경보 및 알림 채널 설정을 추가하고, Trivy 재조회 후에도 부분 결과를 유지하며, OSS 버전 스냅샷에 1.1.9 Portal pin을 기록한 패치 릴리스.

### 추가

- 심각도 필터·억제 상태·연결 저하 안내를 제공하는 경보 화면과 메뉴 추가 (narwhal#610).
- cluster-admin 전용 Slack/Mattermost·SMTP 알림 설정, 비밀값 교체·삭제, 채널 활성화 및 5분 테스트 경보 추가. GitOps 소유 리소스만 갱신하며 저장된 값은 반환하지 않음 (narwhal#610).

### 수정

- Trivy 데이터를 재조회한 뒤에도 부분 결과 안내 표시를 유지 (narwhal#615).

### 변경

- 1.1.9 GitOps pin에 맞춰 OSS 버전 스냅샷의 Portal 버전을 1.1.9로 갱신 (narwhal#608).

## [1.1.9] - 2026-11-09

패치 릴리스. 의존성 업데이트와 OSS 버전 목록 항목 1개 추가 외에 사용자에게 보이는 기능 변경은 없음.
`lucide-react`를 1.66.0으로 올렸고, OSS 버전 페이지에 Trivy DB 미러 갱신 작업이 쓰는 `skopeo`
(trivy-db-refresh) 이미지가 추가됨(#603). `portal/v1.1.8` 이후 `portal/`의 그 밖의 변경은 1.1.8 pin에서 다시
생성한 OSS 스냅샷(#596), `lucide-react` 업데이트에 맞춰 다시 생성한 서드파티 고지(#539), worker VM 메모리 8GB를
반영한 `portal/docs/local-dev.md` 문구 수정(#597)임.

### 변경

- `lucide-react`를 1.58.0에서 1.66.0으로 올림 (narwhal#539).
- OSS 버전 스냅샷(`src/data/oss-versions.generated.json`)의 Registry 섹션에 `skopeo (trivy-db-refresh)` 1.22.2
  항목이 추가됨 (narwhal#603).
- 1.1.8 pin에 맞춰 OSS 버전 스냅샷을 다시 생성함: IDP Portal 1.1.7 -> 1.1.8, Next.js 16.4.2 -> 16.4.3
  (narwhal#596).
- `lucide-react` 1.66.0에 맞춰 `THIRD-PARTY-NOTICES.md`를 다시 생성함 (narwhal#539).

## [1.1.8] - 2026-11-06

패치 릴리스. Argo CD가 phase를 기록하지 않은 DORA 최근 배포 행을 "Unknown" 뱃지 대신 대시로 표시하고,
`next`를 16.4.3으로 올림. `portal/v1.1.7` 이후 `portal/`의 그 밖의 변경은 1.1.7 핀(#580)에 맞춰 다시 생성한
OSS 버전 스냅샷과, 이제 gitops에 있는 네임스페이스 단위 인증서 갱신 RBAC를 설명하도록 고친
`portal/src/lib/k8s-client.ts`의 주석(#592)뿐임.

### 수정

- **거버넌스: DORA 이력 행이 "Unknown"으로 표시됨 (narwhal#581)**: Argo CD는 최근 작업의 결과만
  `status.operationState.phase`에 기록하고 이전 `status.history` 항목에는 상태 필드가 없어, 최근 배포 표가
  실제 상태처럼 보이는 "Unknown" 뱃지를 표시했음. 이제 해당 행은 Argo CD 필드 제약을 설명하는 툴팁(KO/EN)과
  함께 "—"로 표시하며, Succeeded와 Failed는 기존 뱃지를 유지함.

### 변경

- `next`를 16.4.2에서 16.4.3으로 올림 (next-react 그룹, narwhal#486).

## [1.1.7] - 2026-11-05

패치 릴리스. DORA 리드타임에 분 단위 정밀도를 표시하고, Trivy DB 상태 카드에 미러링된 모든 보안
데이터 소스를 나열하며 trivy-checks를 고정(pinned) 상태로 표시함. Pretendard 폰트의 OFL 라이선스를
포털 컨테이너 이미지에 포함하고, `@opentelemetry/semantic-conventions`를 1.44.0으로 올림.
`portal/v1.1.6` 이후 `portal/`의 그 밖의 변경은 1.1.6 핀(#561)에 맞춰 다시 생성한 OSS 버전 스냅샷뿐임.

### 추가

- **보안: Trivy DB 카드에 미러링된 모든 데이터 소스 표시 (narwhal#570)**: 상태 API, 카드 및 복사 가능한
  에어갭 갱신 명령이 소비자, 태그, 소스별 SLO가 정의된 단일 소스 목록(`portal/src/lib/trivy-db-sources.ts`)을
  기반으로 동작함. 사용 기한 SLO가 없는 소스(업스트림에서 2025-10-09에 동결된 `trivy-checks:1` 등)는
  "오래됨(stale)" 대신 "고정(pinned)"으로 표시함.

### 수정

- **거버넌스: DORA 리드타임이 0시간으로 반올림됨 (narwhal#566)**: 1.1.6에서 Git 기반 앱의 리드타임이 0.1시간
  단위로 반올림되어 항상 "0시간"으로 표시되었음. API가 하위 호환성을 유지하는 `leadTimeHours` 외에
  `leadTimeMinutes`를 함께 반환하고, 요약 카드와 앱별 표에서 분 단위 정밀도(`<1분`, `N분`, `N시간 M분`)로
  표시하도록 포맷터를 적용함. 실제 0분은 null(Helm 기반 앱의 "N/A")과 구분하여 표시함.

### 변경

- **패키징: Pretendard OFL 라이선스를 포털 이미지에 포함 (narwhal#572)**: SIL Open Font License 1.1 재배포
  조건을 충족하기 위해 컨테이너 이미지가 번들된 Pretendard 폰트 파일과 함께 `src/app/fonts/OFL.txt`를
  `/app/OFL-Pretendard.txt`로 복사하며, 폰트 출처를 `NOTICE`에 기록함.
- `@opentelemetry/semantic-conventions`를 1.43.0에서 1.44.0으로 올림 (narwhal#538).

## [1.1.6] - 2026-11-05

기능이 더해진 패치 릴리스. 설정·보안 화면에 OSS 업데이트 탭, Trivy DB 카드, Kyverno 정책 설명이
추가되고, 1.1.5에는 없던 여러 수정이 클러스터에 반영됨. `portal/v1.1.5` 이후의 모든 변경을 다룸.

### 추가

- **설정: OSS 업데이트 탭 (narwhal#552)**: 구성요소별 선언 버전(`VERSIONS.md`를 커밋한 스냅샷), 실행
  버전(Argo CD `targetRevision`과 Trivy가 이미 보고하는 이미지 태그·CVE), 업스트림 최신 버전을 보여
  줌. 게시 7일 미만 버전은 쿨링 중으로 표시하고 요청할 수 없음(409). 요청 버튼은
  `narwhal-version-upgrade` 체크리스트가 담긴 업그레이드 이슈를 만들며, 포털은 클러스터를 직접
  바꾸지 않음. 업스트림 조회는 공개 GitHub 릴리스이며 `OSS_UPDATES_UPSTREAM=true`일 때만 켜지고 6시간
  (실패 시 10분) 캐시하며, 꺼져 있거나 실패하면 "알 수 없음"으로 표시함.
- **보안: Trivy DB 상태 카드 (narwhal#554)**: cluster-admin 전용 읽기 화면. Harbor `trivy-db`
  프로젝트의 trivy-db, trivy-java-db, trivy-checks에 대해 태그, 경과 시간, 7일 SLO 기준
  최신/오래됨/없음 상태를 보여주고, 스캐너 상태(리포트 수, 최신 리포트 시각, 최근
  `BackoffLimitExceeded` 이벤트)도 보여줌. 오래됐거나 없으면 복사 가능한 에어갭 갱신 명령을 안내함.
  Harbor는 익명으로 읽으며 선택 환경변수 `HARBOR_URL`의 기본값은
  `http://harbor-core.devtools.svc.cluster.local`임. `GET /api/security/trivy-db`가 이를 제공함.
- **설정: Kyverno 정책 설명과 상세 패널 (narwhal#547)**: 범례가 Enforce(admission 시 차단)와
  Audit(허용하되 PolicyReport에 기록)를 설명함. 동작은 이제 규칙별 `validate.failureAction`
  (Kyverno 1.13+)에서 가져오며 규칙끼리 다르면 `Mixed`로 표시함. 정책 이름을 누르면 설명, 분류, 심각도,
  적용 범위, 동작, 백그라운드 설정, 규칙별 상세가 담긴 패널이 열림. 정책 API에는 하위 호환 필드가
  추가됨.
- **로컬 개발 클러스터**: `make dev-cluster` / `pnpm dev:cluster`가 로컬 클러스터에서 실시간 편집
  포털 빌드를 차트로부터 생성해 실행함(narwhal#529). 개발 도구에 한정됨.

### 수정

- **비용: 서비스 분해가 비어 있음 (narwhal#559)**: `kube_pod_labels`의 `app.kubernetes.io/instance`로만
  묶었는데 kube-state-metrics가 그 지표를 내보내지 않아 모든 워크로드가 제외됐다. 이제 instance, name, app,
  k8s-app, 소유 워크로드, 파드 이름 순으로 귀속하고(대체 기준 행은 추정으로 표시), 남는 것은 보이는
  "(미분류)" 행에 모으며 행 합계가 클러스터 총액과 같다. 같은 PR의 kube-state-metrics 라벨 allowlist가 필요하다.
- **인증서: 갱신이 항상 실패함 (narwhal#549)**: 포털이 `spec.renewBefore: "1s"`를 패치했으나
  cert-manager 웹훅이 5분 미만을 거부했고, 포털 ServiceAccount는 인증서 `get`만 가능했음. 이제
  `cmctl renew`와 같이 `/status` 서브리소스로 `Issuing` 조건을 `True`(`ManuallyTriggered`)로
  설정하고 409면 한 번 재시도하며, spec은 쓰지 않음. `narwhal-portal` ClusterRole에는
  `certificates/status`의 `update`만 추가되며, 이는 GitOps 차트 변경이 반영되어야 함. 오류는
  상태와 경로만 로그에 남기고, Kubernetes 403/404/409/422는 일괄 500 대신 각각의 응답으로 매핑됨.
- **거버넌스: DORA 리드타임이 항상 "—"로 표시됨 (narwhal#556)**: 게이트웨이를 통한 Gitea API 호출이
  Keycloak 로그인으로 리다이렉트되어 커밋 시각을 읽지 못했음. 이제 좁은 APISIX 규칙
  `gitea-portal-api`가 `narwhal-gitops`의 커밋·contents·pulls 경로에 대한 GET/POST 중
  `token <40자리 hex>` Authorization 헤더가 있는 요청만 Gitea로 통과시킴. 같은 리다이렉트로 네임스페이스
  요청 PR 흐름도 깨져 있었음. Helm 차트 버전을 소스로 쓰는 앱은 "—" 대신 "N/A (Helm chart)"로 표시함.
  리다이렉트나 HTML 응답은 SSO 리다이렉트 가능성으로 한 번 로그에 남기며, 안내 문구는
  `gitea-portal-api` 규칙과 `GITEA_URL`을 확인하라고 안내하도록 고침(narwhal#557).
- **보안: Trivy 결과 일부만 반환 (narwhal#505, narwhal#514)**: 조회 중 429나 만료된 continue 토큰이
  리포트 조회를 조용히 끝내지 않음. 페이지당 최대 3회 재시도하며 `Retry-After`를 따르고, 이후에도
  실패하면 화면에 부분 데이터 안내를 띄우고 API는 `X-Partial-Data`를 설정하며 부분 결과는 캐시하지
  않음.
- **설정: 권한 표 고정 열 비침 (narwhal#546)**: 표를 가로로 스크롤할 때 그룹 이름, 헤더, 저장 열이
  불투명하게 유지됨.
- **보안: Trivy DB 표시 경로 (narwhal#550)**: 포털이 보여주는 Trivy DB 레지스트리가 존재하지 않는
  `/library/trivy-db` 대신 `trivy-operator.yaml`과 일치함.
- **한글 하드코딩 (narwhal#512, narwhal#513, narwhal#527)**: DORA 지표, 리소스 상세, KISA 점검표의
  번역되지 않은 UI 문자열 10개가 `ko`/`en` 메시지 키를 쓰도록 바뀜.

### 변경

- OpenTelemetry를 SDK 3.0으로 올림: `auto-instrumentations-node` 0.82.0, `sdk-node`와
  `exporter-trace-otlp-http` 0.300.0, `@opentelemetry/resources` 3.0.0(narwhal#487). `shadcn` 4.23.0
  (narwhal#540), 개발 도구 `@tanstack/react-query-devtools` 5.105.0, `eslint-config-next` 16.4.3,
  `vite` 8.3.5(narwhal#517). 쓰이지 않는 `hono`, `@hono/node-server` override를 제거함(narwhal#543).

## [1.1.5] - 2026-10-31

패치 릴리스. 쿨링 기간 때문에 보류했던 `body-parser` 권고(narwhal#470)를 해결하고,
narwhal#503에서 병합한 Portal 리더 수정을 배포함. `portal/v1.1.4` 이후의 모든 변경을 다룸.

### 수정

- **런타임 이벤트: 우선순위 필터가 아무것도 매칭하지 못함 (narwhal#503)**: falcosidekick은 Loki
  `priority` 라벨을 대문자로 시작하는 이름(`Critical`)으로 쓰는데, 리더는 소문자 값으로 조회해서
  우선순위 필터와 critical 알림 조회가 어떤 스트림도 찾지 못했음. 이제 두 표기를 모두 받고, Falco가
  Kubernetes 밖 프로세스에 내보내는 JSON `null` 필드는 없는 값으로 취급함.
- **Ubuntu 26.04에서 런타임 이벤트 탭이 숨겨짐 (narwhal#503)**: 노드 하나라도 26.04이면 탭 대신
  "Ubuntu 26.04에서 사용할 수 없음" 안내가 나왔음. Falco 0.45.1이 모든 프로바이더에서 활성화되므로
  탭을 항상 보여주고, 노드 OS 판별과 `security.runtimeEvents.unavailable2604` 문구를 제거함. KISA 통제
  메모는 더 이상 Falco가 비활성이라고 하지 않으며, 라이브 클러스터에 대한 주장은 하지 않음.

### 보안

- `body-parser`를 2.3.0에서 2.4.0으로 올려 GHSA-27r3-jxmh-p828을 해결함(narwhal#470,
  narwhal#505). Express 5.3.0의 기존 `^2.3.0` 범위가 허용하므로 override는 추가하지 않음.
  필수 의존성 `qs`도 `^6.16.1` 요구를 만족하도록 6.16.0에서 6.16.1로 올림. 두 버전 모두
  7일 lockfile cooling gate를 통과함.
- `systeminformation`을 5.33.15에서 5.33.16으로 올려 GHSA-3f7j-7jwc-fxfj(high,
  `@opentelemetry/instrumentation-host-metrics` 경유, 부모 범위 `^5.31.6`이 허용)를 해결함.
  5.33.16은 2026-10-19 게시로 cooling gate를 통과했고, 그 뒤의 5.33.17~5.33.19는 아직 통과하지
  못해 받지 않음. 다른 패키지 버전은 바뀌지 않음.

## [1.1.4] - 2026-10-28

패치 릴리스. 스캔한 이미지가 많은 클러스터에서도 보안 화면에 취약점 데이터가 다시 나오고,
`fast-uri`를 공개된 권고 이후 버전으로 올림(narwhal#470). `portal/v1.1.3` 이후의 모든 변경을 다룸.

### 수정

- **보안 화면에 취약점이 하나도 안 보임 (narwhal#501)**: Trivy 리더가 VulnerabilityReport 전체를
  `limit` 없이 한 번에 조회했음. Kakao에서는 리포트 109개가 10,761,256바이트로 클라이언트 응답 상한
  8 MiB를 넘었고, 화면은 빈 결과로 대체됨. 이제 요약·워크로드 표·이미지 화면 모두 한 번에 20개씩
  (최대 50페이지) 나눠 받고, 목록이 잘리면 경고를 남기고 받은 만큼은 표시함.

### 보안

- `ajv` 8.20.0 아래의 전이 의존성 `fast-uri`를 4.2.2에서 4.2.3으로 올림(GHSA-w74x-v6f9-j3xg,
  GHSA-wjr9-j795-jxcr, GHSA-xhq7-xmqj-769h, GHSA-333x-w4r6-6crp) (narwhal#492). 기존 `>=3.1.2`
  override 범위 안이라 lockfile의 다른 패키지는 바뀌지 않았고, `pnpm audit --prod`에서 `fast-uri`
  권고가 사라짐.

### 알려진 문제

- `body-parser`(GHSA-27r3-jxmh-p828, 2.4.0에서 수정)는 아직 올리지 않음. 수정판이 2026-10-29
  11:39Z 이후에 7일 lockfile cooling gate를 지남. 다음 패치에서 반영 (narwhal#470).

## [1.1.3] - 2026-10-27

패치 릴리스. 권한 관리 표가 각 역할이 실제로 접근하는 도구를 보여 줌. `portal/v1.1.2` 이후의 모든
변경을 다룸.

### 수정

- **설정 > 권한 관리 표가 비어 보임 (narwhal#479)**: 표가 그룹의 `allowed_tools` 속성만 보여
  줬는데, `/api/tools`는 `tools.ts`의 역할 기본값에 그 속성을 더해 권한을 줌. 속성이 있는 그룹이
  없는 realm에서는 모든 칸이 흐린 "꺼짐" 아이콘이었음. 이제 역할 기본값은 체크·잠금 상태로
  표시되고(여기서 끌 수 없음), 속성으로 더한 도구만 편집할 수 있음.

### 알려진 문제

- `fast-uri`와 `body-parser`는 아직 올리지 않음 (narwhal#470). `fast-uri` 수정판은 2026-10-26
  22:51Z에 7일 lockfile cooling gate를 지났지만 일부러 뺐음. 1.1.3은 권한 관리 수정만 담고, lockfile
  업그레이드는 따로 리뷰받는 변경으로 진행함. `body-parser`(GHSA-27r3-jxmh-p828)는 2026-10-29
  11:39Z 이후 gate를 지남. 둘 다 다음 패치에서 반영.

## [1.1.2] - 2026-10-27

패치 릴리스. 프로덕션 클러스터에서 클러스터 인프라 화면이 비던 문제를 고치고, `zod`와 `undici`를
공개된 권고 이후 버전으로 올림(narwhal#470). `portal/v1.1.1` 이후의 모든 변경을 다룸.

### 수정

- **아키텍처 > 클러스터 인프라가 노드·Pod·네임스페이스 0으로 표시됨 (narwhal#475)**:
  `/api/cluster`와 `/api/domain/clusters`가 in-cluster 토큰을 `K8S_SA_TOKEN` 환경변수에서만
  읽었는데, 프로덕션은 이 변수 없이 projected token 파일만 마운트함. 그래서 빈 bearer로 API
  서버를 호출해 `system:anonymous` 403을 받았고, 실패가 빈 목록으로 표시됨. 이제 in-cluster
  `K8S_API_SERVER` + `K8S_SA_TOKEN` 쌍은 다른 Kubernetes 라우트와 같이 projected token을 읽음.
  다른 조합은 계속 자기 환경변수만 읽으므로 이 파드의 토큰이 다른 API 서버로 가지 않음.

### 보안

- `shadcn`을 ^4.22.0으로 올려 `zod` 4.6.7(GHSA-mrhx-c7xc-m3w5)과 `undici` 7.30.1
  (GHSA-xjj6-f33q-929c, GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472) 반영 (narwhal#471).

### 변경

- `@opentelemetry/auto-instrumentations-node` 0.81.0 → 0.81.2 (narwhal#469).

### 알려진 문제

- `body-parser`(GHSA-27r3-jxmh-p828, 2.4.0에서 수정)와 `fast-uri`(GHSA-w74x-v6f9-j3xg,
  GHSA-wjr9-j795-jxcr, GHSA-xhq7-xmqj-769h, GHSA-333x-w4r6-6crp, 4.2.3에서 수정)는 아직 올리지
  않음. 수정판이 7일 lockfile cooling gate를 지나지 않았기 때문임(fast-uri는 2026-10-26 22:51Z,
  body-parser는 2026-10-29 11:39Z 이후). 다음 패치에서 반영 (narwhal#470).

## [1.1.1] - 2026-10-23

보안 패치 릴리스. Auth.js와 일부 전이 의존성을 공개된 권고 이후 버전으로 올리고(narwhal#459)
Next.js 16.4와 React 19.3.1 업데이트를 반영함. `portal/v1.1.0` 이후의 모든 변경을 다룸.

### 업그레이드 노트

- **롤아웃 중 진행 중이던 OAuth 로그인은 한 번 실패하므로 다시 시도해야 함 (narwhal#461)**:
  `@auth/core` 0.41.3은 state, nonce, PKCE 검사 쿠키를 이를 만든 provider에 묶고, 다른 provider용으로
  발급된 쿠키의 콜백은 거부함. 업그레이드 이전에 만든 쿠키에는 이 바인딩이 없으므로 이전 파드에서
  시작해 새 파드에서 끝나는 로그인은 실패하며, 다시 로그인하면 정상 동작함. 기존 세션은 영향을 받지 않음.

### 보안

- **치명적(critical) 권고를 해소하도록 Auth.js 업그레이드 (narwhal#461, narwhal#459 해결)**:
  `next-auth` 5.0.0-beta.30에서 5.0.0-beta.32로, `@auth/core` ^0.34.3에서 ^0.41.3으로
  (GHSA-8fpg-xm3f-6cx3, GHSA-7rqj-j65f-68wh, GHSA-xmf8-cvqr-rfgj). 새 회귀 테스트
  `src/lib/auth.fail-closed.test.ts`가 실제 next-auth를 실행하며, `AUTH_SECRET`이 없을 때 beta.30은
  `null` 대신 오류 객체를 반환해(`if (!session)` 게이트가 걸리지 않음) `/api/alerts`가 200을
  응답했고, beta.32는 `null`을 반환해 해당 라우트가 401을 응답함.
- **lockfile의 전이 의존성 갱신 (narwhal#461)**: `form-data` 4.0.6(GHSA-hmw2-7cc7-3qxx),
  `ws` 8.22.0(GHSA-96hv-2xvq-fx4p), `protobufjs` 8.8.1(GHSA-wcpc-wj8m-hjx6).
- **Next.js 16.4.2, React 및 React DOM 19.3.1 (narwhal#457)**: Next.js 16.4.1 보안 수정을 반영함.
  이 앱은 이미지 최적화기를 사용하지 않도록 비활성화되어 있음.

### 변경

- `lucide-react` 1.7.0에서 1.58.0으로(narwhal#463). `CheckCircle2` 아이콘의 체크 모양이 새로 그려짐.
- `shadcn` 4.19.0에서 4.21.4로(narwhal#353), `@base-ui/react` 1.8.0에서 1.9.0으로(narwhal#354).
  둘 다 런타임 `dependencies`임.
- 개발 도구만 해당하며 이미지에 포함되지 않음(narwhal#454): Playwright, Vitest, Vite,
  `@vitejs/plugin-react`, `@types/node`, `eslint-config-next`, TanStack Query devtools.
- `SECURITY.md`와 `SECURITY-ko.md`가 지원 대상 버전을 1.1.x로 표기함(narwhal#451).

### 알려진 문제

- `braces`(GHSA-vfj7-8cjw-p6xm)는 패치된 버전이 없음. shadcn CLI 경로(`shadcn > fast-glob > micromatch`)로만
  도달하며 그대로 둠(`docs/lessons-log.md` 참고).

## [1.1.0] - 2026-10-15

포털은 이제 `dasomel/narwhal` 모노레포의 `portal/` 아래에 있으며 릴리스 태그는 `portal/vX.Y.Z`임
(모노레포에서 만든 첫 릴리스이며, 1.0.17 이하 버전은 기존 `dasomel/narwhal-portal` 링크를 유지함).

프로덕션 보안 기준을 강화하고(인증된 Valkey TLS, 명시적 OpenBao 인증 모드, 호출 시점 의존성 설정 검사,
네임스페이스 범위 읽기) provider 장애가 조용히 정상으로 보이지 않도록 수정함. 쓰기 라우트는 이제
거절·자격 증명·결과 미상을 구분하며, ArgoCD나 Alertmanager를 읽을 수 없을 때 플랫폼 상태가 더 이상
정상으로 표시되지 않음. 1.0.18과 1.0.19 태그는 푸시되었으나 cosign 서명 단계 실패로 GitHub Release로
게시되지 않았으므로, 이 섹션은 1.0.17 이후의 모든 변경을 다룸.

### 업그레이드 노트
- **프로덕션은 이제 인증된 Valkey TLS를 요구함 (#205, 63603d6, 1.0.19 이후)**: 프로덕션에서
  `getValkey()` / `getLiveValkey()`(`src/lib/valkey.ts`)는 `VALKEY_URL=rediss://…`(TLS 스킴),
  `VALKEY_TLS=true`, `VALKEY_USERNAME`(전용 ACL 사용자, 문서에서는 `portal`), `VALKEY_PASSWORD`가
  모두 설정되지 않으면 예외를 던짐. `VALKEY_INSECURE_PRODUCTION`은 더 이상 지원되지 않으며, 값이
  `false`여도 정의만 되어 있으면 클라이언트 초기화를 거부함. `docs/security-clean-install.md` 6절 참고.
  실패는 지연 발생(프로세스 시작 시가 아니라 첫 Valkey 사용 시)임. `VALKEY_URL` 또는
  `VALKEY_PASSWORD`가 설정된 경우 `/api/health/ready`가 503(`Critical cache dependency unavailable`)을
  반환하므로 롤아웃은 Ready가 되지 못함(`AUTH_MOCK=true`가 아닌 경우). 두 변수가 모두 없으면 프로브가
  Valkey를 건너뛰고 200을 반환하며 캐시는 조용히 비활성화됨: 캐시 헬퍼가 보안 설정 오류를 잡음(`cacheGet`과
  `cacheGetWithMeta`는 null을 반환하고, `cacheSet`과 `cacheDel`은 아무 동작도 하지 않음).
  `getValkey()` / `getLiveValkey()`를 직접 호출하는 코드는 예외를 그대로 받으며, 라이브 이벤트
  영속화와 멱등성 claim은 이를 잡아 프로세스 로컬 인메모리 경로로 저하됨: 멱등성 claim이 레플리카 간에
  공유되지 않고(레플리카 사이의 중복 수집이 더 이상 걸러지지 않음), 라이브 이벤트는 영속화되지도 다른
  레플리카로 게시되지도 않으며, 둘 다 재시작 시 사라짐. **작성 시점의 narwhal GitOps 차트는 여전히
  `VALKEY_INSECURE_PRODUCTION=true`와 평문 `redis://` URL을 TLS·ACL이 없는 Valkey에 대해 주입하므로,
  narwhal이 Valkey TLS + ACL과 대응하는 바인딩을 제공하기 전까지 이 포털 버전은 해당 차트와 함께
  배포할 수 없음.** 1.0.19까지의 이미지는 이 변경 이전임.
- **프로덕션에서 OpenBao 정적 토큰 모드는 명시해야 함 (#237, 5319ada)**: 프로덕션에서
  `OPENBAO_TOKEN`을 자격 증명으로 사용하는 경우 `OPENBAO_AUTH_METHOD`는 정확히 `token`이어야 하며,
  그렇지 않으면 첫 OpenBao 요청이 `Static credentials require explicit OPENBAO_AUTH_METHOD=token in
  production` 예외를 던짐. 검사는 지연 방식(시작 시가 아니라 첫 OpenBao 토큰 요청 시)이며 인증 방식이
  `token`으로 결정될 때만 도달함. 즉 `OPENBAO_AUTH_METHOD`가 미설정이고 Kubernetes 서비스 어카운트 JWT가
  마운트되지 않은 경우임. Kubernetes 인증(`OPENBAO_AUTH_METHOD=kubernetes`, 또는 변수 미설정 + JWT
  마운트)을 쓰는 배포는 영향 없음. 프로덕션에서 비어 있지 않은 그 밖의 `OPENBAO_AUTH_METHOD` 값(예:
  오타)은 이제 `OPENBAO_AUTH_METHOD must be kubernetes or token` 예외를 던짐.
- **OpenBao 주소는 모든 요청에서 HTTPS여야 함 (#236, 513a71a)**: `OPENBAO_ADDR`는 프로세스당 한 번이
  아니라 매 외부 요청과 Kubernetes 로그인 전에 검증되며, 프로덕션에서 HTTPS가 아닌 주소는 매번 거부됨.
- **의존성 URL은 프로덕션에서 더 이상 localhost로 대체되지 않음 (1.0.17 이후)**: `ARGOCD_URL`,
  `ALERTMANAGER_URL`, `PROMETHEUS_URL`, `TEMPO_URL`, `LOKI_URL`, `APISIX_ADMIN_URL`, `OPENBAO_ADDR`,
  `GITEA_URL`, `KEYCLOAK_INTERNAL_URL`은 더 이상 localhost나 클러스터 내부 기본값으로 대체되지
  않음(752273f, 451bec8). `getDependencyUrl()`(`src/lib/config.ts`)은 첫 사용 시
  `Missing required production configuration: <NAME>` 예외를 던지며(시작 시가 아님),
  `/api/health/ready`는 누락된 값을 표시하지 않음(`validateRuntimeConfig`는 `missingOptional`로만
  나열하며, https가 아닌 `GITEA_URL`은 표시함).
  `SLO_CHAIN_START`도 이제 프로덕션의 federated logout에 필수이며 없으면 예외를 던짐(451bec8).
  `KEYCLOAK_ISSUER`와 `GITEA_URL`은 `https://`여야 하며(#110), `APISIX_API_KEY_READONLY`는 더 이상
  관리자용 `APISIX_API_KEY`로 대체되지 않음(#112).
- **Kubernetes 토큰 출처 (1.0.17 이후)**: 포털은 `K8S_SA_TOKEN` 대신 projected 단기 서비스 어카운트
  토큰을 읽음(경로 변경은 `K8S_SA_TOKEN_FILE`). `K8S_SA_TOKEN`은 개발 전용임. `K8S_TOKEN_AUDIENCE`를
  설정하면 클라이언트 측 `aud` 검사가 활성화되며, 프로덕션에서 미설정이면 경고를 기록함(eeb5ef6,
  1a27584, 1e35aac).
- **`/api/events/ingest`가 기존 이벤트 생산자에게 더 엄격해짐 (3344e78)**: `idempotency_key`와
  `source_event_id`가 모두 없는 요청은 400을 반환하고(v1.0.17 라우트에는 이 요구사항이 없었음),
  `X-Producer-Id`(또는 `X-Ingest-Producer`) 헤더가 본문 `source`와 다르면 403을 반환하며, 64 KiB
  (65,536바이트, `MAX_INGEST_BODY_SIZE_BYTES`)를 넘는 본문은 413을 반환함.
- **라이브 이벤트 스트림은 기본 거부임 (3c4c36a)**: 네임스페이스가 없는 이벤트는 `visibility: "cluster"`를
  선언한 경우에만 관리자가 아닌 사용자에게 표시되며, 그 외에는 숨겨짐. 제목/설명의 네임스페이스
  정규식 대체 경로는 제거됨. 생산자는 `resource.namespace` 또는 `visibility: "cluster"`를 설정해야 함. 뷰어와 게스트는
  여전히 `node` 이벤트와 설명에 "secret"이 포함된 이벤트도 볼 수 없음.
- **`/api/secrets`는 실패를 드러내고 키 이름을 반환하지 않음 (0c715f5)**: 메타데이터 읽기 실패 시 `[]`
  대신 502 `{error, degraded: true}`를 반환하며, 응답에 시크릿 키 이름이 더 이상 포함되지 않음.
  인벤토리는 이제 KV v2 `secret/metadata/<path>`만 읽고 `secret/data/<path>`는 읽지 않으므로, 이
  라우트에는 포털의 OpenBao 정책에 메타데이터 목록 조회와 읽기가 필요하고 데이터 읽기는 필요하지 않음.
- **`/api/cluster`는 guest를 거부함 (de1edc7)**: 이제 `cluster-admin`, `developer`, `viewer`가
  필요하며 그 밖의 역할에는 403을 반환함. 이전에는 세션만 있으면 됐음.
- **프로덕션에서 kubeconfig 다운로드에는 `K8S_API_SERVER`가 필요함 (506c279)**: 다운로드되는
  kubeconfig는 `getExternalK8sApiServer()`를 사용하며, 프로덕션에서 `K8S_API_SERVER`가 없으면 예외를
  던짐. `KUBERNETES_SERVICE_HOST`만으로는 부족함. 이는 외부에서 도달할 수 없는 클러스터 내부 주소이기
  때문임.
- **네임스페이스 가시성은 `narwhal.io/team` 라벨을 따름 (b094fb7)**: 소유권의 기준은 라벨이며,
  `config/role-filter.json`의 네임스페이스 패턴은 라벨이 없는 네임스페이스를 위한 마이그레이션 경로로
  계속 해석됨. `POST /api/namespaces`는 더 이상 네임스페이스를 직접 만들지 않고, GitOps 저장소에 테넌트
  매니페스트를 커밋하여 풀 리퀘스트를 엶(058426e).
- **API 소비자**: `/api/governance/audit`는 새 `/api/governance/events`의 deprecated 별칭임(#161).
  `/api/health/status`는 이제 `cluster-admin`이 필요하며(a286598), federated logout은 POST 전용임
  (ab69c4f). 카탈로그 롤백, silence 생성/삭제, sync 라우트는 새 상태 코드와 고정 오류 문구를 반환함
  (변경 항목 참고).
- **릴리스 파이프라인**: 태그 빌드는 cosign으로 이미지를 서명하며 `COSIGN_PRIVATE_KEY` /
  `COSIGN_PASSWORD` 저장소 시크릿이 없으면 실패함(3026d23, `docs/cosign-signing-setup.md`).
  GitHub Release는 게시가 성공한 뒤에만 생성됨(#120).

### 추가
- **모든 사용자를 위한 의존성 상태 집계**: `GET /api/health/summary`는 모든 인증된 역할에 `{state,
  observedAt}`만 반환하고 내비게이션 표시기를 구동함(#155). 관리자 전용 `/api/health/dependencies`는
  informer 소유자 상태와 라이브 파이프라인 카운터를 포함한 통합 의존성 계약을 제공함(#148, #158, #162).
- **`/api/governance/events`**: `{items, truncated, evidenceKind, freshness}`를 반환함. 기존
  audit 이름의 라우트는 배열 응답을 유지하면서 `X-Truncated`, `Deprecation`, `Link` 헤더를 추가함(#161).
- **풀 리퀘스트 기반 네임스페이스 요청**: `narwhal.io/team` 라벨이 붙은 Namespace, RoleBinding,
  ResourceQuota를 담은 GitOps PR로 네임스페이스를 요청함(058426e).
- **정규 이벤트 envelope와 라이브 스트림 복원력**: producer별 자격 증명, 속도 제한(429 +
  `Retry-After`), 페이로드 상한을 갖춘 멱등 `/api/events/ingest`(3344e78), 결정적 `Last-Event-ID`
  재생(#154), Valkey lease 기반 단일 소유 Kubernetes informer(#158).
- **Preflight 라우트**: `/api/dr/restore-preflight`(#215), `/api/storage/pvc-expansion-preflight`
  (#221, #222, #223), 고아 네임스페이스 패턴을 찾는 `/api/settings/role-mappings/preflight`(2d44836).
  DR과 PVC 판정은 호출자 입력이 아니라 서버가 관찰한 사실에서만 도출됨.
- **클러스터 레지스트리 기반**: `config/clusters.json`, `cluster_id` 스코프 차원, `/api/settings/clusters`
  관리자 CRUD(681f845), `/api/domain/clusters` 파일럿(e36a68d).
- **공통 외부 HTTP 클라이언트**: Kubernetes apiserver를 포함한 모든 provider 클라이언트에 타임아웃,
  제한된 재시도, 상관 ID를 적용함(#143, #149, #151, #152).
- **운영자 UX**: 재시도와 최신성 표시를 갖춘 공용 로딩/오류/빈 상태/부분/오래됨 `DataState`(#156,
  #159), 캐시 캡처 시각(#157), 취약점 DB 최신성(584b797), 내비게이션의 포털 버전·커밋 표시(#150),
  넘치는 내비게이션 항목을 위한 "More" 메뉴(#167).
- **사용자 생성 시 역할 그룹 지정**: `POST /api/settings/users`가 포털 역할 허용 목록으로 제한된 선택적
  `groups` 배열을 받음(#132).
- **공급망**: cosign 이미지 서명(3026d23), OSS 고지와 의존성 SBOM(2e3ffc5), 신규 의존성에 대한 7일
  lockfile 숙성 게이트(69a4be5).

### 변경
- **카탈로그 롤백이 결과를 분류함 (#245, b0c304e)**: ArgoCD의 4xx는 해당 상태와 `outcome: "rejected"`로
  반환됨. ArgoCD 토큰이 없거나 거부되면 503, 403은 403을 반환함. 타임아웃, 네트워크 오류, 5xx, 408,
  읽을 수 없는 2xx는 502와 `outcome: "unknown"`("재시도 전에 애플리케이션 상태를 확인")으로 반환됨.
  ArgoCD POST는 한 번만 전송되며 자동 재시도되지 않음. 2xx는 요청한 Application을 담고 있어야 함.
- **Silence 생성/삭제가 결과를 분류함 (#247, 486ac21)**: 존재하지 않는 silence 삭제는 404, 그 밖의
  4xx는 해당 상태와 `outcome: "rejected"`, 401은 503, 403은 403, 타임아웃·네트워크 오류·408·5xx·깨진
  2xx는 502와 `outcome: "unknown"`을 반환함. 생성된 `silenceID`는 `[A-Za-z0-9_-]{1,128}`에 맞을 때만
  수락되며 trim 없이 반환된 값 그대로 사용됨.
- **Silence DELETE 소유권 조회 (#244, 3b4d320)**: 명시적인 Alertmanager 404만 "없음"으로 취급함. 401은
  503, 403은 403, 그 밖의 실패나 깨진 조회 응답은 DELETE를 보내지 않고 502를 반환함.
- **ArgoCD 쓰기 전제 조건 (#243, 7f7dc1c)**: 애플리케이션 확인 중 provider 장애가 더 이상
  `Application not found`로 보고되지 않으며, 자격 증명 오류가 503 매핑에 도달함.
- **sync 라우트의 고정 오류 문구 (#246, 1b9984d)**: `argocd/sync`와 `catalog/[name]/sync`는 상류 원문
  오류 대신 `ArgoCD sync failed`(502)를 반환하고, `operation.failed` 이벤트 description도 고정
  문구임. 롤백 이벤트도 같은 규칙을 따름(#245).
- **`/api/status`는 ArgoCD 또는 Alertmanager를 읽을 수 없으면 `degraded`를 보고함 (#242, 2d38cbb)**:
  실패한 provider는 `unknown` 컴포넌트(요약 접미사 `status incomplete: … unreachable`)로 표시되며
  결과는 캐시되지 않음. 로컬 개발처럼 ArgoCD나 Alertmanager가 미구성이거나 접근 불가한 환경은 이제
  `healthy` 대신 `degraded`로 표시됨.
- **의존성 상태가 fail closed로 동작함**: 의존성 상태가 0건인 집계는 `ok`가 아니라 `unavailable`이며
  (#240), 캐시된 스냅샷은 형태 검증을 통과한 경우에만 신뢰함(#241).
- **Kubernetes 목록 계약 (#239, 70bc7ce)**: `items` 배열이 없는 200 응답은 오류이며, `limit` /
  `maxPages`는 양의 안전 정수여야 하고 아니면 요청 전에 거부됨. 클러스터 전체 인벤토리 목록은 상한이
  있고 `truncated`를 보고함(4c146b7).
- **외부 HTTP 본문과 취소**: 디코딩된 응답 본문이 8 MiB를 넘으면 호출자가 한도를 올리지 않은 이상
  `response-too-large` 오류로 실패함(#225). 재시도 백오프와 네이티브 `RequestInit` 취소는 호출자의
  abort를 따름(#228, #238).
- **라이브 이벤트**: Kubernetes watch 만료 후 복구는 상한이 있는 페이지네이션 스냅샷을 적용하고(#226),
  informer는 각 이벤트가 수락될 때까지 기다려 제출이 한 번에 하나만 진행됨(#229).
- **ArgoCD sync와 rollback이 수렴을 검증함**: 2xx는 수락만 의미하므로 라우트는 애플리케이션을 다시
  읽고 `operationState`가 수렴할 때까지 `pending: true`를 반환함(#121).
- **필수 CI 체크**: `Portal critical regression gate`는 항상 보고되는 필수 체크이며(#227), SHA에 묶인
  `independent-review` 커밋 상태가 머지를 막음(#213).
- **의존성**: Next.js 16.3.5와 React 19.3.0(#170), `@base-ui/react` 1.8.0(#212), `@grpc/grpc-js`
  1.14.5(#211), `upload-artifact`, `download-artifact`, `setup-node`, `harden-runner` GitHub Actions
  묶음 업데이트(#230, #233, #232, #231).
- **Kaniko 빌드 증거 (#169, 5df84c5)**: `kaniko-build.sh`가 빌드 성공 후 `build-evidence/<tag>.json`을
  기록하며, 이미지 digest 형식이 잘못되면 실패함.
- **타입이 있는 Kubernetes 자격 증명 오류 (#176, d34cc54)**: 토큰 갱신 후에도 401이 남거나 403이면
  `K8sCredentialError`가 발생함.

### 수정
- **읽기 범위 누락 해소**: `/api/cost`(2ba8fc5)와 상세·추세 라우트(#123), `/api/governance/resources`
  (#147), `/api/k8s/events`(#145), `/api/catalog`, `/api/events`, `/api/k8s/pods`,
  `/api/k8s/resource`(1e48371)가 캐시나 상류 읽기 이전에 호출자의 가시 네임스페이스로 필터링하며,
  캐시 키에 스코프 지문이 포함됨.
- **Silence 생성에 범위 적용**: 관리자가 아닌 호출자는 자신의 범위 안의 정확 일치 `namespace` matcher가
  필요하고, 모든 역할에서 전체 일치 정규식 matcher는 거부되며, 선택적 `Idempotency-Key`를 지원함
  (5ce9516).
- **ArgoCD 쓰기가 프로젝트뿐 아니라 대상 네임스페이스도 검사함** (d00eff1).
- **미상은 정상이 아님**: 스코어카드는 `unavailable` 증거와 `fail`을 구분하고(#118), DORA는 알 수 없는
  배포 결과와 `null` 변경 실패율을 거짓 0 대신 보고하며(#117), 비용은 가격 미구성 시 fail closed로
  동작하고(e2ad866), 부분적인 Keycloak 그룹 조회를 완전한 것처럼 표시하지 않음(#113).
- **만료된 목록 continue 토큰**: 상한이 있는 Kubernetes 목록은 410에서 한 번 재시작함(#141).
- **Kubeconfig 서버**: 다운로드한 kubeconfig는 클러스터 내부가 아닌 외부 API 서버 주소를 사용함
  (506c279).
- **이미지 빌드**: Kaniko 경로는 `latest` 대신 digest 파일과 함께 SHA 이름의 소스 태그를 푸시하고
  (a874ac7), 독립 실행형 이미지는 `@swc/helpers`를 포함하며 CI에서 smoke boot 됨(#164).
- **라이브 이벤트 장애 복구 (#183, 1b8f5ca)**: 장애 이전의 숫자형 `Last-Event-ID` 커서가 이제 프로세스
  로컬 링에서 재생되고, Valkey 장애 중에 이뤄진 멱등성 claim이 같은 프로세스 안에서 Valkey 복구 후에도 유지되며, 410 재조회는
  이미 가져온 페이지를 수집함(전체 백로그가 아니라 가장 최근 이벤트 하나만).

### 보안
- **프로덕션 인증된 Valkey TLS** (#205, 업그레이드 노트 참고).
- **로그아웃 강화**: federated logout은 POST 전용이며 CSRF 보호가 적용되고(ab69c4f), 검증할 수 없는
  요청은 fail closed로 처리하며(a6fc4ec), 리디렉션 호스트 허용 목록이 고정(anchor)되어
  `maliciouslocalhost`가 더 이상 통과하지 않음(25381e1).
- **자격 증명 처리**: ArgoCD와 Gitea 토큰은 호출마다 읽고 401에서 fail closed로 처리하며(#134),
  APISIX는 조용한 관리자 대체가 없고(#112), Keycloak 관리자 토큰 획득이 표준화됨(ea96a9a).
- **오류 문구가 상류 호스트나 본문을 노출하지 않음**: sync/rollback 응답과 `operation.failed` 이벤트
  스트림에서 제거됨(#245, #246).
- **권한 있는 노드 튜닝**: Job 실행 경계 강화(3a209f7), preflight dry-run(#105), 런타임에서의 정확한
  호출 승인(#90).
- **공급망**: 빌드 이미지를 digest로 고정하고 URL에 Gitea 비밀번호를 넣지 않으며(#109), 설치 시점
  작업의 CI 외부 통신을 차단하고(#106), `kubelogin`을 고정하고 체크섬을 검증함(#104).

### 알려진 문제
- **UI 롤백이 400을 반환함**: 카탈로그 UI(`service-actions.tsx`)는 `{historyId}`를 보내지만
  `POST /api/catalog/[name]/rollback`은 `{id}`를 요구함. 1.0.17 이전부터 있던 문제임.
- **롤백에 멱등성 없음**: 라우트가 `Idempotency-Key`를 점유하지 않으므로, `outcome: "unknown"` 응답
  뒤에 같은 키로 재시도하면 ArgoCD POST가 다시 전송됨.
- **Silence DELETE 소유권**은 팀 범위가 아니라 `createdBy`만 비교하며, 조회와 삭제 사이에 경쟁 구간이
  있음(Alertmanager는 같은 id의 `createdBy` 변경을 허용함).
- **`ArgoForbiddenError` 문구**가 403 본문에 ArgoCD 프로젝트 또는 네임스페이스를 포함함.
- **Groups / users PATCH**(`/api/settings/groups`, `/api/settings/users/[pk]`)는 변경을 다시 읽지 않고
  단순한 `{ok: true}`만 반환함.
- **viewer에게 클러스터 전체 뷰 노출**: `/api/governance/distribution`(인증된 모든 사용자)과
  `/api/service-graph`(viewer 이상)에는 스코프 필터가 없음.
- **클러스터 레지스트리는 기반 단계일 뿐임**: `/api/settings/clusters`는 `config/clusters.json`에
  저장하며, 해당 파일에 쓸 수 없으면 변경이 메모리에만 남아 재시작 시 사라짐. Kubernetes와 ArgoCD
  클라이언트는 아직 요청별로 클러스터를 해석하지 않음.
- **Kaniko 빌드 경로는 검증되지 않음 (a874ac7)**: Job 플레이스홀더 `__HARBOR_DESTINATION__`이
  `__HARBOR_DESTINATION_PREFIX__`로 바뀌었고(태그는 `HARBOR_TAG`로 덧붙임), 커밋은 실제 클러스터 내
  빌드와 푸시로 검증하지 않았다고 밝힘(미검증).
- **릴리스 노트 링크**: `release.yml`은 `CHANGELOG_ko.md`를 링크하지만 실제 파일명은
  `CHANGELOG-ko.md`임.

## [1.0.17] - 2026-08-09

플랫폼 도구 헬스 체크, 노드 역할 감지, 컴플라이언스 리포팅 지표 및 노드 인프라 감사 쿼리를 수정하고
버전 태그에 대한 자동화된 릴리스 노트 워크플로를 도입함.

### 추가
- **자동화된 GitHub Release 워크플로**: `v*` 태그 푸시 시 `CHANGELOG.md`에서 릴리스 노트를 추출하여
  게시하고, 버전 섹션이 누락되면 작업을 실패 처리하는 `.github/workflows/release.yml` 워크플로 추가.

### 수정
- **구성 가능한 플랫폼 도구 기본 도메인**: 인팟(in-pod) 헬스 체크의 NXDOMAIN 실패를 방지하고 커스텀
  클러스터 도메인을 지원하기 위해 `CLUSTER_BASE_DOMAIN`(및 `NEXT_PUBLIC_CLUSTER_BASE_DOMAIN`) 환경 변수 지원
  추가(기본값 `local.narwhal.internal`).
- **Prometheus 및 K8s 라벨 기반 노드 역할 도출**: Prometheus 쿼리에서 노드 라벨에 조인된 `kube_node_role`
  메트릭을 사용하고, K8s API 응답에서 `control-plane` 및 기존 `master` 라벨/테인트 키를 모두 인식하여 노드가
  'worker'로 잘못 표시되던 문제 수정.
- **컴플라이언스 프레임워크 통과율 스케일링**: 평균 프레임워크 통과율 비율(0-1)에 100을 곱한 뒤 반올림하도록
  수정하여 0.77과 같은 비율이 1%로 표시되던 오류 해결.
- **클러스터 범위 노드 인프라 감사 리포트 쿼리**: trivy-operator의 노드 보안 감사 발견 사항이 올바르게
  반영되도록 네임스페이스 범위의 `infraassessmentreports`와 함께 클러스터 범위의
  `clusterinfraassessmentreports`를 조회하여 병합.

## [1.0.16] - 2026-07-25

실시간 컴포넌트 상태 추적을 위한 종속성 인지 플랫폼 상태(Platform Status) 페이지를 도입하고
네이티브 멀티 아키텍처 빌드로 컨테이너 릴리스 워크플로를 최적화함.

### 추가
- **종속성 인지 플랫폼 상태 페이지**: 클러스터 컴포넌트 및 하위 인프라 종속성 전반의 실시간
  상태 지표를 표시하는 `/status` 뷰 추가.
- **디자인 시스템 토큰 계약**: 프론트엔드 스타일링 일관성을 위한 디자인 토큰 가이드라인 및 CI
  린트 규칙을 정의하는 `DESIGN.md` 도입.

### 변경
- **각 아키텍처가 네이티브 러너에서 빌드됨** — 단일 작업이 두 플랫폼을 모두 빌드하고 arm64
  단계가 QEMU 환경에서 `next build`를 실행했기 때문에 1.0.16 이미지가 게시되지 않았음: amd64는
  약 90초 만에 완료된 반면 arm64는 5분이 지난 후에도 계속 컴파일 중이었으며, 이전 실행은
  취소되기 전까지 2시간 38분에 달했음. 이제 아키텍처당 하나의 작업(`ubuntu-24.04-arm`, 공개
  리포지토리 무료)이 다이제스트별로 푸시하고 최종 작업이 이를 하나의 멀티 아키텍처 매니페스트로
  병합함. 어디에서도 QEMU를 사용하지 않음.

## [1.0.15] - 2026-07-13

*패키지 버전을 클러스터 GitOps 이미지 핀과 맞추기 위해 버전이 1.0.4에서 1.0.15로 건너뜀.*

Keycloak OIDC 세션 유지(keep-alive), 단일 실행(single-flight) 토큰 갱신 및 분할(chunked) 세션
쿠키 처리를 통해 인증 안정성을 향상시키고, 플랫폼 도구를 확장하며 보안 컴플라이언스 리포팅을
개선함.

### 추가
- **Kubernetes Dashboard 및 NFS Quota 도구 타일**: Kubernetes Dashboard 3.0 및 NFS Quota 관리를
  위한 제로클릭 게이트웨이 SSO 대시보드 타일 통합.
- **Keycloak SSO 세션 유지(keep-alive)**: 사용자 중단 없이 활성 OIDC 세션을 유지하기 위해 단일
  실행(single-flight) 요청 실행을 사용하는 백그라운드 토큰 갱신 구현.
- **상위 도메인 테마 동기화**: Keycloak 로그인 화면과 다크/라이트 테마 기본 설정을 동기화
  상태로 유지하기 위해 상위 도메인 전체에 `narwhal-theme` 쿠키 공유.
- **컴플라이언스의 위생 심각도 계층**: 조치가 필요한 위협과 별개로 마이너한 보안 위생 발견
  사항을 추적하기 위해 config-audit 요약에 `LOW` 심각도 계층 추가.

### 수정
- **분할 세션 쿠키로 인한 로그인 리다이렉트 루프**: 브라우저 헤더 크기 제한을 초과하는 분할
  세션 쿠키를 올바르게 재구성하도록 공유 서술어(`isSessionCookie`)를 통해 NextAuth 세션 쿠키
  처리 업데이트.
- **컴플라이언스 주요 지표 노이즈 감소**: 컴플라이언스 대시보드의 조치가 필요한 주요 지표에서
  내장/업스트림 RBAC 역할 및 시스템 네임스페이스 발견 사항을 필터링하여 제외.

## [1.0.4] - 2026-07-10

핵심 KISA 보안 컴플라이언스 통제 항목을 실시간 Kubernetes 리소스 검사로 전환하고 실시간 이벤트
스트림 분류를 강화함.

### 추가
- **실시간 KISA 보안 컴플라이언스 검사**: `KISA-CP-01`, `KISA-ETCD-01`, `KISA-POD-01`,
  `KISA-NET-01`에 대한 정적 컴플라이언스 규칙을 실시간 클러스터 검사로 전환하고 `KISA-RBAC-01`
  / `KISA-RBAC-02`에 대한 내장 인지 평가 구현.

### 변경
- **직접 Gitea 리포지토리 링크**: Gitea 플랫폼 도구 타일 링크가 `narwhal-gitops` 리포지토리로
  직접 이동하도록 업데이트.
- **Valkey TLS 우회 구성**: 비프로덕션 환경에서 엄격한 TLS 검증을 우회하기 위해
  `VALKEY_INSECURE_PRODUCTION` 환경 변수 지원 추가.

### 수정
- **실시간 이벤트 스트림 분류**: 스트리밍된 K8s 이벤트를 분류하여 UI 카테고리 필터링을 가능하게
  하고 심각한 뷰 아래에 경고 이벤트를 노출.

## [1.0.3] - 2026-07-09

실시간 스트리밍 게시/구독(pub/sub) 작업을 전용 Valkey 연결 풀로 분리함.

### 수정
- **실시간 스트리밍을 위한 전용 Valkey 클라이언트**: 일반 캐시 작업에서 구독자 블로킹을
  방지하기 위해 SSE(Server-Sent Events) 게시/구독(pub/sub) 채널 및 파이프라인 전용 Valkey
  클라이언트 인스턴스 생성.

## [1.0.2] - 2026-07-09

실시간 이벤트 스트림을 대시보드에 공급하기 위한 실시간 Kubernetes Events 인포머 백엔드를
구축함.

### 추가
- **Kubernetes Events 인포머** — `/live`에 이벤트 소스가 전혀 없었음: `live-k8s-informer.ts`는
  호출된 적이 없는 TODO 스터브였으며 `/api/events/ingest`로 아무것도 게시되지 않아 페이지가
  비어 있을 수밖에 없었음. 이제 클러스터 범위의 core/v1 Events watch(`resourceVersion` + 백오프
  재연결, 모든 `Warning` 이벤트 및 정선된 `Normal` 사유 세트)를 실행하고, 각 이벤트를
  `LiveEvent`로 매핑하여 Valkey pub/sub 채널로 게시하는 `pushEvent()`를 호출함. Node.js
  런타임의 `instrumentation.ts`에서 한 번 시작됨.

### 변경
- **릴리스 CI 빌드 트리거**: 중간 커밋에서 불필요한 태그 없는 이미지 빌드를 방지하기 위해 GHCR
  컨테이너 빌드 워크플로를 태그 푸시(`v*`)로 제한.

## [1.0.1] - 2026-07-09

실시간 SSE 스트림 연결을 안정화하고, 컴플라이언스 리포트 파싱을 개선하며, 알림 스코어카드
지표를 정리함.

### 추가
- **노드 검사를 위한 조치 항목 전용 토글**: 노드 상세 뷰에서 정상 상태의 CNI 및 컨트롤 플레인
  정보 패널을 숨기고 조치가 필요한 항목에 집중할 수 있는 필터 토글 추가.

### 수정
- **SSE 재연결 스톰**: 클라이언트 재연결 루프를 방지하기 위해 pub/sub 구독자 실패 중에도
  SSE(Server-Sent Events) 스트림을 유지.
- **Trivy 컴플라이언스 리포트 파싱**: `detailReport.results`를 읽고 통제 항목별 통과/실패
  상태를 정확하게 평가하도록 리포트 파싱 로직 업데이트.
- **거버넌스 스코어카드 노이즈**: 거버넌스 스코어카드 지표 집계에서 Prometheus의 합성 메타
  알림인 `Watchdog` 및 `InfoInhibitor` 제외.

## [1.0.0] - 2026-07-07

Next.js 16 및 React 19로 구축된 Kubernetes 내부 개발자 플랫폼(IDP) 관리 대시보드인 Narwhal IDP
Portal의 첫 번째 공개 릴리스.

### 추가
- **핵심 대시보드 및 서비스 맵**:
  - 실시간 클러스터 이벤트 타임라인, 활동 피드 및 리소스 요약을 제공하는 라이브 대시보드.
  - Hubble 릴레이 L4/L7 eBPF 네트워크 흐름, 실시간 트래픽 비율, 네임스페이스 필터링, 노드 상세
    드로어 및 시각적 범례를 통합한 대화형 서비스 종속성 그래프.
  - 대화형 조치 필요 아코디언 필터, 시스템 감사 패널 및 용량 지표를 포함한 노드 검사 뷰.
- **애플리케이션 카탈로그 및 워크로드**:
  - `app.kubernetes.io/instance` 범위의 스트리밍 컨테이너 로그를 지원하는 애플리케이션 카탈로그
    및 파드 관리 뷰.
  - 팀 재정의 기능을 갖춘 역할 기본 애플리케이션 가시성 범위(`my-apps`).
- **보안 컴플라이언스 및 거버넌스**:
  - 정렬 가능한 테이블 및 Trivy 취약점 리포트 통합을 포함한 KISA 보안 통제 프레임워크
    체크리스트(`/compliance`).
  - 워크로드 분배 분석, 리소스 요청(requests)이 누락된 파드, RBAC 위험 분석 및 DORA 지표를
    제공하는 거버넌스 대시보드.
  - 온프레미스 실제 비용 기준 계산 방법론.
- **단일 로그인(SSO) 및 접근 제어**:
  - 자동 로그인 프록시 리다이렉트 및 상대 경로 `callbackUrl` 처리를 포함한 Keycloak OIDC 인증.
  - 보안 쿠키(`__Secure-`, `__Host-`)를 정리하면서 APISIX 게이트웨이(`/apisix/logout`)를 통해
    라우팅되는 RP 개시 연합 로그아웃(SLO 체인).
  - 탐색 및 플랫폼 도구 전반에서 `cluster-admin`, `developer`, `viewer`, `guest` 역할을
    지원하는 4계층 역할 기반 접근 제어(RBAC).
- **플랫폼 도구 연동**:
  - ArgoCD, Grafana, Harbor, Gitea, OpenBao, Velero UI 및 Headlamp를 위한 딥링크 및 제로클릭
    SSO 부트스트랩을 지원하는 플랫폼 도구 그리드.
- **국제화 및 디자인 시스템**:
  - 쿠키 기반 로캘 전환 및 한국어 타이포그래피를 위한 자체 호스팅 Pretendard 폰트를 갖춘 이중
    언어 i18n 지원(영어 및 한국어).
  - Next.js 16, React 19 및 Tailwind CSS로 구축된 현대적인 반응형 UI.
- **컨테이너 빌드 및 로컬 개발**:
  - GHCR에 게시되는 멀티 아키텍처 Docker 빌드를 지원하는 컨테이너화
    설정(`ghcr.io/dasomel/narwhal-portal:1.0.0`).
  - 오프라인 클린 설치를 위한 클러스터 내 Kaniko 빌드 Job
    매니페스트(`deploy/kaniko-build-job.yaml`).
  - 실시간 핫 모듈 교체(HMR) 및 컨테이너 파일 동기화를 위해 구성된 Skaffold 개발 프로필.

[Unreleased]: https://github.com/dasomel/narwhal/compare/portal/v1.1.32...HEAD
[1.1.32]: https://github.com/dasomel/narwhal/compare/portal/v1.1.31...portal/v1.1.32
[1.1.31]: https://github.com/dasomel/narwhal/compare/portal/v1.1.30...portal/v1.1.31
[1.1.30]: https://github.com/dasomel/narwhal/compare/portal/v1.1.29...portal/v1.1.30
[1.1.29]: https://github.com/dasomel/narwhal/compare/portal/v1.1.28...portal/v1.1.29
[1.1.28]: https://github.com/dasomel/narwhal/compare/portal/v1.1.27...portal/v1.1.28
[1.1.27]: https://github.com/dasomel/narwhal/compare/portal/v1.1.26...portal/v1.1.27
[1.1.26]: https://github.com/dasomel/narwhal/compare/portal/v1.1.25...portal/v1.1.26
[1.1.25]: https://github.com/dasomel/narwhal/compare/portal/v1.1.24...portal/v1.1.25
[1.1.24]: https://github.com/dasomel/narwhal/compare/portal/v1.1.23...portal/v1.1.24
[1.1.23]: https://github.com/dasomel/narwhal/compare/portal/v1.1.22...portal/v1.1.23
[1.1.22]: https://github.com/dasomel/narwhal/compare/portal/v1.1.21...portal/v1.1.22
[1.1.21]: https://github.com/dasomel/narwhal/compare/portal/v1.1.20...portal/v1.1.21
[1.1.20]: https://github.com/dasomel/narwhal/compare/portal/v1.1.19...portal/v1.1.20
[1.1.19]: https://github.com/dasomel/narwhal/compare/portal/v1.1.18...portal/v1.1.19
[1.1.18]: https://github.com/dasomel/narwhal/compare/portal/v1.1.17...portal/v1.1.18
[1.1.17]: https://github.com/dasomel/narwhal/compare/portal/v1.1.16...portal/v1.1.17
[1.1.16]: https://github.com/dasomel/narwhal/compare/portal/v1.1.15...portal/v1.1.16
[1.1.15]: https://github.com/dasomel/narwhal/compare/portal/v1.1.14...portal/v1.1.15
[1.1.14]: https://github.com/dasomel/narwhal/compare/portal/v1.1.13...portal/v1.1.14
[1.1.13]: https://github.com/dasomel/narwhal/compare/portal/v1.1.12...portal/v1.1.13
[1.1.12]: https://github.com/dasomel/narwhal/compare/portal/v1.1.11...portal/v1.1.12
[1.1.11]: https://github.com/dasomel/narwhal/compare/portal/v1.1.10...portal/v1.1.11
[1.1.10]: https://github.com/dasomel/narwhal/compare/portal/v1.1.9...portal/v1.1.10
[1.1.9]: https://github.com/dasomel/narwhal/compare/portal/v1.1.8...portal/v1.1.9
[1.1.8]: https://github.com/dasomel/narwhal/compare/portal/v1.1.7...portal/v1.1.8
[1.1.7]: https://github.com/dasomel/narwhal/compare/portal/v1.1.6...portal/v1.1.7
[1.1.6]: https://github.com/dasomel/narwhal/compare/portal/v1.1.5...portal/v1.1.6
[1.1.5]: https://github.com/dasomel/narwhal/compare/portal/v1.1.4...portal/v1.1.5
[1.1.4]: https://github.com/dasomel/narwhal/compare/portal/v1.1.3...portal/v1.1.4
[1.1.3]: https://github.com/dasomel/narwhal/compare/portal/v1.1.2...portal/v1.1.3
[1.1.2]: https://github.com/dasomel/narwhal/compare/portal/v1.1.1...portal/v1.1.2
[1.1.1]: https://github.com/dasomel/narwhal/compare/portal/v1.1.0...portal/v1.1.1
[1.1.0]: https://github.com/dasomel/narwhal/releases/tag/portal/v1.1.0
[1.0.17]: https://github.com/dasomel/narwhal-portal/compare/v1.0.16...v1.0.17
[1.0.16]: https://github.com/dasomel/narwhal-portal/compare/v1.0.15...v1.0.16
[1.0.15]: https://github.com/dasomel/narwhal-portal/compare/v1.0.4...v1.0.15
[1.0.4]: https://github.com/dasomel/narwhal-portal/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/dasomel/narwhal-portal/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/dasomel/narwhal-portal/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/dasomel/narwhal-portal/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/dasomel/narwhal-portal/releases/tag/v1.0.0
