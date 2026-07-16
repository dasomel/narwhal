# Chaos Mesh 실험 런북 (Runbook)

이 문서는 Kubernetes IDP 클러스터(`narwhal`)에서 Chaos Mesh를 사용하여 인프라의 탄력성(Resiliency)을 검증하기 위한 런북입니다.

---

## 1. istiod-kill (Istio Control Plane 장애 검증)

### 가설
- **과거 장애 이력**: 과거 `istiod`가 단일 레플리카로 동작하던 중 SPOF(Single Point of Failure)로 작용하여, 컨트롤 플레인 장애 발생 시 전체 서비스 메쉬의 설정 동기화가 불가능해지고 프록시 통신 전반에 걸쳐 연쇄 장애(Mesh Cascade)가 발생한 이력이 있습니다.
- **가설**: `istio-system` 네임스페이스의 `istiod` 파드가 킬(kill)되더라도 기존 프록시 간 통신(Data Plane)은 단절 없이 유지되어야 하며, `istiod` 파드가 재시작(Recovery)된 이후 제어 흐름이 정상 복구되어야 합니다.

### 절차
```bash
./tests/chaos/run-chaos.sh istiod-kill
```

### 판정 기준
- `istiod` 파드가 삭제된 후 새로운 파드가 즉시 생성되어 `Running` 및 `Ready` 상태가 되어야 합니다.
- 실험 도중 및 실험 이후 Portal 서비스의 `/login` 페이지가 200 OK로 계속 응답해야 합니다.

### 관찰 포인트
- **Grafana**: Istio Control Plane 대시보드 (`istiod` 리스타트 횟수, Envoy 동기화 상태)
- **Loki**: `istio-system` 네임스페이스 내 `istiod` 로그 중 "Push timeout" 이나 "Active connection" 관련 에러 로그 유무

### 실제 관측 셀렉터
- **Namespace**: `istio-system`
- **Label Selector**: `app=istiod`

### 결과 기록 테이블
| 실험 일시 | 실행자 | 결과 (PASS/FAIL) | 특이사항 / 관측 지표 |
| :--- | :--- | :--- | :--- |
| | | | |

---

## 2. openbao-kill (Vault/OpenBao Secret-Store 장애 검증)

### 가설
- **과거 장애 이력**: OpenBao(또는 Vault) 파드가 비정상적으로 재시작되었을 때, 자동으로 Unseal(잠금 해제)되지 않고 Seal 상태로 대기하여 비밀번호나 API 토큰을 조회하는 서비스들이 모두 장애 상태에 빠졌던 사례가 있습니다.
- **가설**: `storage` 네임스페이스의 `openbao-0` 파드를 강제 종료하면, 재시작 시 `openbao-auto-unseal` 자동화 작업(CronJob/Job)이 즉각 실행되어 파드가 자동으로 Unseal 상태가 되어 정상 서비스를 재개해야 합니다.

### 절차
```bash
./tests/chaos/run-chaos.sh openbao-kill
```

### 판정 기준
- `openbao-0` 파드가 강제 종료된 후, 새로 뜬 파드가 자동으로 Unseal되어 `1/1 Ready` 상태로 복구되어야 합니다.
- Portal 로그인 페이지(`/login`)가 정상 동작하여 200 OK 응답을 유지해야 합니다 (OpenBao 장애 시 로그인 토큰 생성/검증에 영향).

### 관찰 포인트
- **Kubectl / Job**: `openbao-auto-unseal` Job이 정상적으로 구동되고 Completed 되는지 관찰.
- **Loki**: `storage` 네임스페이스의 `openbao-0` 파드 로그에서 "Vault is sealed" 상태 해제 메시지 확인.

### 실제 관측 셀렉터
- **Namespace**: `storage`
- **Label Selector**: `app.kubernetes.io/name=openbao`, `component=server`

### 결과 기록 테이블
| 실험 일시 | 실행자 | 결과 (PASS/FAIL) | 특이사항 / 관측 지표 |
| :--- | :--- | :--- | :--- |
| | | | |

---

## 3. coredns-latency (DNS 지연 장애 검증)

### 가설
- **과거 장애 이력**: 호스트가 과부하되었을 때 CoreDNS 파드가 CPU/네트워크 자원 고갈(Starvation)을 겪으며 DNS 쿼리 타임아웃이 발생하였고, 이로 인해 내부 마이크로서비스 간 통신이 연쇄적으로 끊어진 사건이 있었습니다.
- **가설**: CoreDNS에 500ms의 네트워크 대기 시간(Latency)을 유입시켜도, 서비스 내부의 캐싱 메커니즘 및 타임아웃 재시도 정책이 정상적으로 작동하여 Portal 등의 핵심 서비스 호출이 실패하지 않고 지연 처리된 후 정상 응답해야 합니다.

### 절차
```bash
./tests/chaos/run-chaos.sh coredns-latency
```

### 판정 기준
- 60초간의 DNS 지연 주입 기간 동안 및 그 이후, Portal `/login` 페이지 호출 시 비록 응답 속도는 느려질 수 있으나 반드시 HTTP 200 OK 응답을 반환해야 합니다.

### 관찰 포인트
- **Grafana**: CoreDNS Dashboard (DNS Request Duration, Error rate)
- **Loki**: `kube-system` 내 `coredns` 로그에서 DNS query 처리 지연 로그 관찰.

### 실제 관측 셀렉터
- **Namespace**: `kube-system`
- **Label Selector**: `k8s-app=kube-dns`

### 결과 기록 테이블
| 실험 일시 | 실행자 | 결과 (PASS/FAIL) | 특이사항 / 관측 지표 |
| :--- | :--- | :--- | :--- |
| | | | |

---

## 4. keycloak-kill (IAM 인증 시스템 장애 검증)

### 가설
- **과거 장애 이력**: 인증 컨트롤러인 Keycloak 파드가 비정상적으로 종료되었을 때, 분산 세션 정보가 소실되거나 DB 연결 유실로 인해 접속 중이던 모든 사용자가 강제 로그아웃되고 신규 로그인이 마비된 적이 있습니다.
- **가설**: `iam` 네임스페이스의 Keycloak 파드가 제거되더라도, 클라이언트 단의 기존 JWT 세션은 만료 시까지 정상적으로 살아있어야 하며, 파드가 재시작된 이후에는 기존 세션 검증이 중단 없이 이어지고 새로운 신규 로그인 요청만 일시 지연 후 정상 처리되어야 합니다.

### 절차
```bash
./tests/chaos/run-chaos.sh keycloak-kill
```

### 판정 기준
- `keycloak-0` 파드 삭제 후 재시작 기간 동안, 기존에 발급된 JWT 토큰을 사용하는 API 요청은 유효해야 하며, 복구 즉시 `/login` 페이지가 200 OK를 유지해야 합니다.

### 관찰 포인트
- **Grafana**: Keycloak JVM Dashboard (Active sessions, DB Connection Pool)
- **Loki**: Keycloak 로그에서 세션 복구 및 DB 재연결 에러 확인.

### 실제 관측 셀렉터
- **Namespace**: `iam`
- **Label Selector**: `app=keycloak`

### 결과 기록 테이블
| 실험 일시 | 실행자 | 결과 (PASS/FAIL) | 특이사항 / 관측 지표 |
| :--- | :--- | :--- | :--- |
| | | | |

---

## 5. worker-cpu-stress (호스트 CPU 과부하 검증)

### 가설
- **과거 장애 이력**: 2026-07-14 호스트가 과부하되어 CPU가 100%에 도달하였을 때, 각 파드의 Liveness/Readiness Probe가 타임아웃으로 실패하였고 Kubernetes가 이를 비정상 상태로 오판하여 모든 파드를 강제 재시작하는 악순환(Probe-kill Storm)이 발생하였습니다.
- **가설**: 특정 워커 노드(`narwhal-worker-3`)에 CPU 부하를 과도하게 가하더라도, `pause` 이미지 기반의 독립 스트레스 타겟(`chaos-stress-target`)만을 타겟팅하므로 핵심 시스템 파드에는 직접적인 자원 고갈이 발생하지 않아야 하며 Node 내 Kubelet과 핵심 파드들이 정상 생존해야 합니다.

### 절차
```bash
./tests/chaos/run-chaos.sh worker-cpu-stress
```

### 판정 기준
- `chaos-stress-target` 파드가 작동하는 워커 노드에서 CPU 부하가 증가하지만, 시스템 파드들이 Probe 실패로 인해 강제 종료되거나 재시작되는 현상(Restart count 증가)이 발생하지 않아야 합니다.
- Portal 로그인 페이지가 정상 가동 상태(200 OK)를 유지해야 합니다.

### 관찰 포인트
- **Grafana**: Node Exporter 대시보드 (`narwhal-worker-3` 노드의 CPU Usage, Pod Restart Count)
- **Loki**: Kubelet 로그 중 "Probe failed" 또는 "Killing pod" 검색.

### 실제 관측 셀렉터
- **Namespace**: `chaos-testing`
- **Label Selector**: `chaos-target=stress`

### 결과 기록 테이블
| 실험 일시 | 실행자 | 결과 (PASS/FAIL) | 특이사항 / 관측 지표 |
| :--- | :--- | :--- | :--- |
| | | | |

---

## 6. cnpg-primary-kill (Database Failover 검증)

### 가설
- **과거 장애 이력**: PostgreSQL Primary DB 인스턴스가 예기치 않게 다운되었을 때 failover가 지연되어 Keycloak을 비롯한 IAM 인증 서버가 영구적인 DB 연결 오류를 내며 다운타임이 장기화되었던 사건이 있었습니다.
- **가설**: CloudNativePG(CNPG)의 Primary 인스턴스인 `narwhal-db-2` 파드를 종료(kill)하면, CNPG Operator가 이를 즉시 감지하여 기존 Replica 인스턴스(`narwhal-db-1` 등) 중 하나를 새로운 Primary로 신속하게 승격(Failover)시키고 애플리케이션의 연결을 복구해야 합니다.

### 절차
```bash
./tests/chaos/run-chaos.sh cnpg-primary-kill
```

### 판정 기준
- Primary 파드가 종료된 직후, 다른 파드가 Primary(`cnpg.io/instanceRole=primary`)로 승격되어 `Ready` 상태가 되어야 합니다.
- 전체 데이터베이스 서비스의 다운타임이 수십 초 이내로 제어되어야 하며, 최종 복구 단계에서 Portal 로그인(HTTP 200)이 정상 유지되어야 합니다.

### 관찰 포인트
- **Kubectl**: `kubectl get cluster narwhal-db -n database` 명령어 상태 모니터링 (Healthy/Failover 진행 상황).
- **Loki**: cnpg-operator 로그 및 PostgreSQL 로그에서 "promote" 관련 구문 추적.

### 실제 관측 셀렉터
- **Namespace**: `database`
- **Label Selector**: `cnpg.io/instanceRole=primary`

### 결과 기록 테이블
| 실험 일시 | 실행자 | 결과 (PASS/FAIL) | 특이사항 / 관측 지표 |
| :--- | :--- | :--- | :--- |
| | | | |
