---
name: add-mistake
description: 실수 패턴을 CLAUDE.md Mistakes Log에 기록
disable-model-invocation: true
---

# Add Mistake Pattern - 실수 패턴 수동 추가

발견된 실수 패턴을 CLAUDE.md의 Mistakes Log 테이블에 추가합니다.

## 입력 정보

$ARGUMENTS 에서 다음 정보를 추출하여 기록:

1. **날짜**: 오늘 날짜 (YYYY-MM-DD)
2. **실수 내용**: 무엇이 잘못되었는지
3. **해결책**: 어떻게 해결/방지하는지

## 기록 위치

CLAUDE.md의 해당 카테고리 테이블에 추가:
- Shell Script 실수
- Kubernetes/Helm 실수
- GitOps/ArgoCD 실수
- Vagrant/Infrastructure 실수

## 형식

```markdown
| YYYY-MM-DD | 실수 내용 | 해결책 |
```
