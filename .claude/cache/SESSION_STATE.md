# Session State - 2026-02-20

## 목표
- 프로젝트 구조 정리, 테스트 안정화, v0.1.0 태그 릴리스

## 기술 환경
- macOS (darwin), Vagrant + VMware Desktop
- Kubernetes v1.35, 3m+3w topology (master 4GB NoSchedule, worker 6GB)
- 2-Phase provisioning: Phase 1 (00-05), Phase 2 (07-14), wrapper 06-phase2-start.sh
- License: Apache 2.0

## 완료된 작업
- [x] test-sso.sh: JWT base64url 디코딩 수정 (`decode_jwt_payload()`), Harbor API를 HTTPS 엔드포인트로 변경 (Istio ambient 호환)
- [x] verify-cluster.sh: `|| echo "0"` → `|| true` 전역 교체 (pipefail 충돌 해결, 48개), JWT 디코딩 수정, Mac/VM 감지 (`IS_CLUSTER_NODE`), OpenBao unseal 예외 처리
- [x] docs/ 파일명 lowercase kebab-case 변환 (7개 파일)
- [x] Phase 2 스크립트 리넘버링: phase2-platform.sh → 06-phase2-start.sh, 06-13 → 07-14
- [x] 빈 디렉토리 삭제: addons/, configs/, gitops/values/
- [x] License MIT → Apache 2.0 변경
- [x] 모든 md 파일 검토 및 수정 (경로, 네임스페이스, 토폴로지, 삭제된 디렉토리 참조)
- [x] CLAUDE.md에 Context Management 섹션 추가
- [x] `/compact` 슬래시 커맨드 생성
- [x] README.md에 release/license 배지 추가
- [x] v0.1.0 태그 생성, GitHub repo 생성, push, release 생성

## 변경된 파일
- `README.md`: 배지 추가, docs 링크 lowercase, verify-cluster.sh 경로 수정
- `CLAUDE.md`: Context Management 섹션, /compact 커맨드 추가, gitops/values 참조 제거, 스크립트 번호 업데이트
- `LICENSE`: MIT → Apache 2.0
- `Vagrantfile`: phase2-platform.sh → 06-phase2-start.sh
- `docs/*.md`: 7개 파일 lowercase 리네임 + 내용 수정 (경로, namespace cnpg→database)
- `scripts/cluster/`: 9개 스크립트 리넘버링 (phase2→06, 06→07, ..., 13→14)
- `scripts/test/verify-cluster.sh`: pipefail 수정, JWT 디코딩, Mac/VM 감지, OpenBao 예외
- `scripts/test/test-sso.sh`: JWT 디코딩, Harbor HTTPS 엔드포인트
- `.claude/commands/compact.md`: 신규 생성

## 미완료 작업
- (없음 - 모든 요청 작업 완료)

## 현재 상태
- v0.1.0 릴리스 완료: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
- main 브랜치 clean (커밋되지 않은 변경 없음)
- 다음 작업 대기 중
