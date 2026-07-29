# RTK Token-Compression Policy

This document defines the operational guidelines and policies for applying Run-Time token-compression (RTK) mechanisms to the terminal and command outputs in the `narwhal` cluster provisioning and operations environment.

---

## 1. Core Decisions

### [결정 D1] narwhal 클러스터 출력에 RTK 기본 비활성화
`narwhal` 클러스터 환경에서는 Run-Time token-compression(RTK) 필터링 및 압축 메커니즘을 **기본 비활성화(Default Disabled)** 한다.

#### 근거 (Rationale)
1. **소스 단계의 무손실 추출 기적용**:
   `narwhal` 명령의 대다수는 이미 소스 수준에서 무손실 추출 방식(예: `-o jsonpath`, `-o custom-columns`, `--no-headers | grep -c`, `--field-selector`, `-format=json`)을 사용하고 있어, 후처리 성격의 RTK 압축을 도입하더라도 추가적인 토큰 절감 효과가 무익하다.
2. **Silent Failure 위험**:
   핵심 명령들의 출력은 스크립트 내에서 제어 흐름(게이트)이나 중요 변수 값으로 직접 사용되는 "고신뢰 경로(High-Confidence Paths)"이다. RTK와 같은 정규식 기반 압축을 적용할 경우 의도치 않은 패턴 매칭 오류나 필터 누락으로 인한 silent failure의 위험성이 크다.
3. **장애 복구 시 진단 데이터 손실 (캐스케이드 악화)**:
   비정상 상황이나 장애 상황 시 상세 로그가 압축/필터링되어 유실되면 진단이 불가능해지며, 이는 2차 장애로 이어지는 캐스케이드 악화 위험을 초래한다.

### [결정 D2] 장애 복구 모드 내 RTK 전면 차단
장애 복구 및 재해 복구(DR) 모드, 서비스 메시 캐스케이드(mesh cascade), OpenBao 밀봉 해제/밀봉(unseal/reseal) 등 시스템 안정성과 직접 직결되는 비상/복구 운영 단계에서는 **RTK의 사용을 전면 차단**한다. 진단을 위한 완벽하고 원래의(raw) 로그 출력이 보존되어야 한다.

---

## 2. 명령 분류 표 (Classification Table)

| 분류 | 대상 명령 예시 | 적용 가이드라인 및 설명 |
| :--- | :--- | :--- |
| **❌ RTK 금지<br>(고신뢰 경로)** | <ul><li>`bao operator init/status -format=json`</li><li>`bao operator unseal`</li><li>`etcdctl endpoint health/member list`</li><li>`kubectl get secret -o jsonpath`</li><li>`kubeadm init` (join command 추출부)</li><li>`kubectl wait`</li><li>`helm upgrade --install`</li><li>`kubectl apply`</li><li>cert Ready jsonpath</li></ul> | 스크립트 게이트 제어 및 민감한 인증/설정 값을 직접 추출하고 판단하는 경로. RTK 필터 적용 시 제어 흐름 붕괴 위험이 있어 절대 금지. |
| **⚠️ 무의미<br>(이미 소형/추출됨)** | <ul><li>`-o jsonpath` 류 전부</li><li>`-o custom-columns` 류 전부</li><li>`--no-headers \| grep -c`</li><li>`-format=json`</li></ul> | 이미 명령 실행 수준에서 필요한 정보만 극소량으로 추출하므로 추가 압축이 불필요함. |
| **✅ 조건부 가능<br>(loss-tolerant 대량 덤프)** | <ul><li>`velero backup/restore logs`</li><li>`kubectl logs --tail`</li><li>`helm get values`</li><li>`helm search repo --versions`</li><li>`kubectl get events --sort-by`</li></ul> | 대량의 비정형 텍스트 덤프로 인해 토큰 소모가 크고 손실을 감수할 수 있는 경우. 단, 아래의 **Escape Hatch** 조건 충족 전에는 적용 금지. |

---

## 3. 권장 대안: 소스 단계 무손실 추출 (Lossless-Extraction Alternative)
출력 토큰을 줄이기 위해 사후 정규식 압축(RTK)을 사용하는 대신, CLI 자체의 기능(`-o jsonpath`, `--field-selector`, `-o custom-columns` 등)을 이용해 필요한 데이터만을 소스 단계에서 무손실 추출하는 방식을 권장하고 점진적으로 확장한다.

---

## 4. Escape Hatch (탈출 조건)
조건부 가능 범주에 속하는 4가지 유형의 명령(`velero logs`, `kubectl logs`, `helm get values`/`search`, `kubectl get events`)일지라도 **"정답당 비용 + 진단 성공률"에 대한 정량적 측정 및 검증 없이는 실제 RTK를 적용하는 것을 금지**한다.
* 예외적으로 `velero backup/restore logs`와 같이 비정형 데이터라 `jsonpath` 적용이 불가능한 경우에도, 에러/panic 라인이 완벽히 보존되고 원본 로그 폴백(raw fallback) 경로가 확보된 상태에서만 신중하게 검토하여 도입할 수 있다.
