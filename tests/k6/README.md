# K6 부하 테스트 (Load Testing)

## 목적
이 테스트는 Narwhal 클러스터의 주요 엔드포인트에 대한 부하 및 인증 플로우를 검증하여, 로컬 또는 운영 환경에서 시스템이 설정된 기준선(Threshold)을 만족하는지 확인합니다.

## 사전조건
1. **k6 설치**: `brew install k6` 명령어로 k6(v2.1.0 이상)를 설치합니다.
2. **Preflight 게이트**: 스크립트 실행 전 `preflight-host.sh`가 자동으로 시스템 리소스(CPU Load, Memory, k8s 노드 상태, Kind 클러스터 실행 여부)를 검사합니다. 통과해야 테스트가 진행됩니다.

## 실행법
`tests/k6/run-k6.sh` 스크립트를 사용하여 테스트를 실행할 수 있습니다.
```bash
# 단일 시나리오 실행
./run-k6.sh gateway-fanout
./run-k6.sh portal-browse
./run-k6.sh login-flow

# 전체 시나리오 순차 실행
./run-k6.sh all

# Prometheus 연동 실행 (모니터링)
./run-k6.sh all --prom
```

## VU 상한 근거
- **로그인 시나리오 (login-flow.js)**: VU(가상 사용자) 상한은 5명으로 제한되어 있습니다. Keycloak의 비밀번호 해싱 알고리즘은 CPU 집약적이므로, 2-vCPU 노드 환경에서 더 높은 VU는 시스템에 과도한 부하를 줄 수 있기 때문입니다.

## 기준선(Baseline) 절차
테스트를 최소 3회 이상 실행한 후, `p(95)` 응답 시간과 실패율(Failed Rate)의 평균적인 결과를 아래 표에 기록하여 이후 회귀 테스트의 기준선으로 삼습니다.

### 빈 기준선 표

| 시나리오 | p(95) | 실패율 (Failed Rate) | 날짜 |
|----------|-------|----------------------|------|
| gateway-fanout | | | |
| portal-browse | | | |
| login-flow | | | |
