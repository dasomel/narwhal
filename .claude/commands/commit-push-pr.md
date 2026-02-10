# Commit-Push-PR - 한 번에 커밋, 푸시, PR 생성

변경사항을 커밋하고, 푸시하고, PR을 생성합니다.

## 사전 조건

- 변경사항이 있어야 함
- 현재 브랜치가 main/master가 아니어야 함 (PR 생성 시)

## 실행 단계

### 1. 변경사항 확인
```bash
git status --short
git diff --stat
```

### 2. 검증 실행
```bash
# 문법 검사
ruby -c Vagrantfile 2>/dev/null || true
shellcheck scripts/**/*.sh 2>/dev/null || true
```

### 3. 커밋
```bash
# 변경된 파일 스테이징
git add -A

# 커밋 메시지 생성 (변경 내용 기반)
# 형식: type(scope): description
# 예: feat(keycloak): add OIDC client for Harbor
```

### 4. 푸시
```bash
git push -u origin HEAD
```

### 5. PR 생성 (선택)
```bash
gh pr create --title "PR 제목" --body "설명"
```

## 커밋 메시지 컨벤션

| Type | 설명 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| docs | 문서 변경 |
| refactor | 리팩토링 |
| chore | 빌드/설정 변경 |

## 예시

```bash
# 기능 추가
feat(gitops): add Velero backup application

# 버그 수정
fix(keycloak): correct OIDC redirect URI

# 문서 업데이트
docs(claude): add mistake pattern for CRD dependency
```

## 사용법

변경사항을 분석하여 적절한 커밋 메시지를 생성하고,
푸시 후 PR 생성 여부를 확인합니다.
