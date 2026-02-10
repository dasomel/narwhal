# Sync Versions - 버전 동기화 검사

VERSIONS.md의 버전과 실제 스크립트/매니페스트 버전을 비교하고 동기화합니다.

## 검사 대상

1. **스크립트 파일**
   - `scripts/master/*.sh` 내 VERSION 변수
   - Helm 차트 버전

2. **GitOps 매니페스트**
   - `gitops/apps/*.yaml` 내 targetRevision
   - `gitops/values/*.yaml` 내 image tag

## 수행 작업

1. VERSIONS.md 파싱하여 버전 목록 추출
2. 각 스크립트/매니페스트에서 버전 추출
3. 불일치 항목 리포트
4. (선택) 자동 업데이트 제안

## 출력 예시

```
=== Version Sync Report ===

[OK] Cilium: v1.18.6 (VERSIONS.md) = v1.18.6 (scripts/master/02-cni-install.sh)
[MISMATCH] cert-manager: v1.19.2 (VERSIONS.md) != v1.19.0 (gitops/apps/cert-manager.yaml)
[OK] Prometheus: v0.88.0 (VERSIONS.md) = v0.88.0 (gitops/apps/prometheus-stack.yaml)

Found 1 mismatch(es). Fix? [y/n]
```
