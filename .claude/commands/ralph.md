---
name: ralph
description: Ralph 자율 개발 루프 - PROMPT.md 기반 무한 반복 실행
disable-model-invocation: true
---

# Ralph - 자율 개발 루프 실행

> "Ralph is a Bash loop" - Geoffrey Huntley
> https://ghuntley.com/ralph/

PROMPT.md 파일을 기반으로 Claude를 무한 루프에서 실행하여 자율적으로 작업을 수행합니다.

## 사용법

### 1. PROMPT.md 생성

템플릿 복사:
```bash
cp .claude/templates/PROMPT.md ./PROMPT.md
```

### 2. PROMPT.md 수정

목표, 작업 목록, 완료 조건을 명확히 정의

### 3. Ralph 실행

```bash
.claude/scripts/ralph.sh                          # 기본 (무제한)
.claude/scripts/ralph.sh --max-iterations 10      # 최대 10회
.claude/scripts/ralph.sh --safe                    # 권한 확인 포함
.claude/scripts/ralph.sh --dry-run                 # 프롬프트만 확인
```

## 주의사항

1. PROMPT.md에 작업 범위를 명확히 제한
2. 완료 조건 필수 (무한 루프 방지)
3. 정기적으로 `git diff` 확인
4. 중요 작업 전 git commit

## 실행 중단

`Ctrl+C`로 언제든 중단 가능
