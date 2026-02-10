# Add Mistake Pattern - 실수 패턴 수동 추가

발견된 실수 패턴을 `.claude/cache/mistake-candidates.jsonl`에 기록합니다.

## 입력 정보

다음 정보를 입력받아 기록합니다:

1. **파일 경로**: 실수가 발생한 파일
2. **실수 유형**:
   - `repeated_edit`: 반복 수정
   - `version_mismatch`: 버전 불일치
   - `missing_sync`: 동기화 누락
   - `syntax_error`: 문법 오류
   - `other`: 기타
3. **설명**: 실수 내용 간략 설명
4. **해결책**: 어떻게 해결했는지

## 기록 형식

```json
{
  "timestamp": "2024-01-01T00:00:00Z",
  "file": "scripts/master/05-keycloak.sh",
  "type": "version_mismatch",
  "description": "VERSIONS.md와 스크립트 버전 불일치",
  "resolution": "스크립트 버전을 VERSIONS.md에 맞춰 수정"
}
```

## 사용 예시

```
/add-mistake scripts/master/05-keycloak.sh version_mismatch "VERSIONS.md와 버전 불일치" "스크립트 수정"
```
