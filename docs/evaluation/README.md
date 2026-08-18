# 검증 (Verification)

Narwhal 클러스터를 **단계적으로 검증**하는 가이드 모음이다. 각 단계는 다음 단계의
전제조건(안정 상태)을 확인해 주는 구조로, 순서대로 진행한다. 앞 단계가 실패하면
뒤 단계 결과는 신뢰할 수 없다.

> 배포 대상(Vagrant / Kakao)과 무관한 검증은 이 디렉토리에, 인프라가 답을 바꾸는
> 세부 절차는 [`../vagrant/`](../vagrant/) 또는 [`../kakao/`](../kakao/)를 참조한다.

## 검증 로드맵

| 단계 | 이름 | VM 필요 | 소요 시간 | 가이드 |
|------|------|---------|-----------|--------|
| 0 | 정적 검증 (Static Validation) | ✗ | 수 분 | [00-static-validation.md](./00-static-validation.md) |
| 1 | 클러스터 프로비저닝 (E2E) | 생성함 | 수십 분~수 시간 | `scripts/up.sh` — bare `vagrant up`은 Phase 2 미실행 |
| 2 | 기능 스모크 테스트 | ✓ | 수 분 | `scripts/test/verify-cluster.sh`, `test-sso.sh`, `verify-backup.sh` |
| 3 | 부하 테스트 (k6) | ✓ | 수십 분 | [`tests/k6/README.md`](../../tests/k6/README.md) |
| 4 | 카오스/복원력 테스트 | ✓ | 실험당 수 분 | [`tests/chaos/RUNBOOK.md`](../../tests/chaos/RUNBOOK.md) |
| 선택 | 에어갭 격리 검증 | ✓ | — | [`../common/airgap-isolation-testing.md`](../common/airgap-isolation-testing.md) |

## 단계별 요약

- **0단계 — 정적 검증**: VM 없이 리포만으로 실행. CI(`.github/workflows/lint.yml`)와
  동일한 검사를 로컬에서 재현한다. 커밋/배포 전 항상 먼저 실행.
- **1단계 — 프로비저닝**: `PROVIDER=<provider> ./scripts/up.sh`. 실패 VM만 개별
  재시도하는 resilient 래퍼로, 프로비저닝 스크립트는 멱등이라 재실행하면 수렴한다.
- **2단계 — 스모크**: 프로비저닝 직후 `verify-cluster.sh`(노드/VIP/etcd/CNI/DB/
  APISIX/TLS/DNS 종합), `test-sso.sh`(OIDC 플로우), `verify-backup.sh`(Velero).
- **3단계 — 부하(k6)**: `tests/k6/run-k6.sh all`. 실행 전 `preflight-host.sh`가
  호스트 리소스를 게이트한다. 3회 이상 실행해 p95/실패율 기준선을 기록.
- **4단계 — 카오스**: `tests/chaos/run-chaos.sh <experiment>`. RUNBOOK의 가설과
  판정 기준을 따른다. OpenBao 실험 후에는 수동 unseal이 필요하다.
