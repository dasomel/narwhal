---
name: verify
description: 전체 검증 루프 - Vagrantfile, 스크립트, YAML 문법 검사 및 클러스터 상태 확인
---

# Verify - 전체 검증 루프 실행

작업 결과를 검증하여 품질을 보장합니다.

## 검증 단계

### 1. 문법 검증
```bash
# Vagrantfile
ruby -c Vagrantfile

# Shell scripts
shellcheck scripts/**/*.sh 2>/dev/null || echo "shellcheck not installed"

# YAML files
for f in gitops/apps/*.yaml gitops/resources/*.yaml; do
  yq eval '.' "$f" > /dev/null 2>&1 && echo "OK: $f" || echo "FAIL: $f"
done
```

### 2. Git 상태 확인
```bash
git status --short
git diff --stat
```

### 3. VM 실행 중이면 클러스터 상태 확인
```bash
# 노드 상태
vagrant ssh master-1 -c "kubectl get nodes" 2>/dev/null || echo "VM not running"

# Pod 상태
vagrant ssh master-1 -c "kubectl get pods -A --field-selector=status.phase!=Running 2>/dev/null | head -20" || true

# ArgoCD 앱 상태
vagrant ssh master-1 -c "kubectl get applications -n argocd 2>/dev/null" || true
```

### 4. 버전 일관성 확인
- VERSIONS.md와 스크립트 버전 비교
- gitops/apps/*.yaml의 차트 버전 확인

## 출력 형식

```
=== Verification Report ===
[OK] Vagrantfile syntax
[OK] scripts/cluster/02-init-cluster.sh
[WARN] scripts/cluster/11-keycloak.sh - shellcheck warnings
[OK] gitops/apps/cert-manager.yaml
[FAIL] gitops/apps/harbor.yaml - YAML syntax error

Cluster Status: 3/3 nodes Ready
ArgoCD Apps: 8 Synced, 1 Progressing
===========================
```

## 사용법

이 명령어 실행 시 위 검증을 순서대로 수행하고 결과를 요약합니다.
실패 항목이 있으면 수정 방법을 제안합니다.
