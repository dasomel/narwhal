---
name: check
description: 빠른 문법 검사 - Vagrantfile, 스크립트, YAML 검증
---

# Quick Check - 프로젝트 상태 검증

프로젝트의 주요 설정과 스크립트를 빠르게 검증합니다.

## 수행할 작업

1. **Vagrantfile 문법 검증**
   ```bash
   ruby -c Vagrantfile
   ```

2. **쉘 스크립트 검증** (shellcheck 설치 시)
   ```bash
   shellcheck scripts/**/*.sh 2>/dev/null || echo "shellcheck not installed"
   ```

3. **YAML 문법 검증**
   ```bash
   for f in gitops/apps/*.yaml gitops/resources/*.yaml; do
     yq eval '.' "$f" > /dev/null && echo "OK: $f" || echo "FAIL: $f"
   done
   ```

4. **버전 일관성 확인**
   - VERSIONS.md의 버전과 스크립트 버전 비교
   - gitops/apps/*.yaml의 차트 버전 확인

5. **결과 요약 출력**

## 사용법

이 명령어를 실행하면 위 검증을 순서대로 수행하고 결과를 요약합니다.
